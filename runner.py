"""mc run <name> [--project P] -- <cmd...>
mc run --on <server> [--container C] <name> [--project P] -- <cmd...>

Starts <cmd>, tees stdout+stderr to the terminal (unchanged) and to logs/<run-id>.log (tqdm's \\r turned into
newlines), and registers the run in data/mc.db. A single <cmd> argument runs through the shell, so pipes work:
    mc run sweep --project myproj -- 'python a.py | tee x'
With --on, the job starts detached (setsid nohup) on a home server from projects.yaml that has allow_launch: true,
inside container C if given, and logs to <first run root>/mc-logs/<run-id>.log there. mc returns right away.
"""
import os
import re
import shlex
import signal
import subprocess
import sys
import time

import db


def split_cr(data: bytes, prev_end: bytes):
    """tqdm-style \\r redraws -> one line each. prev_end: last raw byte of the previous chunk (start with b"\\n").
    \\r\\n -> \\n; a \\r at the start of a line draws nothing new, so it's dropped instead of making a blank line."""
    if prev_end == b"\r" and data.startswith(b"\n"):
        data = data[1:]
    end = data[-1:] or prev_end
    data = re.sub(rb"(?<=[\n\r])\r+", b"", data.replace(b"\r\n", b"\n"))
    if prev_end in (b"\n", b"\r"):
        data = data.lstrip(b"\r")
    return data.replace(b"\r", b"\n"), end


def main(argv):
    if len(argv) < 3 or argv[0] != "run" or "--" not in argv:
        sys.exit(__doc__)
    i = argv.index("--")
    opts, cmd, flags, pos = iter(argv[1:i]), argv[i + 1:], {}, []
    for o in opts:
        if o in ("--project", "--on", "--container"):
            flags[o[2:]] = next(opts, None)
        else:
            pos.append(o)
    if len(pos) != 1 or None in flags.values():
        sys.exit(__doc__)
    name, project = pos[0], flags.get("project")
    if not cmd:
        sys.exit("no command after --")

    run_id = time.strftime("%Y%m%d-%H%M%S-") + re.sub(r"[^\w.-]", "_", name)
    if "on" in flags:
        return launch_remote(run_id, name, project, flags["on"], flags.get("container"), cmd)
    db.LOGS.mkdir(exist_ok=True)
    log_path = db.LOGS / f"{run_id}.log"
    shell = len(cmd) == 1
    env = {**os.environ, "PYTHONUNBUFFERED": "1"}  # otherwise piped Python children block-buffer for minutes
    # Like nohup: an ignored SIGHUP is inherited by the child, so closing the terminal doesn't kill the job.
    signal.signal(signal.SIGHUP, signal.SIG_IGN)
    p = subprocess.Popen(cmd[0] if shell else cmd, shell=shell, stdout=subprocess.PIPE,
                         stderr=subprocess.STDOUT, env=env, cwd=os.getcwd())
    # Ctrl-C reaches the child via the terminal's process group; we stay alive to record its exit.
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    signal.signal(signal.SIGTERM, lambda *_: p.terminate())

    con = db.connect()
    cmd_str = cmd[0] if shell else subprocess.list2cmdline(cmd)
    db.upsert(con, id=run_id, source="local", project=project, name=name, status="running",
              started=time.time(), pid=p.pid, cmd=cmd_str, device="mac", log=str(log_path),
              meta={"cwd": os.getcwd(), "runner_pid": os.getpid()})
    print(f"[mc] run {run_id}  log {log_path}", file=sys.stderr)

    prev = b"\n"
    with open(log_path, "wb") as log:
        fd = p.stdout.fileno()
        while chunk := os.read(fd, 65536):
            try:
                sys.stdout.buffer.write(chunk)
                sys.stdout.buffer.flush()
            except OSError:  # terminal went away; keep logging
                pass
            out, prev = split_cr(chunk, prev)
            log.write(out)
            log.flush()
    code = p.wait()
    status = "done" if code == 0 else "killed" if code < 0 else "failed"
    db.upsert(con, id=run_id, status=status, ended=time.time(), exit_code=code)
    print(f"[mc] {run_id} {status} (exit {code})", file=sys.stderr)
    sys.exit(code if code >= 0 else 128 - code)


def launch_remote(run_id, name, project, on, container, cmd):
    import yaml
    from collectors import servers
    cfg = db.ROOT / "projects.yaml"
    srv = servers.load(yaml.safe_load(cfg.read_text()) if cfg.exists() else {}).get(on)
    if not srv:
        sys.exit(f"[mc] no server {on!r} under servers: in {cfg}")
    if srv["allow_launch"] is not True:
        sys.exit(f"[mc] server {on} is read-only; set allow_launch: true on it in {cfg} to launch jobs there")
    container = container or srv["container"]
    if container and not servers.NAME.match(container):
        sys.exit(f"[mc] bad container name {container!r}")
    cmd_str = cmd[0] if len(cmd) == 1 else shlex.join(cmd)
    p = subprocess.run(servers.ssh_cmd(srv, servers.launch_script(srv, run_id, cmd_str, container)),
                       stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=60)
    if p.returncode:
        err = p.stderr.strip()
        hint = servers.hint(srv, err)
        sys.exit(f"[mc] launch on {on} failed: {err}" + (f"\n[mc] no key on the server? run: {hint}" if hint else ""))
    pid, path = p.stdout.strip().split("\t")
    db.upsert(db.connect(), id=run_id, source="server", project=project, name=name, status="running",
              started=time.time(), pid=int(pid), cmd=cmd_str, device=on + (f" · {container}" if container else ""),
              dph=srv["cost_per_hour"], meta={"server": on, "container": container, "path": path, "launched": True})
    print(f"[mc] run {run_id} on {on}{f' in {container}' if container else ''}  pid {pid}  log {path}", file=sys.stderr)


if __name__ == "__main__":
    main(sys.argv[1:])
