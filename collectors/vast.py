"""Vast.ai: instance list via the vastai SDK (key stays in ~/.config/vastai), logs + metrics over ssh."""
import asyncio
import re
import shlex

from runner import split_cr

# ssh target per instance id, kept server-side only (never sent to the browser).
ssh_targets = {}


def fetch():
    from vastai.sdk import VastAI  # lazy: ~0.2 s import, only paid by the poller thread
    return VastAI().show_instances()


def ssh_target(inst):
    """Same choice as `vastai ssh-url`: direct port mapping if exposed, else the vast ssh proxy."""
    direct = (inst.get("ports") or {}).get("22/tcp")
    if direct and inst.get("public_ipaddr"):
        return inst["public_ipaddr"], int(direct[0]["HostPort"])
    return inst["ssh_host"], int(inst["ssh_port"])


def to_run(inst, now, projects=()):
    """Instance JSON -> runs row. Only whitelisted fields leave this function (raw JSON carries jupyter_token)."""
    running = inst["actual_status"] == "running"
    started = inst.get("start_date")
    # Vast bills GPU $/h while running and storage $/h always; accrued is an estimate from uptime.
    dph = inst["dph_total"] if running else inst.get("storage_total_cost") or 0.0
    label = inst.get("label") or ""
    return {
        "id": f"vast-{inst['id']}", "source": "vast", "name": label or f"instance {inst['id']}",
        "project": next((p for p in projects if label.startswith(p)), None),
        "status": inst["actual_status"] or inst.get("cur_state") or "unknown",
        "started": started, "ended": None,
        "device": f"{inst.get('num_gpus') or 1}× {inst.get('gpu_name')}",
        "dph": dph, "cost": inst["dph_total"] * (now - started) / 3600 if running and started else None,
        "meta": {k: inst.get(k) for k in ("id", "gpu_util", "gpu_temp", "cpu_util", "cpu_cores_effective",
                                          "mem_usage", "mem_limit", "disk_usage", "disk_space", "gpu_ram",
                                          "geolocation", "image_uuid", "status_msg", "intended_status")},
    }


SSH_OPTS = ["-o", "BatchMode=yes", "-o", "ConnectTimeout=10", "-o", "ServerAliveInterval=30",
            "-o", "StrictHostKeyChecking=accept-new", "-o", "LogLevel=ERROR",
            # one TCP+auth handshake per box, reused by the log tails and the 5 s metric polls
            "-o", "ControlMaster=auto", "-o", "ControlPath=/tmp/mc-ssh-%C", "-o", "ControlPersist=120"]


def ssh_cmd(iid, remote):
    host, port = ssh_targets[iid]
    return ["ssh", *SSH_OPTS, "-p", str(port), f"root@{host}", remote]


async def ssh_run(iid, remote, timeout=20):
    return await run(ssh_cmd(iid, remote), timeout)


async def run(argv, timeout=20):
    p = await asyncio.create_subprocess_exec(*argv, stdin=asyncio.subprocess.DEVNULL,
                                             stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        out, err = await asyncio.wait_for(p.communicate(), timeout)
    except TimeoutError:
        p.kill()
        raise RuntimeError("ssh timed out")
    if p.returncode != 0:
        raise RuntimeError(err.decode(errors="replace").strip() or f"ssh exit {p.returncode}")
    return out.decode(errors="replace")


METRICS = ("nvidia-smi --query-gpu=index,name,utilization.gpu,memory.used,memory.total,temperature.gpu,"
           "power.draw,power.limit --format=csv,noheader,nounits; echo @@; head -1 /proc/stat; echo @@; "
           "free -b | sed -n 2p; echo @@; df -B1 -P / | tail -1; echo @@; nproc")


def _num(s):
    try:
        return float(s)
    except ValueError:  # nvidia-smi prints "[N/A]" for unsupported fields
        return None


def parse_box(text, prev_cpu=None):
    """Output of METRICS -> dict. prev_cpu: (busy, total) jiffies from the previous sample, for a CPU % delta."""
    gpu, stat, mem, disk, nproc = (s.strip() for s in text.split("@@")[:5])
    gpus = []
    for line in gpu.splitlines():
        f = [x.strip() for x in line.split(",")]
        if len(f) == 8:
            gpus.append({"index": int(f[0]), "name": f[1], "util": _num(f[2]), "mem_used": _num(f[3]),
                         "mem_total": _num(f[4]), "temp": _num(f[5]), "power": _num(f[6]), "power_limit": _num(f[7])})
    j = [int(x) for x in stat.split()[1:]]
    total, idle = sum(j[:8]), j[3] + j[4]  # idle + iowait; guest time is already inside user
    cpu = None
    if prev_cpu and total > prev_cpu[1]:
        cpu = 100 * ((total - idle) - prev_cpu[0]) / (total - prev_cpu[1])
    m = mem.split()
    d = disk.split()
    return {"gpus": gpus, "cpu": cpu, "cpu_raw": (total - idle, total), "nproc": int(nproc),
            "mem": {"total": int(m[1]), "used": int(m[1]) - int(m[6])},  # total - available
            "disk": {"total": int(d[1]), "used": int(d[2])}}


def follow(iid, path, n=500):
    return tail(lambda n: ssh_cmd(iid, f"tail -n {int(n)} -F {shlex.quote(path)}"), n)


async def tail(argv, n=500):
    """argv(n): an ssh command that tails -F a remote file from n lines back. Reconnects every 10 s like
    vast/logs.sh. Yields lists of lines."""
    while True:
        p = await asyncio.create_subprocess_exec(
            *argv(n), stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
        rest, prev = b"", b"\n"
        try:
            while chunk := await p.stdout.read(65536):
                out, prev = split_cr(chunk, prev)
                *lines, rest = (rest + out).split(b"\n")
                if lines:
                    yield lines
        finally:
            if p.returncode is None:
                p.kill()
                await p.wait()
        yield [f"--- disconnected (ssh exit {p.returncode}), retrying in 10 s".encode()]
        await asyncio.sleep(10)
        n = 20  # like logs.sh: a little overlap after a reconnect beats silently missing lines


# Any log on the box, not just /root/queue.log: *.log/*.out/*.err under /root and /workspace (skipping dot-dirs,
# venvs, site-packages), plus Vast's own onstart log. Newest first, so the live one is on top.
def find_logs(roots, more=""):
    return (rf"find {roots} -maxdepth 5 \( -type d \( -name '.*' -o -name site-packages"
            r" -o -name node_modules -o -name __pycache__ -o -name wandb \) -prune \) -o -type f"
            rf" \( -name '*.log' -o -name '*.out' -o -name '*.err' \) -size +0 {more}-printf '%T@\t%s\t%p\n' 2>/dev/null")


FIND_LOGS = find_logs("/root /workspace /var/log/onstart.log") + " | sort -rn | head -60; true"
LOG_PATH = re.compile(r"^/[^\x00-\x1f]+$")  # any absolute path; shlex.quote makes it shell-safe


def parse_logs(out):
    """find -printf '%T@\\t%s\\t%p' lines -> [{path, mtime, size}] (already newest first)."""
    logs = []
    for ln in out.splitlines():
        parts = ln.split("\t", 2)
        if len(parts) == 3 and LOG_PATH.match(parts[2]):
            logs.append({"path": parts[2], "mtime": float(parts[0]), "size": int(parts[1])})
    return logs
