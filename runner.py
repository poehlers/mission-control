"""mc run <name> [--project P] -- <cmd...>

Starts <cmd>, tees stdout+stderr to the terminal (unchanged) and to logs/<run-id>.log (tqdm's \\r turned into
newlines), and registers the run in data/mc.db. A single <cmd> argument runs through the shell, so pipes work:
    mc run sweep --project myproj -- 'python a.py | tee x'
"""
import os
import re
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
    opts, cmd = argv[1:i], argv[i + 1:]
    name, project = opts[0], None
    if "--project" in opts:
        project = opts[opts.index("--project") + 1]
    if not cmd:
        sys.exit("no command after --")

    run_id = time.strftime("%Y%m%d-%H%M%S-") + re.sub(r"[^\w.-]", "_", name)
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


if __name__ == "__main__":
    main(sys.argv[1:])
