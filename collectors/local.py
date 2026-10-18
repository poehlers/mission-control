"""Local host stats via psutil (built on macOS; Linux shows its real mounts). No GPU/temps without sudo, so they're simply absent."""
import sys
import time

import psutil

_last_net = None


def _disks():
    out = []
    for p in psutil.disk_partitions():
        # APFS: "/" is the sealed system snapshot; real usage lives on the Data volume
        mac = sys.platform == "darwin"
        if (p.mountpoint == "/System/Volumes/Data" or p.mountpoint.startswith("/Volumes/")) if mac \
                else not p.mountpoint.startswith(("/boot", "/snap")):
            try:
                u = psutil.disk_usage(p.mountpoint)
            except OSError:  # volume ejected between listing and stat
                continue
            out.append({"mount": "Macintosh HD" if p.mountpoint.startswith("/System") else p.mountpoint, "used": u.used, "total": u.total, "pct": u.percent})
    return out


def top_processes(n=25):
    procs = []
    # process_iter caches Process objects, so cpu_percent is a real delta from the previous call
    for p in psutil.process_iter(["pid", "name", "cpu_percent", "memory_info", "username"]):
        i = p.info
        if i["memory_info"] is None:
            continue
        procs.append({"pid": i["pid"], "name": i["name"], "cpu": i["cpu_percent"] or 0.0,
                      "rss": i["memory_info"].rss, "user": i["username"]})
    procs.sort(key=lambda x: (x["cpu"], x["rss"]), reverse=True)
    top = procs[:n]
    for t in top:  # cmdline is the expensive bit on macOS, so only for the shown ones
        try:
            t["cmd"] = " ".join(psutil.Process(t["pid"]).cmdline())[:300]
        except (psutil.Error, OSError):
            t["cmd"] = ""
    return top


def sample():
    global _last_net
    now = time.time()
    vm, sw = psutil.virtual_memory(), psutil.swap_memory()
    net = psutil.net_io_counters()
    rate = None
    if _last_net:
        dt = now - _last_net[0]
        rate = {"rx": (net.bytes_recv - _last_net[1].bytes_recv) / dt,
                "tx": (net.bytes_sent - _last_net[1].bytes_sent) / dt}
    _last_net = (now, net)
    bat = psutil.sensors_battery()
    return {
        "t": now,
        "cpu": psutil.cpu_percent(percpu=True),
        "load": psutil.getloadavg(),
        "mem": {"used": vm.total - vm.available, "total": vm.total, "pct": vm.percent},
        "swap": {"used": sw.used, "total": sw.total, "pct": sw.percent},
        "disks": _disks(),
        "net": rate,
        "battery": bat and {"pct": bat.percent, "plugged": bat.power_plugged,
                            "secsleft": bat.secsleft if bat.secsleft >= 0 else None},
        "procs": top_processes(),
    }
