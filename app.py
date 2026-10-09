"""Mission Control: one FastAPI process, SSE for everything live. Bind 127.0.0.1 only."""
import asyncio
import json
import os
import re
import signal
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from pathlib import Path

import psutil
import yaml
from fastapi import FastAPI, Header, HTTPException, Query
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

import db
import roadmap
from collectors import kaggle, local, vast

ROOT = Path(__file__).parent
PROJECTS = {p["id"]: p for p in yaml.safe_load((ROOT / "projects.yaml").read_text())["projects"]}
sources = {}  # per-source poll health, shown in the UI: {"vast": {"ok": bool, "error": str|None, "t": epoch}}


class Hub:
    """In-memory pub/sub. Collectors only do work while a topic has subscribers."""

    def __init__(self):
        self.subs = defaultdict(set)

    def publish(self, topic, data):
        for q in self.subs[topic]:
            q.put_nowait((topic, data))

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


async def kaggle_fetch_log(r):
    """Full session log for a finished kernel -> logs/<run-id>.log; duration from the log's own timestamps."""
    raw = await asyncio.to_thread(kaggle.fetch_log, r["meta"]["ref"])
    text, last = kaggle.parse_log(raw)
    path = db.LOGS / f"{r['id']}.log"
    db.LOGS.mkdir(exist_ok=True)
    path.write_bytes(text)
    upd = {"id": r["id"], "log": str(path)}
    if last and r["started"]:
        upd["ended"] = r["started"] + last
    db.upsert(con, **upd)
    return str(path)


async def kaggle_loop():
    prefixes = {pid: p["kernel_prefix"] for pid, p in PROJECTS.items() if p.get("kernel_prefix")}
    while True:
        try:
            known = {}
            for r in db.runs(con):  # newest first, so this keeps each kernel's latest run
                if r["source"] == "kaggle":
                    known.setdefault(r["meta"]["slug"], r)
            rows, fetch = await asyncio.to_thread(kaggle.poll, known, prefixes)
            for row in rows:
                db.upsert(con, **row)
            for rid in fetch:
                await kaggle_fetch_log(get_run(rid))
            sources["kaggle"] = {"ok": True, "error": None, "t": time.time()}
        except Exception as e:
            sources["kaggle"] = {"ok": False, "error": str(e)[:300], "t": time.time()}
        await asyncio.sleep(60)  # status only changes on minutes scale; CLAUDE.md floor is 15 s


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
    tasks = [asyncio.create_task(c()) for c in (local_loop, runs_loop, vast_loop, box_loop, kaggle_loop)]
    yield
    for t in tasks:
        t.cancel()


app = FastAPI(lifespan=lifespan)


@app.get("/api/runs")
async def runs_json():
    """One-shot JSON of the run registry + source health, for scripts and agents (the UI uses /api/stream)."""
    return runs_payload()


def topic_history(topic):
    if topic == "runs":
        return [runs_payload()]
    if topic == "local":
        return list(local_hist)
    iid = int(topic.removeprefix("box:"))
    return list(box_hist[iid]) if iid in box_hist else []


TOPIC = re.compile(r"^(runs|local|box:\d+)$")


@app.get("/api/stream")
async def stream(t: list[str] = Query([])):
    """All hub topics a page needs over ONE connection (browsers allow only 6 per host over HTTP/1.1).
    Messages: {"topic", "items", "reset"}; reset=True carries the topic's history and replaces what the page had."""
    topics = sorted(set(t))
    if not topics or not all(TOPIC.match(x) for x in topics):
        raise HTTPException(400, "topics: runs, local, box:<id>")

    async def gen():
        q = asyncio.Queue()
        for x in topics:
            hub.subs[x].add(q)
        try:
            for x in topics:
                yield f"data: {json.dumps({'topic': x, 'items': topic_history(x), 'reset': True})}\n\n"
            while True:
                try:
                    x, item = await asyncio.wait_for(q.get(), 15)
                    yield f"data: {json.dumps({'topic': x, 'items': [item], 'reset': False})}\n\n"
                except TimeoutError:
                    yield ": ping\n\n"
        finally:
            for x in topics:
                hub.subs[x].discard(q)
    return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})


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
    if r["source"] == "kaggle" and not (r["log"] and Path(r["log"]).exists()):
        if r["status"] not in kaggle.TERMINAL:
            raise HTTPException(409, "Kaggle has no live log for a running kernel; the full log is fetched when it finishes.")
        try:
            r["meta"] = json.loads(r["meta"])
            r["log"] = await kaggle_fetch_log(r)
        except Exception as e:
            raise HTTPException(502, f"Kaggle log fetch failed: {e}")
    if not r["log"] or not Path(r["log"]).exists():
        raise HTTPException(404, "no log for this run (yet)")
    return sse_iter(follow_file(r["log"], n))



# ---------- Lifecycle actions: each needs the exact action echoed back from the page's confirm dialog ----------
ACTIONS = {"local": {"kill"}, "vast": {"stop", "destroy"}}


@app.post("/api/runs/{run_id}/{action}")
async def run_action(run_id: str, action: str, x_mc_confirm: str = Header("")):
    # A custom header can't be sent cross-origin without a CORS preflight, which this app never allows,
    # so other pages (including the sandboxed architecture iframe) can't fire these.
    if x_mc_confirm != f"{action} {run_id}":
        raise HTTPException(428, "confirm header missing or for a different action")
    r = get_run(run_id)
    if action not in ACTIONS.get(r["source"], ()):
        raise HTTPException(400, f"{action} is not available for {r['source']} runs")
    if r["source"] == "local":
        if r["status"] != "running":
            raise HTTPException(409, f"run is {r['status']}")
        try:
            p = psutil.Process(r["pid"])
            if abs(p.create_time() - r["started"]) > 5:  # pid reused by an unrelated process
                raise HTTPException(409, "pid now belongs to another process; not killing it")
            # the whole tree: with a shell command the pid is `sh`, and its children keep the log pipe open
            tree = p.children(recursive=True) + [p]
            for q in tree:
                try:
                    q.send_signal(signal.SIGTERM)
                except psutil.NoSuchProcess:
                    pass
        except psutil.NoSuchProcess:
            raise HTTPException(409, "process already gone")
        return {"ok": True, "detail": f"SIGTERM sent to pid {r['pid']} and {len(tree) - 1} child process(es)"}
    iid = int(run_id.removeprefix("vast-"))
    from vastai.sdk import VastAI
    try:
        res = await asyncio.to_thread(getattr(VastAI(), f"{action}_instance"), iid)
    except Exception as e:
        raise HTTPException(502, f"vast {action} failed: {e}")
    return {"ok": True, "detail": f"vast {action} {iid}: {str(res)[:200]}"}


# ---------- Projects (read-only toward each repo) ----------
def project(pid):
    if pid not in PROJECTS:
        raise HTTPException(404, "unknown project")
    return PROJECTS[pid]


SERVABLE = {".md", ".html", ".htm", ".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".css", ".js"}


def repo_file(p, rel):
    """A file inside the repo that's safe to show: no traversal, no dotfiles (.git, .env), doc/asset types only."""
    root = Path(p["path"]).resolve()
    f = (root / rel).resolve()
    if not f.is_relative_to(root) or any(part.startswith(".") for part in f.relative_to(root).parts):
        raise HTTPException(403, "outside the repo or a dotfile")
    if f.suffix.lower() not in SERVABLE:
        raise HTTPException(403, f"not a doc/asset type: {f.suffix}")
    if not f.is_file():
        raise HTTPException(404, f"{rel} does not exist")
    return f


@app.get("/api/projects")
async def projects():
    return [{k: p.get(k) for k in ("id", "name", "path", "docs", "architecture", "roadmap")} for p in PROJECTS.values()]


@app.get("/api/projects/{pid}/file")
async def project_file(pid: str, path: str):
    return FileResponse(repo_file(project(pid), path), headers={"Cache-Control": "no-cache"})


@app.get("/api/projects/{pid}/watch")
async def project_watch(pid: str):
    """mtimes of the project's docs/roadmap/architecture; pushes the changed ones (polled every 2 s)."""
    p = project(pid)
    paths = [*p.get("docs", []), *(x for x in (p.get("roadmap"), p.get("architecture")) if x)]

    def mtimes():
        out = {}
        for rel in paths:
            try:
                out[rel] = (Path(p["path"]) / rel).stat().st_mtime
            except OSError:
                out[rel] = None
        return out

    async def gen():
        last = mtimes()
        yield last
        while True:
            await asyncio.sleep(2)
            now = mtimes()
            changed = {k: v for k, v in now.items() if v != last.get(k)}
            if changed:
                last = now
                yield changed
    return sse_iter(gen())


@app.get("/api/projects/{pid}/roadmap")
async def project_roadmap(pid: str):
    p = project(pid)
    if not p.get("roadmap"):
        return {"exists": False, "path": None}
    f = Path(p["path"]) / p["roadmap"]
    if not f.is_file():
        return {"exists": False, "path": p["roadmap"]}
    return {"exists": True, "path": p["roadmap"], **roadmap.parse(f.read_text(errors="replace"))}


async def git(path, *args):
    p = await asyncio.create_subprocess_exec(
        "git", "-C", path, *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"})  # status must not refresh the repo's index: read-only
    out, err = await p.communicate()
    if p.returncode:
        raise HTTPException(500, f"git: {err.decode().strip()}")
    return out.decode(errors="replace")


@app.get("/api/projects/{pid}/git")
async def project_git(pid: str, n: int = 15):
    path = project(pid)["path"]
    log, status = await asyncio.gather(
        git(path, "log", f"-n{n}", "--format=%h%x1f%ct%x1f%an%x1f%s"),
        git(path, "status", "--porcelain=v1", "--branch"))
    commits = [dict(zip(("hash", "t", "author", "subject"), l.split("\x1f"))) for l in log.splitlines()]
    head, *files = status.splitlines()
    return {"branch": head.removeprefix("## "), "commits": commits,
            "files": [{"xy": f[:2], "path": f[3:]} for f in files]}


class Static(StaticFiles):
    def file_response(self, *a, **kw):  # revalidate (ETag) so edits show without a hard reload
        r = super().file_response(*a, **kw)
        r.headers["Cache-Control"] = "no-cache"
        return r


app.mount("/static", Static(directory=ROOT / "web"), name="static")


@app.get("/")
async def index():
    return FileResponse(ROOT / "web" / "index.html", headers={"Cache-Control": "no-cache"})
