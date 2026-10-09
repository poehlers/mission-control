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
