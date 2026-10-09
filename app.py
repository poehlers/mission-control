"""Mission Control: one FastAPI process, SSE for everything live. Bind 127.0.0.1 only."""
import asyncio
import json
import re
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from pathlib import Path

import psutil
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

import db
from collectors import local

ROOT = Path(__file__).parent


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
            rs = db.runs(con)
            j = json.dumps(rs)
            if j != last:
                last = j
                hub.publish("runs", rs)
        else:
            last = None
        await asyncio.sleep(2)


@asynccontextmanager
async def lifespan(app):
    tasks = [asyncio.create_task(local_loop()), asyncio.create_task(runs_loop())]
    yield
    for t in tasks:
        t.cancel()


app = FastAPI(lifespan=lifespan)


@app.get("/api/local/stream")
async def local_stream(request: Request):
    return sse("local", request, list(local_hist))


@app.get("/api/runs/stream")
async def runs_stream(request: Request):
    return sse("runs", request, [db.runs(con)])


def get_run(run_id):
    r = con.execute("select * from runs where id=?", (run_id,)).fetchone()
    if not r:
        raise HTTPException(404, "no such run")
    return dict(r)


@app.get("/api/runs/{run_id}/log")
async def run_log(run_id: str, n: int = 500):
    r = get_run(run_id)
    if not r["log"] or not Path(r["log"]).exists():
        raise HTTPException(404, "no log for this run (yet)")
    return sse_iter(follow_file(r["log"], n))


class Static(StaticFiles):
    def file_response(self, *a, **kw):  # revalidate (ETag) so edits show without a hard reload
        r = super().file_response(*a, **kw)
        r.headers["Cache-Control"] = "no-cache"
        return r


app.mount("/static", Static(directory=ROOT / "web"), name="static")


@app.get("/")
async def index():
    return FileResponse(ROOT / "web" / "index.html", headers={"Cache-Control": "no-cache"})
