#!/bin/sh
# Symlink into PATH: ln -s "$PWD/mc" ~/.local/bin/mc
D=$(dirname "$(readlink -f "$0")")
exec "$D/.venv/bin/python" "$D/runner.py" "$@"
