"""GitHub repos via the gh CLI (token stays in gh's keyring). Blocking; app.py calls these via asyncio.to_thread."""
import json
import os
import re
import subprocess
from pathlib import Path

# where local clones live (top level only); default: the folder this repo sits in
SCAN = Path(os.environ.get("MC_SCAN_DIR") or Path(__file__).resolve().parents[2])


def gh(*args):
    p = subprocess.run(["gh", *args], capture_output=True, text=True, timeout=30)
    if p.returncode:
        raise RuntimeError((p.stderr or p.stdout).strip()[:300] or f"gh exit {p.returncode}")
    return p.stdout


def full_name(remote):
    """'https://github.com/o/r.git' / 'git@github.com:o/r.git' / 'ssh://git@github.com/o/r' -> 'o/r' (lowercase)."""
    m = re.search(r"github\.com[:/]([^/\s]+)/([^/\s]+?)(?:\.git)?/?$", remote.strip())
    return f"{m.group(1)}/{m.group(2)}".lower() if m else None


def remote_full(path):
    p = subprocess.run(["git", "-C", str(path), "remote", "get-url", "origin"], capture_output=True, text=True)
    return full_name(p.stdout)


def local_clones(extra_paths=()):
    """full name -> local path, for git repos directly under SCAN plus the tracked projects' paths."""
    out = {}
    for d in [*SCAN.iterdir(), *map(Path, extra_paths)]:
        if (d / ".git").exists() and (name := remote_full(d)):
            out[name] = str(d)
    return out


def discover(projects):
    """Git repos directly under SCAN that projects.yaml doesn't list -> read-only auto projects
    (top-level .md files as docs, README first; docs/ROADMAP.md if present). Also sets `github` on every project."""
    tracked = {str(Path(p["path"]).resolve()) for p in projects.values()}
    auto = {}
    for d in sorted(SCAN.iterdir()):
        if not (d / ".git").is_dir() or str(d.resolve()) in tracked:
            continue
        pid = re.sub(r"[^a-z0-9-]+", "-", d.name.lower()).strip("-")
        if pid in projects or pid in auto:
            continue
        mds = sorted((f.name for f in d.glob("*.md") if not f.name.startswith(".")),  # ._x = macOS resource forks
                     key=lambda n: (n.lower() != "readme.md", n.lower()))
        auto[pid] = {"id": pid, "name": d.name, "path": str(d), "docs": mds[:8], "auto": True,
                     "roadmap": "docs/ROADMAP.md" if (d / "docs/ROADMAP.md").is_file() else None}
    for p in [*projects.values(), *auto.values()]:
        p["github"] = remote_full(p["path"])
    return auto


def repos(projects):
    """projects: id -> projects.yaml entry. Newest push first, with local clone and tracked project id attached."""
    rs = json.loads(gh("repo", "list", "--limit", "200", "--json",
                       "nameWithOwner,description,pushedAt,isPrivate,url,defaultBranchRef,isFork,isArchived"))
    clones = local_clones(p["path"] for p in projects.values())
    by_path = {p["path"].rstrip("/"): pid for pid, p in projects.items()}
    out = []
    for r in rs:
        local = clones.get(r["nameWithOwner"].lower())
        out.append({"full": r["nameWithOwner"], "description": r["description"] or "", "private": r["isPrivate"],
                    "fork": r["isFork"], "archived": r["isArchived"], "pushed": r["pushedAt"], "url": r["url"],
                    "branch": (r.get("defaultBranchRef") or {}).get("name"), "local": local,
                    "tracked": by_path.get((local or "").rstrip("/"))})
    return sorted(out, key=lambda r: r["pushed"] or "", reverse=True)


def detail(full):
    """README as GitHub-rendered (sanitized) HTML + last 15 commits."""
    try:
        readme = gh("api", f"repos/{full}/readme", "-H", "Accept: application/vnd.github.html+json")
    except RuntimeError as e:
        readme = "" if "404" in str(e) or "Not Found" in str(e) else f"<p>README failed: {e}</p>"
    try:
        cs = json.loads(gh("api", f"repos/{full}/commits?per_page=15"))
    except RuntimeError:  # empty repo: 409
        cs = []
    commits = [{"sha": c["sha"][:7], "subject": c["commit"]["message"].split("\n")[0],
                "date": c["commit"]["author"]["date"], "author": c["commit"]["author"]["name"]} for c in cs]
    return {"readme_html": readme, "commits": commits}
