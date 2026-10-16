# Soul Mate 后端

个人学习项目，桌面 AI Agent 的后端服务。搭载「遐蝶」人格与长期记忆，基于 LangGraph 多 Agent 架构，支持工具调用、RAG、TTS。

**当前版本**：v0.7（TTS）

---

## 环境要求

- Python >= 3.12（推荐 3.12，3.13+ 视生态而定）
- [uv](https://docs.astral.sh/uv/)
- Windows / Linux / macOS

---

## 快速开始

```bash
cd backend

# 安装依赖
uv sync

# 复制本地配置
cp config.local.example.yaml config.local.yaml   # Linux/macOS
copy config.local.example.yaml config.local.yaml # Windows

# 设置 API Key（推荐环境变量，不写进文件）
export SOUL_MATE_MODEL__API_KEY="sk-..."         # Linux/macOS
$env:SOUL_MATE_MODEL__API_KEY="sk-..."           # Windows PowerShell

# 启动
uv run uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

启动后：

- 健康检查：`http://127.0.0.1:8000/health`
- WebSocket：`ws://127.0.0.1:8000/ws`
- API 文档：`http://127.0.0.1:8000/docs`

### 功能开关

默认全部关闭。按需开启：

```powershell
$env:SOUL_MATE_FEATURES__MEMORY = "true"     # 长期记忆
$env:SOUL_MATE_FEATURES__RAG = "true"        # RAG + worldbook
$env:SOUL_MATE_FEATURES__TOOLS = "true"      # 工具调用
$env:SOUL_MATE_FEATURES__TTS = "true"        # 语音合成

# 记忆 / RAG 需要本地嵌入模型
$env:SOUL_MATE_PATHS__EMBED_MODEL_DIR = "D:/my_local_model/paraphrase-multilingual-MiniLM-L12-v2"
```

---

## 配置

### 三层覆盖

优先级从低到高：

```text
代码默认值 < config.default.yaml < config.local.yaml < 环境变量
```

### 文件

| 文件 | 是否入 Git | 用途 |
|---|---|---|
| `config.default.yaml` | 是 | 默认配置，所有人共享 |
| `config.local.example.yaml` | 是 | 本地配置示例 |
| `config.local.yaml` | 否 | 本机实际配置，可覆盖 default |

### 环境变量

前缀 `SOUL_MATE_`，嵌套用 `__` 分隔：

```bash
export SOUL_MATE_SERVER__PORT=9000
export SOUL_MATE_LOGGING__LEVEL=DEBUG
export SOUL_MATE_PATHS__DATA_DIR=/opt/soul-mate/data
export SOUL_MATE_PATHS__EMBED_MODEL_DIR="D:/my_local_model/paraphrase-multilingual-MiniLM-L12-v2"
export SOUL_MATE_MODEL__API_KEY="sk-..."
export SOUL_MATE_MODEL__MODEL="deepseek-chat"
export SOUL_MATE_FEATURES__MEMORY=true
export SOUL_MATE_FEATURES__RAG=true
export SOUL_MATE_FEATURES__TOOLS=true
export SOUL_MATE_FEATURES__TTS=true
export SOUL_MATE_AGENT__DEFAULT_SESSION_ID="default"
export SOUL_MATE_COST__BUDGET=30.0
```

### 关键配置项

| 配置 | 默认 | 说明 |
|---|---|---|
| `server.host` / `server.port` | `127.0.0.1:8000` | 监听地址 |
| `server.shutdown_timeout_s` | `10.0` | 优雅停机等后台任务秒数 |
| `paths.data_dir` | `./data` | 数据根目录 |
| `paths.embed_model_dir` | 无 | 本地嵌入模型目录 |
| `model.provider` | `deepseek` | 写入 `cost_log` |
| `model.model` | `deepseek-chat` | 默认模型 |
| `model.timeout_s` | `60` | 读取超时（秒）：两段流式内容之间的最长等待 |
| `model.connect_timeout_s` | `10` | 连接超时（秒）：断网时快速失败 |
| `model.max_retries` | `2` | 限流 / 5xx / 网络抖动的重试次数 |
| `agent.history_turns` | `6` | 注入 prompt 的最近轮数 |
| `agent.summary_trigger_tokens` | `3000` | 超过则触发摘要 |
| `agent.recursion_limit` | `10` | 图执行最大步数 |
| `agent.run_timeout_s` | `120` | 单轮执行超时 |
| `agent.max_tool_steps` | `5` | 单轮最大工具调用次数 |
| `agent.default_session_id` | `default` | 单用户单会话固定 ID |
| `cost.budget` | `30.0` | 预算（CNY） |
| `features.memory` | `false` | 记忆总开关 |
| `features.rag` | `false` | RAG + worldbook 总开关 |
| `features.tools` | `false` | 工具总开关 |
| `features.tts` | `false` | TTS 总开关 |
| `memory.top_k` | `10` | 记忆召回条数 |
| `memory.time_decay_days` | `30` | 时间衰减半衰期 |
| `rag.top_k` | `5` | 文档召回条数 |
| `rag.chunk_size` | `500` | 切分目标字符数 |
| `rag.worldbook_top_k` | `3` | worldbook 召回条数 |
| `rag.worldbook_keyword_boost` | `0.3` | 关键词命中加权 |
| `permission.default` | `confirm` | 默认决策 |
| `permission.confirm_timeout_s` | `30.0` | 确认超时秒数 |
| `tts.provider` | `edge` | TTS 提供方 |
| `tts.voice` | `zh-CN-XiaoxiaoNeural` | 音色 |
| `tts.max_chars_per_turn` | `2000` | 单轮 TTS 字符上限 |

### 切换到 Ollama

Ollama 暴露 OpenAI 兼容接口，改 `base_url` 和 `model` 即可：

```bash
export SOUL_MATE_MODEL__BASE_URL="http://localhost:11434/v1"
export SOUL_MATE_MODEL__MODEL="deepseek-r1:7b"
export SOUL_MATE_MODEL__API_KEY="ollama"   # 占位，Ollama 不校验
```

---

## 目录结构

```text
backend/
├── app/
│   ├── main.py              服务入口：装配模块，启动 HTTP / WebSocket
│   ├── config.py            配置模型与加载
│   ├── logging_config.py    日志初始化
│   ├── events.py            事件信封工具
│   ├── lifecycle.py         优雅停机管理
│   ├── api/
│   │   ├── health.py        GET /health
│   │   ├── memory.py        /memory/* 记忆审计
│   │   ├── documents.py     /documents/* 文档管理
│   │   ├── worldbook.py     /worldbook/* 设定管理
│   │   ├── ws.py            WS /ws
│   │   └── ws_sender.py     并发安全的 WebSocket 发送包装
│   ├── agent/
│   │   ├── state.py         AgentState 定义
│   │   ├── prompts.py       各专家 system prompt 构造
│   │   ├── nodes.py         supervisor_node / chat_node / info_node / tool_node
│   │   ├── graph.py         LangGraph 图构建
│   │   └── runner.py        图执行桥接，产出 AgentEvent
│   ├── models/
│   │   ├── base.py          模型适配接口
│   │   ├── openai_compatible.py  OpenAI 兼容协议适配器
│   │   └── __init__.py      build_adapter
│   ├── persona/
│   │   ├── persona.yaml     遐蝶人格设定
│   │   ├── config.py        PersonaProfile 等模型
│   │   └── loader.py        加载与 Prompt 构建
│   ├── memory/
│   │   ├── embedder.py      本地嵌入模型管理
│   │   ├── store.py         SQLite + Chroma 双写（memory_facts）
│   │   ├── extract.py       每轮异步抽取
│   │   ├── retrieve.py      三来源合并召回
│   │   ├── rag.py           文档检索
│   │   ├── documents.py     文档导入、切分、去重
│   │   ├── worldbook.py     设定条目
│   │   ├── audit.py         审计写入
│   │   └── schemas.py       API 请求/响应模型
│   ├── tools/
│   │   ├── base.py          BaseTool / ToolContext / ToolResult
│   │   ├── registry.py      工具注册表
│   │   └── builtin/
│   │       ├── get_time.py
│   │       ├── get_weather.py
│   │       └── read_file.py
│   ├── permission/
│   │   ├── decisions.py     Decision 枚举
│   │   ├── pending.py       确认挂起表
│   │   └── checker.py       权限检查
│   ├── tts/
│   │   ├── base.py          TtsAdapter / SynthesizeResult
│   │   ├── edge.py          Edge TTS 适配器
│   │   └── splitter.py      按句切分
│   └── storage/
│       ├── db_SQlite.py     SQLite 连接与初始化（异步，aiosqlite）
│       ├── schema.sql       建表 SQL
│       ├── history.py       历史加载与摘要
│       └── session.py       单用户单会话管理
├── data/                    运行数据（不入 Git）
│   ├── soul_mate.db
│   ├── soul_mate.db-wal
│   ├── soul_mate.db-shm
│   ├── chroma/              Chroma 向量库
│   ├── documents/           导入的文档原件（可选）
│   └── logs/backend.log
├── scripts/                 手动验证与运维脚本
├── config.default.yaml      默认配置
├── config.local.example.yaml 本地配置示例
├── config.local.yaml        本地配置（不入 Git）
├── pyproject.toml
├── uv.lock
└── tests/                   测试
```

---

## 接口

### HTTP

#### `GET /health`

```json
{
  "status": "ok",
  "app": "soul-mate-backend",
  "version": "0.1.0",
  "time": "2026-09-30T10:00:00+00:00",
  "uptime_s": 42,
  "checks": {
    "database": {
      "status": "ok",
      "path": "data/soul_mate.db",
      "schema_version": "1",
      "journal_mode": "wal"
    }
  }
}
```

#### 记忆审计

`features.memory=false` 时全部返回 503。

| 方法 | 路径 | 说明 |
|---|---|---|
| `GET` | `/memory/facts` | 列出记忆；`?status=active&type=preference` 过滤，不带 `status` 表示不过滤 |
| `GET` | `/memory/facts/{id}` | 查单条 |
| `PATCH` | `/memory/facts/{id}` | 编辑内容、重要性、状态 |
| `DELETE` | `/memory/facts/{id}` | 删除，默认软删，`?hard=true` 物理删 |
| `POST` | `/memory/facts/{id}/restore` | 恢复软删的记忆 |
| `GET` | `/memory/audit` | 审计记录，`?target_id=xxx` |

#### 文档管理

`features.rag=false` 时全部返回 503。

| 方法 | 路径 | 说明 |
|---|---|---|
| `GET` | `/documents` | 列出文档 |
| `GET` | `/documents/{id}` | 查单个 |
| `GET` | `/documents/{id}/chunks` | 查切分片段 |
| `POST` | `/documents` | 导入文本 |
| `POST` | `/documents/import_file?path=...` | 从服务端文件导入 |
| `PATCH` | `/documents/{id}` | 改标题或状态；`status=active` 可恢复软删的文档（与同内容活跃文档冲突 409 / 嵌入模型不可用 503） |
| `DELETE` | `/documents/{id}` | 删除，默认软删，`?hard=true` 物理删 |

导入请求体：

```json
{
  "title": "产品手册",
  "content": "本产品保修期为一年...",
  "source": "manual",
  "mime_type": "text/plain"
}
```

相同内容（SHA256 哈希）重复导入直接返回已有文档，不重复入库。

#### worldbook 管理

| 方法 | 路径 | 说明 |
|---|---|---|
| `GET` | `/worldbook` | 列出条目 |
| `GET` | `/worldbook/{id}` | 查单个 |
| `POST` | `/worldbook` | 新建 |
| `PATCH` | `/worldbook/{id}` | 编辑 |
| `DELETE` | `/worldbook/{id}` | 删除，默认软删 |

新建请求体：

```json
{
  "title": "遐蝶",
  "content": "死荫的侍女，冥河的女儿。",
  "keywords": ["遐蝶", "Castorice"],
  "importance": 0.9
}
```

### WebSocket

#### `WS /ws`

AG-UI 事件流。所有消息为 JSON 文本帧，统一信封：

```json
{
  "v": 1,
  "type": "pong",
  "request_id": "r1",
  "session_id": "default",
  "ts": "2026-09-30T10:00:00+00:00",
  "data": {}
}
```

**契约版本**由 `contracts.ws_event_version` 决定。客户端消息的 `v` 必须等于服务端版本，否则返回 `version_mismatch`。

#### 客户端 → 服务端

| type | data | 说明 |
|---|---|---|
| `ping` | `{}` | 心跳 |
| `echo` | `{text}` | 回显 |
| `text_input` | `{text}` | 文本输入，触发 Agent |
| `tool_confirm` | `{confirm_id, allowed}` | 回复工具确认 |

#### 服务端 → 客户端

| type | data | 说明 |
|---|---|---|
| `pong` | `{server_time}` | 心跳响应 |
| `echo` | `{text, received_at}` | 回显结果 |
| `run_started` | `{}` | 一轮开始 |
| `expert_selected` | `{expert}` | 本轮接手的专家（`chat` 闲聊 / `info` 信息查询），前端据此显示「正在查询信息…」这类等待提示。工具轮次回到专家时会再次发送 |
| `text_delta` | `{text}` | 流式文本片段 |
| `run_finished` | `{tokens_in, tokens_out, model, estimated_cost, currency}` | 一轮结束 |
| `tool_call_started` | `{request_id, tool_call_id, tool, args}` | 工具开始执行 |
| `tool_confirm_request` | `{request_id, tool_call_id, confirm_id, tool, args, timeout_s}` | 请求用户确认 |
| `tool_result` | `{request_id, tool_call_id, tool, ok, content, error, meta}` | 工具执行结果 |
| `tool_denied` | `{request_id, tool_call_id, tool, reason}` | 权限拒绝 |
| `tts_audio_chunk` | `{audio, format, text, index}` | 一段音频（base64） |
| `tts_finished` | `{chunks, chars}` | TTS 本轮结束 |
| `error` | `{code, message}` | 错误 |
| `server_shutdown` | `{message}` | 服务即将关闭 |

#### 错误码

| code | 说明 |
|---|---|
| `bad_json` | 消息不是合法 JSON |
| `bad_request` | 缺少必填字段，或类型不合法 |
| `version_mismatch` | 契约版本不匹配 |
| `unsupported_type` | 不支持的消息类型 |
| `internal_error` | 服务端内部错误 |
| `model_error` | 模型调用失败 |
| `recursion_limit` | 图执行步数超限 |
| `run_timeout` | 单轮执行超时 |
| `cancelled` | 生成被取消 |
| `tool_loop_limit` | 工具调用次数超限 |

---

## 测试

```bash
uv run pytest -v
```

测试使用 `tmp_path` 隔离，不碰真实 `data/`，不调真实模型。涉及嵌入模型的用例在模型不存在时自动跳过。

### 手动验证脚本

`scripts/` 下：

| 脚本 | 用途 |
|---|---|
| `smoke_model.py` | 验证模型适配器能调通 |
| `smoke_memory.py` | 验证本地嵌入与 Chroma |
| `show_persona.py` | 打印当前 system prompt |
| `ws_client_audio.py` | WebSocket 客户端，收到 TTS 自动播放 |
| `check_tables.py` | 列出 SQLite 表 |
| `check_facts.py` | 列出 memory_facts |
| `check_cost.py` | 查看 cost_log |
| `check_recall_all.py` | 验证三来源合并召回 |

### 用 wscat 聊天

```bash
npm install -g wscat
wscat -c ws://127.0.0.1:8000/ws
```

```json
{"v":1,"type":"text_input","request_id":"r1","data":{"text":"你好，你是谁"}}
```

### 听声音

```bash
$env:SOUL_MATE_FEATURES__TTS = "true"
uv run python scripts/ws_client_audio.py
```

Windows 用内置 MCI 播放，无额外依赖。

---

## 开发约定

### 单用户单会话

应用只有一个会话，`session_id` 固定为 `settings.agent.default_session_id`（默认 `default`）。客户端不传或传相同值都行，传别的值报 `bad_request`。

### 日志

- 正文用中文，技术标识符保持原样。
- 级别 `INFO` / `ERROR` 由 `logging` 模块输出，改不了也不必改。
- 全项目统一用 `get_logger("模块名")`，不直接用 `logging.getLogger`。

### 事件信封

- 契约版本唯一来源：`settings.contracts.ws_event_version`。
- 所有事件用 `app.events.make_event` 构造，不手拼字典。
- 时间用 `app.events.now_iso`，全项目只有这一处实现。

### 意图分类

- 规则（关键词 / 短输入）优先，未命中才调模型；分类只是路由优化，任何失败都回退 `chat`。
- 来源分布与耗时在 `app/agent/intent.py` 内聚合成进程内计数，每 20 次分类打一条
  `意图分类统计：…` 聚合日志；`get_intent_stats()` 可读快照，`reset_intent_stats()` 供测试清零。
- 计数是进程内的，多进程部署下各进程独立；单用户单会话场景不涉及。

### 模型调用韧性

- 限流（429）、服务端 5xx、408 与网络类错误会按 `model.max_retries` 重试，指数退避
  （`retry_base_delay_s * 2^(n-1)`）。
- **只在还没吐出可见内容时重试**：一旦已经流式发出正文或工具调用，失败就直接上报，
  否则同一段话会被发两遍。取消（`CancelledError`）不重试。
- 4xx 里的鉴权错、参数错不重试，重试只是白等。
- `connect_timeout_s` 与 `timeout_s` 分开：断网快速失败，流式生成仍能长时间等待。

### 优雅停机

`Lifecycle` 接管 `SIGINT` / `SIGTERM`：

1. 收到信号，先跑 `lifecycle.shutdown()`。
2. 通知所有 WebSocket：发 `server_shutdown`。
3. 等待后台任务，超时强制取消。
4. 关闭剩余 WebSocket（code 1001）。
5. 恢复 Uvicorn 原处理器，重新触发信号，让 Uvicorn 走自己的关闭序列。

### 停机保护三层

- **应用层**：`lifecycle.is_shutting_down`，节点入口与每个 chunk 检查。
- **框架层**：`recursion_limit`，防图跑飞。
- **资源层**：`run_timeout_s`，防单轮卡死。

### 记忆与检索

- **抽取**：每轮 `run_finished` 后后台异步跑，用模型从对话中提取偏好/事实。
- **去重**：向量距离小于 `0.15` 视为重复。
- **冲突**：`persona_first` 保留旧条目，`newest_first` 覆盖。
- **召回**：三来源合并（记忆 + worldbook + 文档），每路 min-max 归一后乘来源权重（记忆 1.0，worldbook 0.9，文档 0.8）。
- **注入**：按来源分组，顺序固定「记忆 → 设定 → 文档」。

### 文档去重

- **导入层**：`content_hash`（SHA256）唯一索引，相同内容不重复入库。
- **检索层**：按 `content.strip()` 去重，保留相似度最高的。

### 工具与权限

- 工具继承 `BaseTool`，类属性声明元数据，`run` 方法实现逻辑。
- 权限决策：`allow` / `confirm` / `deny`。`confirm` 时挂起等前端回复，超时视为拒绝。
- `permission.default` 优先级高于 `risk_level`，可用 `permission.overrides` 按工具名覆盖。

### TTS

- 按句切分：句末标点断开，从句标点在 `sentence_min_len` 之后断开。
- 纯标点句被过滤，不送 TTS。
- 串行 worker 保证音频顺序。
- 单轮字符数上限 `max_chars_per_turn`。
- 成本记入 `cost_log.tts_chars`。

### SQLite

- `journal_mode = WAL` 持久化设置，`init_db` 时一次。
- `busy_timeout` / `synchronous` / `foreign_keys` 连接级，每次 `connect` 设。
- 全部异步（`aiosqlite`），不在异步路径里跑同步 SQL。
- 写操作按语义分函数：`insert` / `insert_with_rowcount` / `execute` / `execute_many`。
- `locked` / `busy` 错误有指数退避重试兜底。

### ChromaDB

- `data/chroma/`，持久化目录。
- SQLite 是 source of truth，Chroma 是索引。
- 写入顺序：先 SQLite 拿 id，再 Chroma upsert。
- Chroma 写入失败只记 warning，可通过 `rebuild_index(settings)` 从 SQLite 重建。
- 软删的记录从 Chroma 移除。

### 模块命名

- `app/` 下按模块划分目录，一个目录一个职责。
- 配置文件类统一 `XxxConfig` 后缀，顶层入口叫 `Settings`。
- 人格档案类叫 `PersonaProfile`，与模块配置 `PersonaConfig` 区分。
- 历史遗留：`db_SQlite.py` 保留非 snake_case 名，import 时严格用 `from app.storage.db_SQlite import ...`，注意大小写（Linux 区分）。

---

## 数据存储

### SQLite

`data/soul_mate.db`，WAL 模式。表：

| 表 | 用途 |
|---|---|
| `meta` | 元信息，含 `schema_version` |
| `sessions` | 会话（单用户单会话，只有一行） |
| `messages` | 消息（user / assistant / tool） |
| `session_summaries` | 会话摘要 |
| `memory_facts` | 记忆事实 |
| `documents` | 文档元数据，含 `content_hash` |
| `document_chunks` | 文档切分片段 |
| `worldbook` | 设定条目，含 `keywords` |
| `audit_log` | 记忆 / 配置 / 工具调用变更审计 |
| `cost_log` | token 消耗、TTS 字符数、成本 |

### ChromaDB

`data/chroma/`，持久化目录。

| Collection | 用途 |
|---|---|
| `memory_facts` | 长期记忆向量 |
| `documents` | 文档片段向量 |
| `worldbook` | 设定条目向量 |

### 迁移

复制 `data/` 目录即可。`db_SQlite.py` 的 `init_db` 幂等，重复调用安全。

---

## 路线图

| 版本 | 目标 | 状态 |
|---|---|---|
| v0.1 | 服务骨架：FastAPI + WS + SQLite | ✅ |
| v0.2 | 模型接入 + 人格 + LangGraph | ✅ |
| v0.3 | 历史上下文 + 滚动摘要 | ✅ |
| v0.4 | 长期记忆：抽取、召回、注入、审计 | ✅ |
| v0.5 | 工具系统 + 权限 | ✅ |
| v0.6 | RAG + worldbook | ✅ |
| v0.7 | TTS | ✅ |
| v0.8 | 多专家节点 + 规划-执行子图 | 待做（可选） |
| v0.9 | 技能 / MCP / 定时任务 | 待做（可选） |

---

## 已知限制

- 单用户单会话，不支持多会话切换。
- 无认证，只监听本机。
- 模型走 API，断网不可用。
- 工具只有 3 个（时间、天气、读文件），天气是 mock。
- 文档只支持 UTF-8 文本，PDF / Word 后置。
- 文档导入同步执行，超大文件可能超时。
- worldbook 关键词是字面匹配，不做分词或同义扩展。
- TTS 走 Edge，偶尔不稳定，长文本有延迟。
- 音频未做本地缓存，每轮重新合成。
- 价格表默认值可能过时，以官方为准。
- Windows 上 `SIGTERM` 支持有限，主要靠 `SIGINT`。
