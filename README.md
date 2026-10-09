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
