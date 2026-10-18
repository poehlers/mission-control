# Mission Control roadmap

## Done
- [x] Local runs via `mc run` — tee to logs/, tqdm `\r` redraws turned into lines, SQLite registry (2026-10-09)
- [x] Live logs over SSE — last N lines on connect, auto-scroll that pauses on scroll-up, filter, ANSI stripped (2026-10-09)
- [x] Vast.ai instances — state, $/h burn and accrued estimate; ssh `tail -F` logs with reconnect; nvidia-smi sparklines (2026-10-10)
- [x] Kaggle kernels — status polling within an API budget (429-safe), full log fetched once a kernel finishes (2026-10-10)
- [x] One multiplexed SSE stream per page — Chrome caps HTTP/1.1 at 6 connections per host (2026-10-10)
- [x] Lifecycle actions behind an exact-action confirm header — kill (process tree), Vast stop/destroy (2026-10-11)
- [x] Roadmap board parsed from each repo's docs/ROADMAP.md, with off-format lines reported instead of dropped (2026-10-11)
- [x] Projects menu — tracked, auto-discovered local repos and GitHub-only repos, each shown once (2026-10-12)
- [x] Vast log discovery — every *.log/*.out/*.err on the box instead of one fixed path (2026-10-13)
- [x] Manual run status and automatic `superseded` for old Kaggle versions (2026-10-14)
- [x] Collapsible roadmap columns with show more / less (2026-10-14)
- [x] Public release — portable paths, example config, CI (2026-10-18)

## Now
- [ ] Watch real use for a week — note anything that still needs a terminal

## Planned
- [ ] Notifications when a run finishes or fails — browser Notification API, no extra service
- [ ] Re-scan for new local clones without a server restart
- [ ] Per-run cost totals for Vast from the billing API instead of the uptime × $/h estimate

## Ideas
- [ ] Other GPU clouds (RunPod, Lambda) behind the same run-row shape
- [ ] Linux host stats (GPU via nvidia-smi locally) for running on a workstation instead of a Mac
