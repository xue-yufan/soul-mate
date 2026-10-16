# 生产模式：不 reload，单 worker
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot\..

$env:SOUL_MATE_APP__ENV = "prod"
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1
