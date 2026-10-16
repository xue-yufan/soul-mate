#!/usr/bin/env bash
set -e
cd "$(dirname "$0")/.."
export SOUL_MATE_APP__ENV=dev
exec uv run uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
