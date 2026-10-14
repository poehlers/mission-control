"""Kaggle: status mapping (real enum), session-log parsing (real sample), poll's new/re-run/finish detection."""
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace as NS

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent))
from kagglesdk.kernels.types.kernels_enums import KernelWorkerStatus as KWS

from collectors import kaggle

assert kaggle.map_status(KWS.COMPLETE) == "done" and kaggle.map_status(KWS.ERROR) == "failed"
assert kaggle.map_status(KWS.RUNNING) == "running" and kaggle.map_status(KWS.QUEUED) == "queued"
assert kaggle.map_status(KWS.CANCEL_ACKNOWLEDGED) == "cancelled"

text, last = kaggle.parse_log((HERE / "samples/kaggle_log.json").read_text())
lines = text.decode().splitlines()
assert lines[0] == "rg 0: 131072 spectra, 8056471 peaks, total peaks 8056471, 15s", lines[0]
assert lines[-1] == "[NbConvertApp] Writing 315178 bytes to __results__.html"
assert len(lines) == 7 and abs(last - 16522.000931538) < 1e-6
assert kaggle.parse_log('[{"stream_name":"stdout","time":1,"data":"a\\r"},{"stream_name":"stdout","time":2,"data":"\\rb\\n"}]')[0] == b"a\nb\n"
assert kaggle.parse_log("not json")[0] == b"not json"

assert kaggle.device(NS(machine_shape="NvidiaTeslaT4", enable_gpu=True, enable_tpu=False)) == "NvidiaTeslaT4"
assert kaggle.device(NS(machine_shape=None, enable_gpu=False, enable_tpu=False)) == "CPU"
assert kaggle.device(NS(machine_shape="None", enable_gpu=False, enable_tpu=False)) == "CPU"  # real API value

# --- poll() with a stubbed SDK: the cheap path must skip finished kernels, and finishing must trigger a log fetch.
calls = []
kernels = [NS(ref="pieteroehlers/mims-a", title="a", last_run_time=datetime(2026, 10, 4, 9, 0)),
           NS(ref="pieteroehlers/kadd-b", title="b", last_run_time=datetime(2026, 10, 5, 9, 0)),
           NS(ref="", title="[Private Notebook]", last_run_time=datetime(2010, 4, 1))]
status = {"pieteroehlers/mims-a": KWS.RUNNING, "pieteroehlers/kadd-b": KWS.COMPLETE}
kaggle._api = NS(kernels_list=lambda **kw: kernels,
                 kernels_status=lambda ref: calls.append(("status", ref)) or NS(status=status[ref], failure_message=""))
kaggle._client_call = lambda m, c, ref, **kw: calls.append((m, ref)) or NS(
    metadata=NS(current_version_number=3, machine_shape="NvidiaTeslaT4", enable_gpu=True, enable_tpu=False))
prefixes = {"mims": "mims-"}

rows, fetch = kaggle.poll({}, prefixes)  # first sync: both new, nothing observed finishing
a, b = rows
assert a["id"] == "kaggle-mims-a-v3" and a["project"] == "mims" and a["status"] == "running" and a["device"] == "NvidiaTeslaT4"
assert a["started"] == datetime(2026, 10, 4, 9, 0, tzinfo=timezone.utc).timestamp()  # Kaggle times are naive UTC
assert b["project"] is None and b["status"] == "done" and fetch == []

known = {r["name"]: r for r in rows}
calls.clear()
status["pieteroehlers/mims-a"] = KWS.COMPLETE
rows, fetch = kaggle.poll(known, prefixes)  # a finishes; b is finished + unchanged -> zero calls for b
assert calls == [("status", "pieteroehlers/mims-a")], calls
assert rows[0]["status"] == "done" and rows[0]["ended"] and fetch == ["kaggle-mims-a-v3"]

kernels[1] = NS(ref="pieteroehlers/kadd-b", title="b", last_run_time=datetime(2026, 10, 9, 9, 0))  # b re-run
status["pieteroehlers/kadd-b"] = KWS.QUEUED
known = {"mims-a": {**rows[0]}, "kadd-b": known["kadd-b"]}
calls.clear()
rows, fetch = kaggle.poll(known, prefixes)
assert ("get_kernel", "pieteroehlers/kadd-b") in calls and rows[0]["status"] == "queued" and rows[0]["ended"] is None
print("ok test_kaggle")

# --- budget, 429 back-off, per-kernel errors
class HTTPErr(Exception):
    def __init__(self, code): super().__init__(f"{code} Client Error"); self.response = NS(status_code=code)

many = [NS(ref=f"pieteroehlers/k{i}", title=str(i), last_run_time=datetime(2026, 9, 1 + i)) for i in range(20)]
kaggle._api = NS(kernels_list=lambda **kw: many, kernels_status=lambda ref: NS(status=KWS.COMPLETE, failure_message=""))
calls.clear()
rows, _ = kaggle.poll({}, prefixes, budget=8)
assert len(rows) == 4 and len(calls) == 4, (len(rows), len(calls))  # get_kernel + status per new kernel

def status_404_then_429(ref):
    if ref.endswith("k0"): raise HTTPErr(404)
    if ref.endswith("k2"): raise HTTPErr(429)
    return NS(status=KWS.COMPLETE, failure_message="")
kaggle._api = NS(kernels_list=lambda **kw: many, kernels_status=status_404_then_429)
rows, _ = kaggle.poll({}, prefixes, budget=100)
assert [r["status"] for r in rows] == ["unknown", "done"], rows  # k0 404 -> unknown (not retried), k2 429 -> stop
assert "404" in rows[0]["meta"]["error"]
rows2, _ = kaggle.poll({r["name"]: r for r in rows}, prefixes, budget=100)
assert all(r["name"] not in ("k0", "k1") for r in rows2)  # settled kernels cost nothing next tick
print("ok test_kaggle budget/backoff")

from collectors.kaggle import superseded
rs = [{"id": "kaggle-s-v2", "source": "kaggle", "status": "done", "meta": {"slug": "s"}},
      {"id": "vast-1", "source": "vast", "status": "running", "meta": {}},
      {"id": "kaggle-s-v1", "source": "kaggle", "status": "queued", "meta": {"slug": "s"}},
      {"id": "kaggle-t-v3", "source": "kaggle", "status": "running", "meta": {"slug": "t"}},
      {"id": "kaggle-s-v0", "source": "kaggle", "status": "done", "meta": {"slug": "s"}}]
assert superseded(rs) == ["kaggle-s-v1"]  # latest version of each kernel and settled old ones are left alone
print("ok test_kaggle superseded")
