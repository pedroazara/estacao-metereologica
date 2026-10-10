#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "$0")"
if ! python3 -c 'import serial' 2>/dev/null; then
  printf '%s\n' 'Falta a dependência pyserial. Instale em um ambiente Python:' 'python3 -m venv .venv' '.venv/bin/pip install -r requirements.txt' '.venv/bin/python server.py'
  exit 1
fi
exec python3 server.py "$@"
