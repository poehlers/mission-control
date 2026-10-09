"""Mission Control: one FastAPI process, SSE for everything live. Bind 127.0.0.1 only."""
import asyncio
import json
import re
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from pathlib import Path

import psutil
import yaml
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

import db
from collectors import local, vast

ROOT = Path(__file__).parent
PROJECTS = {p["id"]: p for p in yaml.safe_load((ROOT / "projects.yaml").read_text())["projects"]}
sources = {}  # per-source poll health, shown in the UI: {"vast": {"ok": bool, "error": str|None, "t": epoch}}


class Hub:
    """In-memory pub/sub. Collectors only do work while a topic has subscribers."""

    def __init__(self):
        self.subs = defaultdict(set)

    def publish(self, topic, data):
        for q in self.subs[topic]:
            q.put_nowait(data)

    def watched(self, topic):
        return bool(self.subs[topic])


hub = Hub()
local_hist = deque(maxlen=90)  # ~3 min of 2 s samples, replayed on connect for the sparklines


def sse_iter(agen):
    """SSE from an async generator; Starlette cancels it (running its finally) when the client goes away."""
    async def gen():
        async for item in agen:
            yield f"data: {json.dumps(item)}\n\n"
    return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})


ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b[()][A-Z0-9]")


def clean(lines):
    return [ANSI.sub("", b.decode("utf-8", "replace")) for b in lines]


async def follow_file(path, n=500):
    """Last n lines, then new complete lines as they're appended (polled every 0.5 s)."""
    with open(path, "rb") as f:
        f.seek(0, 2)
        start = max(0, f.tell() - 1024 * 1024)  # ponytail: 1 MB lookback; n lines longer than that get truncated
        f.seek(start)
        buf = f.read()
        if start:
            buf = buf.split(b"\n", 1)[-1]
        *lines, rest = buf.split(b"\n")
        yield clean(lines[-n:])
        while True:
            await asyncio.sleep(0.5)
            new = f.read()
            if new:
                *lines, rest = (rest + new).split(b"\n")
                if lines:
                    yield clean(lines)


def sse(topic, request: Request, initial=()):
    async def gen():
        q = asyncio.Queue()
        hub.subs[topic].add(q)
        try:
            for item in initial:
                yield f"data: {json.dumps(item)}\n\n"
            while True:
                try:
                    item = await asyncio.wait_for(q.get(), 15)
                    yield f"data: {json.dumps(item)}\n\n"
                except TimeoutError:
                    yield ": ping\n\n"
                if await request.is_disconnected():
                    break
        finally:
            hub.subs[topic].discard(q)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


async def local_loop():
    while True:
        if hub.watched("local"):
            s = await asyncio.to_thread(local.sample)
            local_hist.append(s)
            hub.publish("local", s)
            await asyncio.sleep(2)
        else:
            local_hist.clear()  # stale history would draw a misleading sparkline
            await asyncio.sleep(1)


con = db.connect()  # used only from the event-loop thread


async def runs_loop():
    last = None
    while True:
        # A local run whose process vanished without the runner recording an exit (runner killed -9, reboot).
        # ponytail: pid reuse could keep a dead run "running"; store process create_time if that bites.
        for r in con.execute("select id, pid from runs where source='local' and status='running'").fetchall():
            if not psutil.pid_exists(r["pid"]):
                db.upsert(con, id=r["id"], status="lost", ended=time.time())
        if hub.watched("runs"):
            rs = runs_payload()
            j = json.dumps(rs)
            if j != last:
                last = j
                hub.publish("runs", rs)
        else:
            last = None
        await asyncio.sleep(2)


def runs_payload():
    return {"runs": db.runs(con), "sources": sources}


async def vast_loop():
    while True:
        try:
            insts = await asyncio.to_thread(vast.fetch)
            now = time.time()
            seen = set()
            for i in insts:
                vast.ssh_targets[i["id"]] = vast.ssh_target(i)
                row = vast.to_run(i, now, PROJECTS)
                seen.add(row["id"])
                db.upsert(con, **row)
            for r in con.execute("select id from runs where source='vast' and status!='destroyed'").fetchall():
                if r["id"] not in seen:  # gone from the account: destroyed; keep its last cost estimate
                    db.upsert(con, id=r["id"], status="destroyed", ended=now, dph=0)
            sources["vast"] = {"ok": True, "error": None, "t": now}
        except Exception as e:  # network/API hiccup: show it, keep the last known rows
            sources["vast"] = {"ok": False, "error": str(e)[:300], "t": time.time()}
        await asyncio.sleep(20)  # CLAUDE.md: never faster than 15 s


box_hist = defaultdict(lambda: deque(maxlen=60))  # per instance, 5 s samples


async def box_sample(iid):
    h = box_hist[iid]
    prev = h[-1].get("cpu_raw") if h else None
    try:
        s = vast.parse_box(await vast.ssh_run(iid, vast.METRICS), prev)
    except Exception as e:
        s = {"error": str(e)[:300], "cpu_raw": prev}
    s["t"] = time.time()
    h.append(s)
    hub.publish(f"box:{iid}", s)


async def box_loop():
    """nvidia-smi + cpu/mem/disk over ssh, only for boxes someone is looking at."""
    while True:
        watched = [iid for iid in vast.ssh_targets if hub.watched(f"box:{iid}")]
        for iid in list(box_hist):
            if iid not in watched:
                del box_hist[iid]
        await asyncio.gather(*(box_sample(i) for i in watched))
        await asyncio.sleep(5)


@asynccontextmanager
async def lifespan(app):
    tasks = [asyncio.create_task(c()) for c in (local_loop, runs_loop, vast_loop, box_loop)]
    yield
    for t in tasks:
        t.cancel()


app = FastAPI(lifespan=lifespan)


@app.get("/api/local/stream")
async def local_stream(request: Request):
    return sse("local", request, list(local_hist))


@app.get("/api/runs/stream")
async def runs_stream(request: Request):
    return sse("runs", request, [runs_payload()])


def get_run(run_id):
    r = con.execute("select * from runs where id=?", (run_id,)).fetchone()
    if not r:
        raise HTTPException(404, "no such run")
    return dict(r)


def vast_iid(r):
    iid = int(r["id"].removeprefix("vast-"))
    if iid not in vast.ssh_targets:
        raise HTTPException(409, "instance not seen by the poller yet")
    return iid


JOB_LOG = re.compile(r"^[\w.-]+\.log$")


@app.get("/api/runs/{run_id}/logs")
async def run_logs(run_id: str):
    """Log names a run offers. Vast: the queue log plus per-job logs in /root/logs (newest first)."""
    r = get_run(run_id)
    if r["source"] != "vast":
        return ["output"]
    if r["status"] != "running":
        return []
    try:
        out = await vast.ssh_run(vast_iid(r), "ls -1t /root/logs/ 2>/dev/null; true")
    except RuntimeError as e:
        raise HTTPException(502, f"ssh: {e}")
    return ["queue"] + [x for x in out.split() if JOB_LOG.match(x)]


@app.get("/api/runs/{run_id}/log")
async def run_log(run_id: str, name: str = "output", n: int = 500):
    r = get_run(run_id)
    if r["source"] == "vast":
        if name != "queue" and not JOB_LOG.match(name):
            raise HTTPException(400, "bad log name")
        iid = vast_iid(r)

        async def lines():
            async for ls in vast.follow(iid, "/root/queue.log" if name == "queue" else f"/root/logs/{name}", n):
                yield clean(ls)
        return sse_iter(lines())
    if not r["log"] or not Path(r["log"]).exists():
        raise HTTPException(404, "no log for this run (yet)")
    return sse_iter(follow_file(r["log"], n))


@app.get("/api/vast/{iid}/stream")
async def box_stream(iid: int, request: Request):
    return sse(f"box:{iid}", request, list(box_hist[iid]) if iid in box_hist else [])


class Static(StaticFiles):
    def file_response(self, *a, **kw):  # revalidate (ETag) so edits show without a hard reload
        r = super().file_response(*a, **kw)
        r.headers["Cache-Control"] = "no-cache"
        return r


app.mount("/static", Static(directory=ROOT / "web"), name="static")


@app.get("/")
async def index():
    return FileResponse(ROOT / "web" / "index.html", headers={"Cache-Control": "no-cache"})
