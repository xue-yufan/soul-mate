# 开发模式：自动 reload，便于改代码
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot\..

$env:SOUL_MATE_APP__ENV = "dev"
uv run uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
