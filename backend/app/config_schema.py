"""设置面板的可写配置白名单

这里是"前端到底能改什么"的唯一事实来源：字段、类型、取值范围、分组都声明在这，
由 GET /config/public 下发给前端，前端据此渲染表单。

为什么要白名单而不是直接反射 Settings：
- 绝大多数字段是部署参数（端口、CORS、数据目录、日志切分…），不该出现在面板里；
- 反射出来的表单无法表达取值范围与枚举；
- 白名单能明确挡住越权写入，未列出的路径一律 400。

写入目标是 config.overrides.yaml，不是用户的 config.local.yaml。
原因见 app/config.py 里 CONFIG_OVERRIDES_FILE 的注释：safe_dump 会把
用户手写的注释全部抹掉，而 config.local.yaml 里恰好有很多说明性注释。

文案约定（这个面板是给使用者看的，不是给开发者看的）：
- 不出现文件名、字段路径、协议名、token 这类内部术语；
- 下拉框不要直接显示 allow / confirm / persona_first 之类的英文枚举值，
  统一走 option_labels 提供中文说法。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Sequence

import yaml

from app import config as app_config
from app.config import Settings

Kind = Literal["bool", "int", "float", "str", "select", "secret", "lines"]


class ConfigValueError(ValueError):
    """配置值非法（路径不在白名单、类型不对、超出范围）→ API 层映射成 400"""


@dataclass(frozen=True)
class GroupSpec:
    """设置面板的一个分组（对应界面上的一个页签）

    title 会被压缩成两个字以排在一行里，所以完整含义靠 description 补上。
    """

    id: str
    title: str
    description: str = ""


@dataclass(frozen=True)
class FieldSpec:
    """一个可写字段"""

    path: str
    label: str
    group: str
    kind: Kind
    help: str = ""
    unit: str = ""
    min: float | None = None
    max: float | None = None
    step: float | None = None
    options: tuple[str, ...] = ()
    # 枚举值 → 界面文案。缺省时前端直接显示枚举值本身。
    option_labels: dict[str, str] = field(default_factory=dict)


GROUPS: tuple[GroupSpec, ...] = (
    GroupSpec(
        "voice",
        "语音",
        "控制后端要不要合成语音。本机是否播放由「本机」页决定，两者互不影响。",
    ),
    GroupSpec("model", "模型", "用哪个模型，以及怎么连上它。"),
    GroupSpec("memory", "记忆", "她长期记住什么，以及能不能翻你的资料。"),
    GroupSpec("tools", "工具", "她能调用什么，以及动手前要不要先问你。"),
    GroupSpec("agent", "对话", "每轮带多少历史，什么时候开始压缩。"),
)


FIELDS: tuple[FieldSpec, ...] = (
    # ---------- 语音 ----------
    FieldSpec(
        "features.tts", "启用语音合成", "voice", "bool",
        help="关掉后她就不再开口，本机的播放开关也就无事可做。",
    ),
    FieldSpec(
        "tts.provider", "语音引擎", "voice", "select",
        options=("edge", "gpt_sovits"),
        option_labels={
            "edge": "Edge（在线，开箱即用）",
            "gpt_sovits": "GPT-SoVITS（本地，需自备服务）",
        },
        help="切换后需要重启后端。GPT-SoVITS 需要先在本机跑起推理服务。",
    ),
    FieldSpec(
        "tts.base_url", "本地引擎地址", "voice", "str",
        help="仅 GPT-SoVITS 使用，例如 http://127.0.0.1:9880。",
    ),
    FieldSpec(
        "tts.voice", "音色", "voice", "str",
        help="填语音服务支持的音色名，例如 zh-CN-XiaoxiaoNeural（晓晓）。",
    ),
    FieldSpec("tts.rate", "语速", "voice", "str", help="例如 +0%、-10%、+20%。"),
    FieldSpec("tts.volume", "合成音量", "voice", "str", help="例如 +0%、-20%。"),
    FieldSpec(
        "tts.timeout_s", "单句合成超时", "voice", "float",
        unit="秒", min=1, max=600, step=5,
        help="本地模型首次加载慢，给足时间；一般 60 秒够用。",
    ),
    FieldSpec(
        "tts.max_chars_per_turn", "单轮字数上限", "voice", "int",
        unit="字", min=0, max=100000, step=100,
        help="一轮回复超过这个长度就不再合成语音。",
    ),
    FieldSpec(
        "tts.ref_audio", "参考音频", "voice", "str",
        help="音色克隆用的参考音频绝对路径（Windows 用正斜杠），例如 D:/GPT-SoVITS/ref.wav。",
    ),
    FieldSpec(
        "tts.prompt_text", "参考音频文本", "voice", "str",
        help="必须与参考音频逐字一致，否则音色会飘。",
    ),
    FieldSpec(
        "tts.engine_dir", "本地引擎目录", "voice", "str",
        help="填了才会在后端启动时自动拉起引擎（后台跑，不拖慢启动）；留空就自己起。",
    ),
    FieldSpec(
        "tts.temperature", "声音起伏", "voice", "float",
        min=0.1, max=2.0, step=0.05,
        help="越高越有起伏，也越容易飘；默认 1.0 最稳，想更生动试 1.2~1.4。",
    ),
    FieldSpec("tts.top_k", "采样 top-k", "voice", "int", min=1, max=100),
    FieldSpec(
        "tts.top_p", "采样 top-p", "voice", "float", min=0.0, max=1.0, step=0.05,
    ),
    FieldSpec(
        "tts.ref_audio_dir", "情绪参考音频目录", "voice", "str",
        help="放 <情绪>.wav + <情绪>.txt（如 happy.wav/happy.txt）；缺就回落「参考音频」。",
    ),
    # ---------- 模型与成本 ----------
    FieldSpec(
        "model.model", "模型名", "model", "str",
        help="要和所用服务商提供的名称一致，例如 deepseek-chat。",
    ),
    FieldSpec(
        "model.base_url", "接口地址", "model", "str",
        help="服务商的 API 地址，一般不用改。",
    ),
    FieldSpec(
        "model.temperature", "回答随机性", "model", "float",
        min=0, max=2, step=0.1,
        help="越低越稳定、越高越有变化，一般保持 0.7。",
    ),
    FieldSpec(
        "model.max_tokens", "单次回答长度上限", "model", "int",
        min=1, max=65536, step=128,
        help="一次回复最多生成多少内容，太小会中途截断。",
    ),
    FieldSpec(
        "model.timeout_s", "请求超时", "model", "int",
        unit="秒", min=1, max=600, step=5,
        help="超过这个时间还没响应就放弃这次请求。",
    ),
    FieldSpec(
        "model.api_key", "API 密钥", "model", "secret",
        help="只在保存时写入，之后永不显示；留空表示不修改。",
    ),
    FieldSpec(
        "cost.currency", "金额单位", "model", "select",
        options=("CNY", "USD"),
        option_labels={"CNY": "人民币（CNY）", "USD": "美元（USD）"},
        help="只影响每条回复下面显示的金额单位。",
    ),
    # ---------- 记忆与知识库 ----------
    FieldSpec(
        "features.memory", "长期记忆", "memory", "bool",
        help="开启后她会把值得记住的事长期存下来。",
    ),
    FieldSpec(
        "memory.extract_per_turn", "每轮自动整理记忆", "memory", "bool",
        help="每次对话后自动判断哪些内容值得记住。",
    ),
    FieldSpec("memory.top_k", "每次回忆几条", "memory", "int", min=1, max=50),
    FieldSpec(
        "memory.min_importance", "值得记住的最低门槛", "memory", "float",
        min=0, max=1, step=0.05,
        help="低于这个重要性的事情不会被长期保存。",
    ),
    FieldSpec(
        "memory.time_decay_days", "记忆淡忘周期", "memory", "int",
        unit="天", min=1, max=3650,
        help="越久以前的记忆权重越低，这里是权重减半所需的天数。",
    ),
    FieldSpec(
        "memory.conflict_policy", "记忆冲突时", "memory", "select",
        options=("persona_first", "newest_first"),
        option_labels={
            "persona_first": "以人格设定为准",
            "newest_first": "以最新说法为准",
        },
    ),
    FieldSpec(
        "features.rag", "资料库检索", "memory", "bool",
        help="让她能翻你导入的文档与世界书；首次开启要准备本地模型，启动会慢十几秒。",
    ),
    FieldSpec("rag.top_k", "每次引用几段文档", "memory", "int", min=1, max=50),
    FieldSpec(
        "rag.min_similarity", "引用门槛", "memory", "float",
        min=0, max=1, step=0.05,
        help="调高更严格（宁缺毋滥），调低更宽松。",
    ),
    FieldSpec(
        "rag.chunk_size", "文档切块长度", "memory", "int",
        unit="字", min=100, max=4000, step=50,
        help="导入文档时按这个长度切分，一般不用改。",
    ),
    FieldSpec(
        "rag.chunk_overlap", "相邻切块重叠", "memory", "int",
        unit="字", min=0, max=1000, step=10,
        help="避免答案正好落在两块的交界处，一般不用改。",
    ),
    FieldSpec(
        "rag.worldbook_top_k", "每次引用几条世界书", "memory", "int",
        min=0, max=20,
    ),
    # ---------- 工具与权限 ----------
    FieldSpec(
        "features.tools", "工具调用", "tools", "bool",
        help="允许她使用读文件、看时间这类工具。",
    ),
    FieldSpec(
        "tools.allowed_dirs", "允许她读取的目录", "tools", "lines",
        help="每行一个绝对路径；留空表示不限制。",
    ),
    FieldSpec(
        "permission.default", "默认权限", "tools", "select",
        options=("allow", "confirm", "deny"),
        option_labels={
            "allow": "直接执行",
            "confirm": "每次询问",
            "deny": "一律拒绝",
        },
        help="选「每次询问」最安全。",
    ),
    FieldSpec(
        "permission.confirm_timeout_s", "确认超时", "tools", "float",
        unit="秒", min=5, max=600, step=5,
        help="超过这个时间没有回应，就按拒绝处理。",
    ),
    FieldSpec(
        "agent.max_tool_steps", "单轮最多用几次工具", "tools", "int",
        unit="次", min=1, max=20,
    ),
    # ---------- 对话与上下文 ----------
    FieldSpec(
        "agent.history_turns", "每轮带几段历史", "agent", "int",
        unit="轮", min=0, max=50,
        help="带得越多越连贯，也越费额度。",
    ),
    FieldSpec(
        "agent.summary_trigger_tokens", "多长之后开始压缩", "agent", "int",
        min=0, max=100000, step=100,
        help="对话累积到这个长度会自动摘要成一段；0 表示不压缩。",
    ),
    FieldSpec(
        "agent.summary_keep_recent_turns", "压缩后保留最近几轮", "agent", "int",
        unit="轮", min=0, max=50,
        help="最近的对话原样保留，不参与摘要。",
    ),
)


BY_PATH: dict[str, FieldSpec] = {spec.path: spec for spec in FIELDS}


def overrides_path() -> Path:
    """当前 overrides 文件路径

    每次调用都重新读 app.config 上的常量（而不是 import 时取一次值），
    这样测试可以把它指到临时目录，避免把用例的写入落到项目里。
    """

    return app_config.CONFIG_OVERRIDES_FILE


# --------------------------------------------------------------------------
# 取值
# --------------------------------------------------------------------------


def resolve_value(settings: Settings, path: str) -> Any:
    """按点分路径从已加载的 Settings 里取值（SecretStr 不解包）"""

    node: Any = settings
    for part in path.split("."):
        node = getattr(node, part, None)
        if node is None:
            return None
    # 密钥永远不外传：只告诉前端"有没有设置"
    if hasattr(node, "get_secret_value"):
        return None
    return node


def _is_set(settings: Settings, path: str) -> bool:
    node: Any = settings
    for part in path.split("."):
        node = getattr(node, part, None)
        if node is None:
            return False
    if hasattr(node, "get_secret_value"):
        return bool(node.get_secret_value())
    return True


def _fmt(number: float) -> str:
    return f"{number:g}"


def coerce_value(spec: FieldSpec, raw: Any) -> Any:
    """把前端传来的原始值校验并转换成目标类型，失败抛 ConfigValueError"""

    if spec.kind == "bool":
        if isinstance(raw, bool):
            return raw
        if isinstance(raw, str) and raw.strip().lower() in {"true", "false"}:
            return raw.strip().lower() == "true"
        raise ConfigValueError(f"{spec.label} 只能是「开」或「关」")

    if spec.kind in {"int", "float"}:
        if isinstance(raw, bool):
            raise ConfigValueError(f"{spec.label} 需要填数字")
        try:
            number = int(raw) if spec.kind == "int" else float(raw)
        except (TypeError, ValueError):
            wanted = "整数" if spec.kind == "int" else "数字"
            raise ConfigValueError(f"{spec.label} 需要{wanted}") from None
        if spec.min is not None and number < spec.min:
            raise ConfigValueError(f"{spec.label} 不能小于 {_fmt(spec.min)}")
        if spec.max is not None and number > spec.max:
            raise ConfigValueError(f"{spec.label} 不能大于 {_fmt(spec.max)}")
        return number

    if spec.kind == "select":
        text = str(raw).strip()
        if text not in spec.options:
            allowed = " / ".join(
                spec.option_labels.get(option, option) for option in spec.options
            )
            raise ConfigValueError(f"{spec.label} 只能是：{allowed}")
        return text

    if spec.kind == "lines":
        if isinstance(raw, str):
            items = raw.splitlines()
        elif isinstance(raw, (list, tuple)):
            items = [str(item) for item in raw]
        else:
            raise ConfigValueError(f"{spec.label} 需要一行一个路径")
        return [item.strip() for item in items if item.strip()]

    # str / secret
    text = str(raw).strip()
    if not text:
        raise ConfigValueError(f"{spec.label} 不能为空")
    return text


# --------------------------------------------------------------------------
# config.overrides.yaml 读写
# --------------------------------------------------------------------------

_HEADER = (
    "# 本文件由设置面板自动写入（PATCH /config），请不要手改——下一次保存会覆盖它。\n"
    "# 它的优先级高于 config.local.yaml：这里出现的键会盖住你在 local 里写的值。\n"
    "# 想长期手写配置，请改 config.local.yaml，并在设置面板里把对应项恢复默认。\n"
)


def read_overrides() -> dict[str, Any]:
    """读取当前的 overrides；文件不存在或为空返回 {}"""

    path = overrides_path()
    if not path.exists():
        return {}

    try:
        loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ConfigValueError(f"读取配置文件失败：{exc}") from exc

    if loaded is None:
        return {}
    if not isinstance(loaded, dict):
        raise ConfigValueError("配置文件顶层必须是键值映射")
    return loaded


def write_overrides(data: dict[str, Any]) -> None:
    """原子写入 overrides；data 为空时直接删掉文件"""

    path = overrides_path()

    if not data:
        if path.exists():
            path.unlink()
        return

    body = yaml.safe_dump(
        data, allow_unicode=True, sort_keys=False, default_flow_style=False
    )
    tmp = path.parent / (path.name + ".tmp")
    tmp.write_text(_HEADER + body, encoding="utf-8")
    os.replace(tmp, path)


def _set_nested(data: dict[str, Any], dotted: str, value: Any) -> None:
    parts = dotted.split(".")
    node = data
    for part in parts[:-1]:
        child = node.get(part)
        if not isinstance(child, dict):
            child = {}
            node[part] = child
        node = child
    node[parts[-1]] = value


def _delete_nested(data: dict[str, Any], dotted: str) -> bool:
    """删掉某个键，并顺手清掉因此变空的分组"""

    parts = dotted.split(".")
    parents: list[tuple[dict[str, Any], str]] = []
    node = data
    for part in parts[:-1]:
        child = node.get(part)
        if not isinstance(child, dict):
            return False
        parents.append((node, part))
        node = child

    if parts[-1] not in node:
        return False
    del node[parts[-1]]

    for parent, key in reversed(parents):
        if isinstance(parent.get(key), dict) and not parent[key]:
            del parent[key]
        else:
            break
    return True


def apply_patch(
    values: dict[str, Any], reset: Sequence[str] = ()
) -> dict[str, Any]:
    """校验并落盘一次修改，返回写入后的完整 overrides"""

    if not values and not reset:
        raise ConfigValueError("没有需要修改的字段")

    unknown = sorted({p for p in [*values, *reset] if p not in BY_PATH})
    if unknown:
        raise ConfigValueError(f"不支持的配置项：{', '.join(unknown)}")

    locked = {p: env_lock(p) for p in [*values, *reset]}
    blocked = {p: name for p, name in locked.items() if name is not None}
    if blocked:
        detail = "；".join(
            f"{BY_PATH[p].label}（{name}）" for p, name in sorted(blocked.items())
        )
        raise ConfigValueError(
            f"以下配置当前由环境变量固定，改配置文件不会生效：{detail}。"
            "打包版请改 userData 下的 settings.json，或先清掉该环境变量。"
        )

    data = read_overrides()

    for path in reset:
        _delete_nested(data, path)

    for path, raw in values.items():
        spec = BY_PATH[path]
        _set_nested(data, path, coerce_value(spec, raw))

    write_overrides(data)
    return data


# --------------------------------------------------------------------------
# 下发给前端的描述
# --------------------------------------------------------------------------


def build_settings_payload(settings: Settings) -> dict[str, Any]:
    """构造 /config/public 里的 settings 段：分组 + 字段 + 当前值"""

    fields: list[dict[str, Any]] = []
    for spec in FIELDS:
        item: dict[str, Any] = {
            "path": spec.path,
            "label": spec.label,
            "group": spec.group,
            "kind": spec.kind,
            "help": spec.help,
            "unit": spec.unit,
            "min": spec.min,
            "max": spec.max,
            "step": spec.step,
            "options": list(spec.options),
            "option_labels": dict(spec.option_labels),
            "value": resolve_value(settings, spec.path),
            "locked_by": env_lock(spec.path),
        }
        if spec.kind == "secret":
            item["is_set"] = _is_set(settings, spec.path)
        fields.append(item)

    return {
        "groups": [
            {"id": g.id, "title": g.title, "description": g.description}
            for g in GROUPS
        ],
        "fields": fields,
        "overrides_file": str(overrides_path()),
        # 所有配置都在 lifespan 里读一次（app.state.settings），
        # 因此任何一项改动都需要重启后端才生效。
        "restart_required": True,
    }


# --------------------------------------------------------------------------
# 环境变量锁定检测
# --------------------------------------------------------------------------
#
# 配置源顺序是 env > YAML，所以某个键一旦有对应的 SOUL_MATE_* 环境变量，
# 写进 config.overrides.yaml 也不会有任何效果。
#
# 这不是理论问题：打包版的主进程会把 userData/settings.json 翻译成
# SOUL_MATE_MODEL__API_KEY / SOUL_MATE_FEATURES__TTS 等环境变量下发
# （见 electron/userConfig.ts 的 userConfigToEnv），恰好覆盖了设置面板最想管的
# 那几个键。若不做检测，用户在打包版里改完、重启，值会被悄悄顶回去——正是
# 我们这次要消灭的"改了没用"。
#
# 因此：这些字段在面板里标成"被环境变量固定"，写入请求直接拒绝并说明去哪里改。


def env_var_name(path: str) -> str:
    return "SOUL_MATE_" + path.replace(".", "__").upper()


def env_lock(path: str) -> str | None:
    """该路径被哪个环境变量固定；未被固定返回 None"""

    wanted = env_var_name(path).upper()
    for key in os.environ:
        if key.upper() == wanted:
            return key
    return None
