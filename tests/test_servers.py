"""Home-server parsers against real output from a LAN box (username redacted): metrics, log discovery, run settling."""
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent))
from collectors import servers

S = servers.load({"servers": [{"id": "homelab", "host": "192.168.1.20", "user": "user"}]})["homelab"]
assert S["allow_launch"] is False and S["cost_per_hour"] == 0 and S["roots"] == ["~", "/root"]  # read-only by default
for bad in ({"host": "-oProxyCommand=x"}, {"host": "a b"}, {"user": "-l"}, {"jump": "x;y"}, {"container": "a b"}):
    try:
        servers.load({"servers": [{"id": "x", "host": "h", **bad}]})
        raise AssertionError(f"accepted {bad}")
    except ValueError:
        pass

m = servers.parse_metrics((HERE / "samples/server_box.txt").read_text())
assert m["gpus"] == [] and m["nproc"] == 12 and m["load"] == [0.99, 0.67, 0.33]  # no nvidia-smi: no GPU section
assert m["mem"] == {"total": 15990259712, "used": 15990259712 - 15067803648}
assert m["containers"] == [{"name": "qcc-lab", "image": "qcc-lab", "status": "Up About a minute"}]

logs, states = servers.parse_discover((HERE / "samples/server_discover.txt").read_text())
assert [x["mtime"] for x in logs] == sorted((x["mtime"] for x in logs), reverse=True)
# ~/qcc is bind-mounted at /root/qcc in the container: each job once, as the container's copy
assert len(logs) == 10 and not any(x["path"].startswith("/home/user/qcc/") for x in logs), logs
assert logs[0] == {"path": "/home/user/mc-logs/20261028-103442-mc-selftest.log", "mtime": 1793180097.446050298,
                   "size": 71, "container": None}

k5 = next(x for x in logs if x["path"].endswith("k5.log"))
row = servers.found_row(S, k5, k5["mtime"] + 60, projects=["mims", "qcc"])
assert row["project"] == "qcc" and row["status"] == "running" and row["device"] == "homelab · qcc-lab"
assert row["name"] == "logs/gf12_lns_k5.log" and row["id"].startswith("srv-homelab-") and row["dph"] == 0
quiet = servers.found_row(S, k5, k5["mtime"] + 3600, projects=["q"])
assert quiet["status"] == "quiet" and quiet["ended"] == k5["mtime"] and quiet["project"] is None  # "q" ≠ "qcc-lab"
assert servers.project_of(["qcc"], ["", "root", "qcc", "x.log"]) == "qcc"

r = {"id": "x", "started": 100.0, "dph": 0.0, "meta": {}}
assert servers.settle(r, states["20261028-103442-mc-killtest"], 200) is None
assert servers.settle(r, states["20261028-103442-mc-selftest"], 200) == {"id": "x", "ended": 200, "status": "done", "exit_code": 0}
assert servers.settle(r, states["20261028-103442-qcc-selftest"], 200)["status"] == "failed"
assert servers.settle(r, states["20261028-x-gone"], 200)["status"] == "lost"
assert servers.settle({**r, "meta": {"kill_t": 150}}, "", 200)["status"] == "killed"
assert servers.settle({**r, "dph": 0.36}, "[mc] exit 0", 200)["cost"] == 0.01

assert servers.hint(S, "user@192.168.1.20: Permission denied (publickey,password).") == "ssh-copy-id user@192.168.1.20"
assert servers.hint(S, "ssh: connect to host 192.168.1.20 port 22: Operation timed out") is None

# Every generated script must at least parse as sh, including a hostile command and launched-run checks.
launched = [{"id": "a'b", "pid": 7, "meta": {"path": "/root/mc-logs/x y.log", "container": "qcc-lab"}}]
cmd = """python -c 'print("it'"'"'s")' | tee "out file" # comment"""
for script in (servers.METRICS, servers.discover_script(S, launched), servers.launch_script(S, "r1", cmd),
               servers.launch_script(S, "r1", cmd, "qcc-lab"), servers.kill_script(7, launched[0]["meta"])):
    subprocess.run(["sh", "-n", "-c", script], check=True)
print("ok test_servers")
