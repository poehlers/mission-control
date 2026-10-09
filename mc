#!/bin/sh
# Symlink into PATH: ln -s /Volumes/T7/mission-control/mc ~/.local/bin/mc
D=/Volumes/T7/mission-control
exec "$D/.venv/bin/python" "$D/runner.py" "$@"
