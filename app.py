"""Mission Control: one FastAPI process, SSE for everything live. Bind 127.0.0.1 only."""
import asyncio
import json
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

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


@asynccontextmanager
async def lifespan(app):
    tasks = [asyncio.create_task(local_loop())]
    yield
    for t in tasks:
        t.cancel()


app = FastAPI(lifespan=lifespan)


@app.get("/api/local/stream")
async def local_stream(request: Request):
    return sse("local", request, list(local_hist))


app.mount("/static", StaticFiles(directory=ROOT / "web"), name="static")


@app.get("/")
async def index():
    return FileResponse(ROOT / "web" / "index.html")
