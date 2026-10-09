"""Vast parsers against real samples: instance JSON -> run row, box metrics text -> numbers."""
import json
import sys
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent))
from collectors.vast import parse_box, ssh_target, to_run

insts = json.loads((HERE / "samples/vast_instances.json").read_text())
now = insts[0]["start_date"] + 7200  # two hours in

r = to_run(insts[0], now, projects=["mims"])
assert r["id"] == "vast-55015585" and r["source"] == "vast" and r["status"] == "running", r
assert r["device"] == "1× RTX 3090"
assert abs(r["cost"] - 2 * insts[0]["dph_total"]) < 1e-9 and r["dph"] == insts[0]["dph_total"]
assert "jupyter_token" not in json.dumps(r) and not any("ssh" in k or "ip" in k for k in r["meta"])
assert ssh_target(insts[0]) == ("203.0.113.7", 40708)  # direct port mapping preferred, like `vastai ssh-url`

stopped = to_run(insts[1], now)
assert stopped["status"] == "exited" and stopped["cost"] is None
assert stopped["dph"] == insts[1]["storage_total_cost"]  # a stopped box still bills storage
assert ssh_target(insts[1]) == (insts[1]["ssh_host"], insts[1]["ssh_port"])  # no direct port -> proxy

labelled = to_run({**insts[0], "label": "mims-fpnet"}, now, projects=["mims"])
assert labelled["project"] == "mims" and labelled["name"] == "mims-fpnet"

text = (HERE / "samples/vast_box.txt").read_text()
b = parse_box(text)
g = b["gpus"][0]
assert g["name"] == "NVIDIA GeForce RTX 3090" and g["util"] == 0 and g["mem_used"] == 1256 and g["mem_total"] == 24576
assert g["temp"] == 49 and g["power"] == 23.23 and g["power_limit"] == 350
assert b["nproc"] == 32 and b["cpu"] is None  # no previous sample yet
assert b["mem"] == {"total": 101202358272, "used": 101202358272 - 86530244608}
assert b["disk"] == {"total": 107374182400, "used": 41265504256}
busy, total = b["cpu_raw"]
b2 = parse_box(text, prev_cpu=(busy - 250, total - 1000))
assert abs(b2["cpu"] - 25.0) < 1e-9, b2["cpu"]

assert parse_box(text.replace("23.23", "[N/A]"))["gpus"][0]["power"] is None
print("ok test_vast")
