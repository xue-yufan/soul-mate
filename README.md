# Soul Mate

本地运行的 AI 伴侣桌面端：前端是 Electron + Vue 渲染层，后端是 Python 的 LangGraph 多 Agent 服务（人格「遐蝶」、长期记忆、RAG、工具调用、TTS）。

## 仓库结构

| 目录 | 说明 |
| --- | --- |
| `frontend/` | Electron + Vue 3 桌面端（electron-vite 构建，three-vrm 渲染 3D 形象） |
| `backend/` | Python 后端服务（FastAPI + WebSocket + LangGraph） |
| `docs/` | 架构文档与后续改进计划 |

## 快速开始

### 后端

```bash
cd backend
uv sync
cp config.local.example.yaml config.local.yaml    # Windows: copy config.local.example.yaml config.local.yaml
uv run uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

启动后：健康检查 `http://127.0.0.1:8000/health`，WebSocket `ws://127.0.0.1:8000/ws`，API 文档 `http://127.0.0.1:8000/docs`。

API Key 走环境变量（如 `SOUL_MATE_MODEL__API_KEY`），不要写进被跟踪的文件；功能开关默认全关，按需用 `SOUL_MATE_FEATURES__MEMORY` / `__RAG` / `__TOOLS` / `__TTS` 打开。更多细节见 [backend/README.md](backend/README.md)。

### 前端

```bash
cd frontend
npm install
npm run dev      # 开发模式
npm run dist     # 打包 Windows 安装包
```

开发模式下前端会去上一级找 `backend/`，自动拉起后端进程并轮询 `/health` 做就绪检查；打包版则使用随包附带的 `resources/backend` 与 `uv.exe`。

## 配置与机密

以下内容不入库：`config.local.yaml`、`config.overrides.yaml`、`.env`、`backend/data/`（SQLite 与日志）、`frontend/_stage/`（含 Key 的备份）、`node_modules/`、`.venv/`、`release/`、`out/`。提交前请确认没有把真实 API Key 带进版本库。
