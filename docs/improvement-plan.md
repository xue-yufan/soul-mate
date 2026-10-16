# Soul Mate 后续改动方案

> 2026-10-13。与 `backend-architecture.md` / `frontend-architecture.md` 并列：
> 那两份写「要做成什么样」，这份写「接下来按什么顺序做、怎么算做完」。

## 0. 前提（决定了下面的排序）

| 前提 | 影响 |
| --- | --- |
| 项目还没有版本控制 | 动大件之前先立回滚点 |
| Live2D 大概率不做，改走 3D 模型 | `pixi.js` / `untitled-pixi-live2d-engine` / `public/xiadie/` 从「预留」变成「待清理」 |
| 后端是单用户单会话（`agent.default_session_id` 贯穿 schema 与 API） | 侧边栏、多会话不做 |
| 后端只监听 `127.0.0.1`、无认证；CORS 白名单是 `"null"`（打包版）与 `localhost:5173`（开发版） | 不做远程接入；换 dev 端口要同步改 `cors_origins`（白名单是整体替换） |
| 前端只做表现层，不持有 API Key | 新增能力一律走后端接口 |

## 1. 已有基线（本次会话完成的部分）

真 bug：

- 工具卡的「好，准了 / 不必了」接上了 `resolve`（原来点了没反应）
- 中文输入法选词回车不再误发（`isComposing` / keyCode 229）
- WS 断开后不再永久卡在「生成中...」（后端断连时会掐掉本轮，前端原先不收拾）
- `expert_selected` 不再被静默丢弃，等待期显示「正在查询信息…」
- Markdown 外链交系统浏览器，不再把应用窗口导航走
- 后端 `status` / `type` 空串归一化，记忆的「全部」筛选生效（含新测试）

加固：

- HTTP 20 秒超时 + 统一 `fetchJson` 出口
- `error.message` 优先（保留后端那份带人格口吻的文案）
- 工具卡与确认弹窗在中断时落到终态
- `tts_audio_chunk.format` 真正被使用（mp3 / wav / ogg）
- 事件类型全集校验：后端新增事件会在控制台留警告
- WS 事件契约 15/15 对齐；版本号单一来源（`package.json` → `__APP_VERSION__`）

## 2. P0：先立回滚点（约 30 分钟）

- [ ] `git init` + 首次提交
- [ ] 提交前亲眼确认 `backend/config.local.yaml`（里面有 API Key）、`backend/data/`、`release/`、`out/` 不进库
- [ ] 手动过一遍四个已修场景：回复中重启后端、拼音选词回车、工具确认按钮、点回复里的链接

验收：`git diff` 能看见自己的改动，改坏了能一键回退。

## 3. P1：知识库闭环（低风险，半天到一天）

这一批全是「后端已经写完、界面够不着」，改动集中在加 UI + 调现成接口。

| 项 | 后端（现成） | 前端要改 |
| --- | --- | --- |
| 文档 / 设定的软删可见与恢复 | `?status=`、`PATCH /worldbook/{id}` 改 status、`DELETE ...?hard=true` | `DocumentsTab` / `WorldbookTab` 加状态筛选与恢复、硬删 |
| 审计记录 | `GET /memory/audit?event_type=all&target_id=`（工具调用与记忆变更都记了） | 知识面板加第 4 个 tab |
| 文件导入 | `POST /documents/import_file?path=` | 主进程加 `dialog.showOpenDialog` + IPC，前端加「从文件导入」 |
| 历史分页 | `/history/messages?limit=&offset=`，返回 `total` | `useChat.loadHistory` 改分页 + 「加载更多」 |
| 列表总数 | 三个 list 接口都返回 `total` | 现在显示的是 `items.length`，改用 `total` |

验收：删掉的文档能在界面上找回来；能导入一个真实文件；历史能翻到第 100 条以前。

## 4. P2：TTS 换成真音色（一天起，视路线而定）

第一步（装好就有变化）：`tts.voice` 已经是设置面板的可写字段（「语音」页），换音色保存即自动重启后端生效。想调音调需要补后端三处：`TtsConfig` 加 `pitch`、`edge.py` 的 `Communicate(...)` 多传一个参数、`config_schema.py` 的 FIELDS 加一条（并登记进 `_RESTART_REQUIRED_PATHS`）。

第二步（真·角色音色）：接入清单是后端 5 个触点、前端 0 改动。

1. `app/tts/<provider>.py` 实现 `synthesize(text) -> SynthesizeResult`
2. `app/tts/__init__.py` 的 `build_tts()` 加分支（现在非 `edge` 直接抛 `ValueError`）
3. `app/config.py: TtsConfig` 加 `base_url` / `ref_audio` / `prompt_text`
4. `config.local.yaml` 设 `tts.provider`（**不在设置面板白名单里**，只能改文件）
5. 想让新字段出现在面板里，再登记 `config_schema.py`

两个坑：

- 裸 PCM 浏览器放不了 `audio/pcm` 的 data URL：在适配器里套 44 字节 WAV 头，别改前端
- `tts.max_chars_per_turn` 到顶是**静默停**的，长回复的语音会断在半路且界面无提示

验收：能听到本地引擎的音色；文字与语音并行，首字延迟可接受。

## 5. P3：3D 渲染层（含试错，先做 spike 再排期）

- [ ] 选型：`three.js` + `@pixiv/three-vrm`（自带眨眼 / 表情 / 口型 blendShape，和「按句 TTS + 振幅驱动口型」对得上），或裸 glTF 自己驱动 morph target
- [ ] 先 spike：透明窗口叠 WebGL 在 Windows 上的黑边、阴影异常、点击穿透（架构文档预警过，必须早测）
- [ ] 桌面壳：透明、置顶、托盘、点击穿透、窗口拖拽（与渲染层共用同一个窗口，一起做）
- [ ] 口型：Web Audio `AnalyserNode` 取振幅 → 平滑 → morph target（不必等 3D，可先用立绘验证数值范围）
- [ ] 清理：`npm uninstall pixi.js untitled-pixi-live2d-engine`、移走 `public/xiadie/`（2.3MB 贴图会被打进安装包）、`CharacterPanel.vue` 换成 canvas 组件

验收：模型站在窗口里会眨眼；说话时口型跟音频走；窗口透明无黑边；置顶时不抢输入焦点。

## 6. P4：工程化（可穿插，随时做）

- [ ] Electron 加固：`index.html` 加 CSP、`webPreferences.sandbox`
- [ ] ESLint + Prettier（现在 `main.ts` 单引号无分号，其余文件双引号带分号）
- [ ] 前端测试：给 `useChat` 的事件归并、`useWebSocket` 的重连、`ToolConfirmDialog` 的倒计时写 vitest（后端 171 个用例，前端 0）
- [ ] 成本显示 / 日志入口：**需要后端先开接口**（`cost_log` 与 `data/logs/backend.log` 目前只能直接读文件）

## 7. 暂缓 / 不做

| 项 | 原因 |
| --- | --- |
| 任务页、定时任务 | 后端对应模块还是「预留」 |
| 侧边栏、多会话 | 后端单会话假设贯穿 schema 与 API，改造大、收益低 |
| 「打断」（取消生成） | 需要后端先加 `cancel` 协议消息，前端才有按钮可挂 |
| MCP / 技能系统 | 后端路线图 v0.9，未做 |
| 远程后端接入 | 后端无认证且只监听本机 |

## 8. 待决策

1. **「全部」是否包含已删除**：现在包含（测试已钉住）。若希望「全部 = 活跃 + 归档，不含已删除」，需要显式取值（如 `?status=all`）并同步改测试。
2. **音色来源**：本地克隆还是云端 API。用真人声优的音色涉及声音权，自用可以，别公开发布或商用。
3. **3D 模型从哪来**：自建 / 购买 / 开源，决定渲染层做到多细。
4. **要不要「打断」**：架构文档里的状态机状态，目前协议不支持。

## 9. 需要后端配合的清单

| 需求 | 后端动作 | 前端受益 |
| --- | --- | --- |
| 成本显示 | 新增 `GET /cost/summary`（读 `cost_log`） | 设置页显示累计花费与预算 |
| 日志入口 | 新增 `GET /logs` 或返回日志文件路径 | 设置页「打开日志」 |
| 打断生成 | WS 客户端消息加 `cancel` | 生成中可中断 |
| TTS 音色列表 | 包一层 `edge_tts.list_voices()`（需联网） | 音色从文本框变下拉框 |
| 新 TTS 提供方 | 新增适配器 + `build_tts` 分支 | 真音色 |
| `tts.pitch` | 配置字段 + 传给 `Communicate` | 音调可调 |

## 10. 沉淀：已查证过的事实（避免重复踩）

- **连断即终止**：`ws.py` 的 `finally` 会 `_cancel_and_drain(turn_task)`，断线后本轮必死，前端必须自己收尾状态。
- **`expert_selected`**：后端在专家节点入口发一次、工具轮次回到专家时再发，用途就是等待提示。
- **`status` 有两种约定**：`/memory/facts` 不带参数 = 不过滤；`/documents`、`/worldbook` 不带参数 = 只看 active，`?status=` = 不过滤。改之前先看对应 api client 的注释。
- **成本只存在于 `cost_log`**（按 `message_id` 存 token、成本、tts 字符数），历史接口不返回，所以刷新后每条消息的成本显示为 0。
- **配置落盘位置**：设置面板写的是 `config.overrides.yaml`，位于后端目录旁——只有 per-user 安装（`perMachine: false`）才可写。
- **事件契约以 `backend/README.md` 为准**；`frontend-architecture.md` §8.1 那张 WS 事件表（`state`、`memory_update` 等）是被实现取代的旧草案，别照它实现。
- **测试现状**：后端 171 个用例全绿（`backend/.venv/Scripts/python.exe -m pytest`），前端还没有测试框架。
- **回退点**：`frontend/_stage/patch-backend/backup/` 存着本次后端改动的改前副本。
