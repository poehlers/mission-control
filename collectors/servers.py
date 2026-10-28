"""Home servers: your own always-on machines (LAN boxes, workstations, NAS) over ssh, like Vast boxes.

Key auth through your ssh agent only (BatchMode, never a password). Read-only unless a server has
`allow_launch: true`, and then the only write is `mc run --on`'s log under the server's first run root.
Remote side assumes Linux (/proc) and GNU find.
"""
import hashlib
import json
import re
import shlex

from collectors import vast

SAFE = re.compile(r"^[\w.@:%\[\]-]+$")  # host/user/jump: no spaces or quotes, and can't start like an ssh option
NAME = re.compile(r"^[\w][\w.-]*$")  # server ids, container names
DEFAULTS = {"port": None, "user": None, "jump": None, "container": None, "owner": None, "cost_per_hour": 0.0,
            "roots": ["~", "/root"], "allow_launch": False}
ACTIVE = 300  # a found log written to in the last 5 min counts as a running job
ALIVE = "@@alive@@"


def load(cfg):
    """`servers:` list from projects.yaml -> {id: server}. Raises on a value that could smuggle ssh options."""
    out = {}
    for s in (cfg or {}).get("servers") or []:
        s = {**DEFAULTS, **s}
        if not NAME.match(str(s.get("id", ""))) or not s.get("host"):
            raise ValueError(f"servers: each entry needs an id ([A-Za-z0-9_.-]) and a host: {s}")
        for k in ("host", "user", "jump"):
            if s[k] is not None and not (SAFE.match(str(s[k])) and not str(s[k]).startswith("-")):
                raise ValueError(f"server {s['id']}: bad {k} {s[k]!r}")
        if s["container"] is not None and not NAME.match(s["container"]):
            raise ValueError(f"server {s['id']}: bad container {s['container']!r}")
        out[s["id"]] = s
    return out


def dest(s):
    return f"{s['user']}@{s['host']}" if s["user"] else s["host"]


def ssh_cmd(s, script):
    """Script runs under sh, whatever the login shell is. ConnectTimeout first: ssh keeps the first value."""
    return ["ssh", "-o", "ConnectTimeout=5", *vast.SSH_OPTS, *(["-p", str(int(s["port"]))] if s["port"] else []),
            *(["-J", s["jump"]] if s["jump"] else []), dest(s), shlex.join(["sh", "-c", script])]


def run(s, script, timeout=20):
    return vast.run(ssh_cmd(s, script), timeout)


def in_container(c, script):
    return shlex.join(["docker", "exec", c, "sh", "-c", script]) if c else script


def hint(s, err):
    """No key on the server -> the exact command that installs it. Never a password prompt from here."""
    if "Permission denied" not in err and "publickey" not in err:
        return None
    return shlex.join(["ssh-copy-id", *(["-p", str(s["port"])] if s["port"] else []),
                       *(["-o", f"ProxyJump={s['jump']}"] if s["jump"] else []), dest(s)])


# vast.METRICS works as is (no nvidia-smi = empty GPU section), plus load and running containers.
METRICS = vast.METRICS + "; echo @@; cat /proc/loadavg; echo @@; docker ps --format '{{json .}}' 2>/dev/null; true"


def parse_metrics(text, prev_cpu=None):
    parts = text.split("@@")
    x = vast.parse_box("@@".join(parts[:5]), prev_cpu)
    x["load"] = [float(v) for v in parts[5].split()[:3]]
    cs = [json.loads(ln) for ln in parts[6].splitlines() if ln.startswith("{")]
    x["containers"] = [{"name": c.get("Names"), "image": c.get("Image"), "status": c.get("Status")} for c in cs]
    return x


def _root(r):
    return '"$HOME"' if r == "~" else shlex.quote(r)


def discover_script(s, launched=()):
    """Logs from the last 7 days under the run roots and each running container's /root, then the state of
    each launched run still marked running. Output: `container\\tmtime\\tsize\\tpath` lines, @@, `id\\tstate`."""
    week = "-mmin -10080 "
    host = vast.find_logs(" ".join(map(_root, s["roots"])), week) + " | head -200"
    box = vast.find_logs("/root", week) + " | head -200"
    tag = shlex.quote('{print c "\\t" $0}')
    checks = []
    for r in launched:
        m, path = r["meta"], shlex.quote(r["meta"]["path"])
        state = in_container(m.get("container"), f'if [ "$(readlink /proc/{int(r["pid"])}/fd/1)" = {path} ]; '
                                                 f'then echo {ALIVE}; else tail -n 1 {path}; fi')
        checks.append(f"printf '%s\\t%s\\n' {shlex.quote(r['id'])} \"$({state} 2>/dev/null)\"")
    return (f"{host} | awk -v c= {tag}; "
            f"for c in $(docker ps --format '{{{{.Names}}}}' 2>/dev/null); do "
            f"docker exec \"$c\" sh -c {shlex.quote(box)} 2>/dev/null | awk -v c=\"$c\" {tag}; done; "
            f"echo @@; {'; '.join(checks)}{'; ' if checks else ''}true")


def parse_discover(out):
    """-> (logs newest first [{container, path, mtime, size}], {run_id: state line})."""
    found, _, checks = ("\n" + out).partition("\n@@\n")
    logs = []
    for ln in found.splitlines():
        c, _, rest = ln.partition("\t")
        if c and not NAME.match(c):
            continue
        logs += [{**x, "container": c or None} for x in vast.parse_logs(rest)]
    # A container's bind mount shows the host's file twice; keep the container's copy, it says where the job runs.
    boxed = {(x["path"].rsplit("/", 1)[-1], x["mtime"], x["size"]) for x in logs if x["container"]}
    logs = [x for x in logs if x["container"] or (x["path"].rsplit("/", 1)[-1], x["mtime"], x["size"]) not in boxed]
    states = dict(ln.split("\t", 1) for ln in checks.splitlines() if "\t" in ln)
    return sorted(logs, key=lambda x: -x["mtime"]), states


def project_of(projects, labels):
    """Same rule as Vast labels: `qcc-…` belongs to project qcc. Here the label is the container name or any
    directory/file name in the log path."""
    return next((p for p in projects for x in labels if re.match(rf"{re.escape(p)}(?:[-_.]|$)", x)), None)


def found_row(s, log, now, projects=()):
    c, path = log["container"], log["path"]
    active = now - log["mtime"] < ACTIVE
    return {"id": f"srv-{s['id']}-{hashlib.sha1(f'{c or ''}:{path}'.encode()).hexdigest()[:10]}",
            "source": "server", "name": "/".join(path.split("/")[-2:]),
            "project": project_of(projects, [c or "", *path.split("/")]),
            "status": "running" if active else "quiet", "started": None, "ended": None if active else log["mtime"],
            "device": s["id"] + (f" · {c}" if c else ""), "dph": s["cost_per_hour"], "cost": None,
            "meta": {"server": s["id"], "container": c, "path": path, "mtime": log["mtime"], "size": log["size"]}}


def settle(r, state, now):
    """A launched run's state line -> row update, or None while it's still running."""
    if state == ALIVE:
        return None
    m = re.fullmatch(r"\[mc\] exit (\d+)", state.strip())
    upd = {"id": r["id"], "ended": now}
    if r["started"] and r["dph"]:
        upd["cost"] = r["dph"] * (now - r["started"]) / 3600
    if m:
        return {**upd, "status": "done" if m[1] == "0" else "failed", "exit_code": int(m[1])}
    return {**upd, "status": "killed" if r["meta"].get("kill_t") else "lost"}


def launch_script(s, run_id, cmd, container=None):
    """nohup + setsid so the job outlives ssh and gets its own process group (what Kill signals). The log goes to
    <first run root>/mc-logs (/root/mc-logs inside a container), where discovery and the runs table find it.
    The wrapper appends `[mc] exit N`, which is how the poller learns the exit code. Prints `pid\\tlog`."""
    d = "/root/mc-logs" if container else _root(s["roots"][0].rstrip("/") or "/") + "/mc-logs"
    body = shlex.quote(f'(\n{cmd}\n)\necho "[mc] exit $?"')  # subshell: an `exit` in cmd still gets its trailer
    script = (f"set -e; mkdir -p {d}; L=$(cd {d} && pwd -P)/{run_id}.log; cd; "
              f"PYTHONUNBUFFERED=1 setsid nohup sh -c {body} > \"$L\" 2>&1 < /dev/null & "
              f"printf '%s\\t%s\\n' \"$!\" \"$L\"")
    return in_container(container, script)


def kill_script(pid, meta):
    """SIGTERM to the run's process group, only if that pid still writes to the run's log (pid reuse guard)."""
    path = shlex.quote(meta["path"])
    return in_container(meta.get("container"), f'[ "$(readlink /proc/{int(pid)}/fd/1)" = {path} ] '
                                                 f'&& kill -TERM -{int(pid)} || {{ echo "process gone or pid reused" >&2; exit 1; }}')


def follow(s, container, path, n=500):
    return vast.tail(lambda n: ssh_cmd(s, in_container(container, f"tail -n {int(n)} -F {shlex.quote(path)}")), n)
