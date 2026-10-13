# Mission Control

Local live dashboard for projects, runs (local / Kaggle / Vast) and devices. See `CLAUDE.md` for stack and rules.

## Start

```sh
cd /Volumes/T7/mission-control
.venv/bin/uvicorn app:app --host 127.0.0.1 --port 8787 --reload --timeout-graceful-shutdown 1
```

Open http://127.0.0.1:8787. It binds to localhost only, so never expose it.

## Tests

```sh
for t in tests/test_*.py; do .venv/bin/python $t || break; done
```

## Track a local run: `mc run`

```sh
ln -s /Volumes/T7/mission-control/mc ~/.local/bin/mc     # once; any dir on PATH works
mc run train-fpnet --project mims -- python src/train.py --epochs 10
mc run sweep -- 'python a.py && python b.py'             # one quoted arg = runs through the shell
```

Output still shows in your terminal. It is also written to `logs/<run-id>.log`, with tqdm `\r` redraws turned into
lines, and the run (pid, cmd, exit code) appears under **Runs** with a live log. Ctrl-C stops the job, and the
exit is recorded. A run whose process disappears without a recorded exit shows as `lost`.

## Pages

- **Overview**: Vast burn ($/h, accrued, per box), what's running, what finished since you pressed *Mark seen*, each project's Now column.
- **Runs**: every local / Kaggle / Vast run, filterable. A run page has the live log (pauses when you scroll up, filter box).
  Vast: pick any log found on the box (`*.log/*.out/*.err` under /root and /workspace, newest first) or type a path (ssh `tail -F`, reconnects), live nvidia-smi/CPU/RAM/disk. Kaggle: no live log
  exists for a running kernel; the full log is fetched when it finishes (or when you open a finished one).
- **Devices**: this Mac + every running Vast box. **Processes**: heaviest local processes, tracked or not.
- **MIMS** (one tab per project in `projects.yaml`): docs rendered live from the repo, architecture page, git panel, roadmap board.

## Roadmap format (`docs/ROADMAP.md` in each project)

```md
## Done
- [x] Thing that shipped — optional note (2026-10-05)
## Now
## Planned
## Ideas
```
Exactly those four headings. Lines that don't fit are listed above the board rather than dropped.

## Actions

Run pages have **Kill** (local, SIGTERM to the process tree), **Stop** and **Destroy** (Vast). Each asks for
confirmation, and the server rejects any request that doesn't echo the exact action and run id. Kaggle has no cancel button.

## Notes

- Polling: Vast every 20 s, Kaggle every 60 s (at most 8 Kaggle API calls per tick; it rate-limits bursts).
  Mac stats and box metrics are sampled only while a page shows them. Idle CPU ~0.5% of one core.
- Each page uses one SSE connection for runs + stats (browsers allow 6 per host), plus one per open log.
- Accrued Vast cost is uptime × current $/h, an estimate rather than the invoice.

## Projects menu

**Projects ▾** lists each project once:
- **Tracked**: from `projects.yaml`.
- **Local**: git repos directly under `/Volumes/T7`, found at server start and shown read-only with their top-level `.md` files.
- **GitHub only**: your repos (via the logged-in `gh` CLI) that aren't cloned on the T7. They get a page with the README and commits.
