#!/usr/bin/env bash
set -e
cd "$(dirname "$0")/.."
export SOUL_MATE_APP__ENV=prod
export SOUL_MATE_LOGGING__LEVEL=INFO
exec uv run uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1
