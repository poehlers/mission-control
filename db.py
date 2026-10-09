"""Run registry in data/mc.db. Shared by app.py (collectors) and runner.py (mc run)."""
import json
import sqlite3
from pathlib import Path

ROOT = Path(__file__).parent
DB = ROOT / "data" / "mc.db"
LOGS = ROOT / "logs"


def connect():
    DB.parent.mkdir(exist_ok=True)
    con = sqlite3.connect(DB, timeout=10, isolation_level=None)  # autocommit
    con.row_factory = sqlite3.Row
    con.execute("""create table if not exists runs(
        id text primary key, source text, project text, name text, status text,
        started real, ended real, exit_code int, pid int, cmd text, device text,
        dph real, cost real, log text, meta text)""")
    return con


def upsert(con, **row):
    if isinstance(row.get("meta"), dict):
        row["meta"] = json.dumps(row["meta"])
    keys = list(row)
    con.execute(f"insert into runs({','.join(keys)}) values({','.join('?' * len(keys))}) "
                f"on conflict(id) do update set {','.join(f'{k}=excluded.{k}' for k in keys if k != 'id')}",
                [row[k] for k in keys])


def runs(con):
    out = []
    for r in con.execute("select * from runs order by coalesce(started, 0) desc"):
        d = dict(r)
        d["meta"] = json.loads(d["meta"]) if d["meta"] else {}
        out.append(d)
    return out
