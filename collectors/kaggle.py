"""Kaggle kernels: list + status polling, full log once finished. Kaggle has no live log for a running kernel.

Everything here is blocking (Kaggle SDK); app.py calls it via asyncio.to_thread and does the DB writes itself.
One run row per kernel version: kaggle-<slug>-v<N>.
"""
import json
import time
from datetime import timezone

from runner import split_cr

TERMINAL = {"done", "failed", "cancelled"}
SETTLED = TERMINAL | {"unknown"}  # not re-polled until the kernel runs again
BUDGET = 8  # API calls per poll tick; Kaggle answers 429 to a burst of ~100
_STATUS = {"COMPLETE": "done", "ERROR": "failed", "RUNNING": "running", "QUEUED": "queued", "NEW_SCRIPT": "queued",
           "CANCEL_REQUESTED": "cancelled", "CANCEL_ACKNOWLEDGED": "cancelled"}


def map_status(s):
    """KernelWorkerStatus enum (or its name/str form) -> our vocabulary."""
    name = getattr(s, "name", str(s)).split(".")[-1].upper()
    return _STATUS.get(name, name.lower())


def device(md):
    shape = md.machine_shape if md.machine_shape not in (None, "", "None") else None  # API sends the string "None"
    return shape or ("TPU" if md.enable_tpu else "GPU" if md.enable_gpu else "CPU")


def parse_log(raw):
    """Kaggle session log (JSON list of {stream_name, time, data}) -> (text bytes, seconds of the last entry)."""
    try:
        entries = json.loads(raw)
    except ValueError:  # truncated/odd log: show it as-is rather than nothing
        return raw.encode(), None
    text, prev = b"", b"\n"
    for e in entries:
        out, prev = split_cr(e.get("data", "").encode(), prev)
        text += out
    return text, max((e.get("time") or 0 for e in entries), default=None)


_api = None


def api():
    global _api
    if _api is None:
        from kaggle.api.kaggle_api_extended import KaggleApi  # reads ~/.kaggle itself; creds never touch our code
        _api = KaggleApi()
        _api.authenticate()
    return _api


def _client_call(method, req_cls, ref, **fields):
    from kagglesdk.kernels.types import kernels_api_service as t
    req = getattr(t, req_cls)()
    req.user_name, req.kernel_slug = ref.split("/")
    for k, v in fields.items():
        setattr(req, k, v)
    with api().build_kaggle_client() as kg:
        return getattr(kg.kernels.kernels_api_client, method)(req)


def fetch_log(ref):
    """Only the session log; kernels_output would also download every output file."""
    return _client_call("list_kernel_session_output", "ApiListKernelSessionOutputRequest", ref, page_size=1).log or ""


def superseded(runs):
    """Ids of older kernel versions still marked unfinished. Kaggle's API only reports a kernel's latest version,
    so once a newer one is pushed an older queued/running row would otherwise stay that way forever."""
    latest, out = set(), []
    for r in runs:  # newest first
        if r["source"] != "kaggle":
            continue
        slug = r["meta"]["slug"]
        if slug in latest and r["status"] not in SETTLED and r["status"] != "superseded":
            out.append(r["id"])
        latest.add(slug)
    return out


def _http_status(e):
    return getattr(getattr(e, "response", None), "status_code", None)


def poll(known, prefixes, budget=BUDGET):
    """known: slug -> latest run row (from the DB). Returns (rows to upsert, run ids whose log to fetch now).

    Only kernels that are new, re-run (last_run_time moved) or not yet finished cost API calls, and at most
    `budget` of them per tick, newest first; the first sync of a big account catches up over a few minutes.
    """
    rows, fetch, now = [], [], time.time()
    pending = []
    for k in api().kernels_list(mine=True, page_size=100, sort_by="dateRun"):
        if not k.ref:  # "[Private Notebook]" placeholders come back without a ref
            continue
        slug = k.ref.split("/")[1]
        # list's last_run_time is the real run start; get_kernel's is a metadata-refresh time, don't use it
        lrt = k.last_run_time.replace(tzinfo=timezone.utc).timestamp()
        old = known.get(slug)
        if old and old["meta"].get("lrt") == lrt and old["status"] in SETTLED:
            continue
        pending.append((k, slug, lrt, old))
    # unfinished runs first (they're what you're waiting on), then new/re-run kernels newest first
    pending.sort(key=lambda x: not (x[3] and x[3]["meta"].get("lrt") == x[2]))
    for k, slug, lrt, old in pending:
        if budget <= 0:
            break
        try:
            row, budget = _one(k, slug, lrt, old, prefixes, now, budget)
        except Exception as e:
            if _http_status(e) == 429:
                break  # back off until the next tick
            if old and old["meta"].get("lrt") == lrt:
                continue  # known unfinished run: just retry next tick
            row = {**_new_row(k, slug, lrt, prefixes, None), "status": "unknown"}
            row["meta"]["error"] = str(e)[:300]
        if row["status"] in TERMINAL and old and old["status"] not in SETTLED:
            row["ended"] = now  # observed finishing; refined from the log's own timestamps once fetched
            fetch.append(row["id"])
        rows.append(row)
    return rows, fetch


def _new_row(k, slug, lrt, prefixes, md):
    ver = md.current_version_number if md else None
    return {"id": f"kaggle-{slug}-v{ver}" if md else f"kaggle-{slug}-{int(lrt)}", "source": "kaggle", "name": slug,
            "project": next((p for p, pre in prefixes.items() if slug.startswith(pre)), None),
            "started": lrt, "ended": None, "device": device(md) if md else None, "log": None,
            "meta": {"ref": k.ref, "slug": slug, "title": k.title, "lrt": lrt, "version": ver}}


def _one(k, slug, lrt, old, prefixes, now, budget):
    if old and old["meta"].get("lrt") == lrt:
        row = {k2: old[k2] for k2 in ("id", "meta")}
    else:
        budget -= 1
        row = _new_row(k, slug, lrt, prefixes, _client_call("get_kernel", "ApiGetKernelRequest", k.ref).metadata)
    budget -= 1
    st = api().kernels_status(k.ref)
    row["status"] = map_status(st.status)
    if st.failure_message:
        row["meta"] = {**row["meta"], "failure": st.failure_message[:500]}
    return row, budget
