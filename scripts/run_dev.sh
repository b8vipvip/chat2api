#!/usr/bin/env sh
set -eu
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
exec uvicorn app.main:app --reload --host 0.0.0.0 --port 8765
