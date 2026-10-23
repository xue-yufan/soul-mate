from pathlib import Path
from typing import List, Optional

from pydantic import BaseModel, Field, SecretStr, ValidationError
from pydantic_settings import (
    BaseSettings,
    SettingsConfigDict,
    YamlConfigSettingsSource,
)

# ---------- 配置文件名 ----------
#
# 三者都是相对"当前工作目录"（后端启动目录）。常量放在这里是为了让
# 加载方（下面的 settings_customise_sources）和写入方（app/config_schema.py）
# 共用同一份路径，不会各写一份而对不上。

CONFIG_DEFAULT_FILE = Path("config.default.yaml")
CONFIG_LOCAL_FILE = Path("config.local.yaml")
# 设置面板写入的文件，优先级高于 config.local.yaml。
# 单独放一个文件、而不是回写 config.local.yaml，是为了保住用户在那个文件里
# 手写的注释和排版（yaml.safe_dump 会把注释全部丢掉）。
CONFIG_OVERRIDES_FILE = Path("config.overrides.yaml")


class AppConfig(BaseModel):
    """应用元信息：名称、版本、运行环境"""

    name: str = "soul-mate-backend"
    version: str = "0.1.0"
    env: str = "dev"


class ServerConfig(BaseModel):
    """HTTP / WebSocket 服务监听配置"""

    host: str = "127.0.0.1"
    port: int = Field(default=8000, ge=1, le=65535)
    ws_path: str = "/ws"
    cors_origins: List[str] = []
    shutdown_timeout_s: float = 10.0
    ws_notify_delay_s: float = 0.3


class LoggingConfig(BaseModel):
    """日志配置：级别、是否输出到控制台、日志文件路径"""

    level: str = "INFO"
    console: bool = True
    max_bytes: int = 10 * 1024 * 1024
    backup_count: int = 5


class DatabaseConfig(BaseModel):
    """SQLite 数据库连接配置"""

    timeout_s: int = 5


class PathsConfig(BaseModel):
    """统一路径管理：数据目录、SQLite、日志、Chroma、文档、嵌入模型等

    除 data_dir 与 embed_model_dir 外的路径都是从 data_dir 派生的只读 property，
    写进 YAML 不会生效（pydantic 的 extra=ignore 会静默丢弃），
    需要换位置请改 data_dir。
    """

    data_dir: Path = Path("./data")

    @property
    def sqlite_path(self) -> Path:
        return self.data_dir / "soul_mate.db"

    @property
    def log_dir(self) -> Path:
        return self.data_dir / "logs"

    @property
    def log_file(self) -> Path:
        return self.log_dir / "backend.log"

    @property
    def chroma_dir(self) -> Path:
        return self.data_dir / "chroma"

    @property
    def documents_dir(self) -> Path:
        return self.data_dir / "documents"

    embed_model_dir: Optional[Path] = None


class ContractsConfig(BaseModel):
    """前后端契约定义

    只保留真正被读取的字段：ws_event_version 会写进每个 WS 事件，
    前端据此判断协议版本。原先还有 tool_schema_version、
    memory_fact_schema_version、message_role_enum、permission_decision_enum，
    但没有任何一行代码读过它们，已删除——留着一个不生效的开关比没有更糟。
    """

    ws_event_version: int = 1


class FeaturesConfig(BaseModel):
    """功能开关：控制各模块是否加载

    这是**唯一**的功能闸门。原先各模块还各有一个 `*.enabled`
    （tools/rag/tts），但代码里从来只读 features.*，那些字段是死的，
    而且 config.local.yaml 里 features 全为 true、*.enabled 全为 false，
    互相矛盾，用户按 YAML 去关 TTS 会改到死键上。已删除。
    """

    tools: bool = False
    vision: bool = True
    memory: bool = False
    rag: bool = False
    tts: bool = False


class ModelConfig(BaseModel):
    """模型提供方配置：适配器、Base URL、API Key、模型名、采样参数等"""

    adapter: str = "openai_compatible"
    provider: str = "deepseek"
    base_url: str = "https://api.deepseek.com"
    api_key: SecretStr = SecretStr("")
    model: str = "deepseek-chat"
    # 读取超时：两段流式内容之间最多等多久。流式生成间隔可能很长，别设太小
    timeout_s: int = 60
    # 连接超时：连不上时快速失败，别让断网把一整轮拖到 run_timeout_s
    connect_timeout_s: float = 10.0
    # 限流 / 5xx / 网络抖动时的重试次数。只在「还没吐出任何内容」时重试，
    # 已经流式发出内容的失败不重来（否则同一段话会发两遍）
    max_retries: int = 2
    # 重试退避基数（秒）：0.5 → 0.5、1、2 …
    retry_base_delay_s: float = 0.5
    max_tokens: int = 2048
    temperature: float = 0.7
    # 原有一个 stream 字段，但适配器里是硬编码 "stream": True 的，
    # 改它不会有任何效果，已删除。


class PersonaConfig(BaseModel):
    """人格配置

    原有一个 enabled 字段，但 main.py 里是无条件 `PersonaStore(file).load()` 的，
    从来没有读过它——也就是说把 enabled 改成 false 并不会关掉人格，
    反而会因为人格文件缺失而在启动时抛 FileNotFoundError。
    留着这种"看起来是保险丝、实际不接线的开关"比没有更危险，已删除。
    想让这个开关真正生效，需要同时改 main.py 的装配与 persona_prompt 的取值。
    """

    file: Path = Path("./app/persona/persona.yaml")


class AgentConfig(BaseModel):
    """Agent 配置

    history_turns / summary_trigger_tokens / summary_keep_recent_turns
    过去的代码默认值是 0，含义是"不加载历史、不触发摘要"。也就是说
    config.local.yaml 里少写一行，对话会静默退化成无记忆状态且不报错。
    这里改成项目实际在用的值，让兜底默认值也是安全的。
    """

    history_turns: int = 6
    summary_trigger_tokens: int = 3000
    summary_keep_recent_turns: int = 3
    max_tool_steps: int = 5
    recursion_limit: int = 10
    run_timeout_s: int = 120
    default_session_id: str = "default"
    # 原有一个 graph 字段，但 main.py 是直接 build_graph() 的，
    # 没有任何代码读过它，已删除。


class ModelPriceConfig(BaseModel):
    """单个模型的价格，单位 CNY / 1K token"""

    input_per_1k: float = 0.0
    output_per_1k: float = 0.0


class CostConfig(BaseModel):
    """成本与预算配置

    注意：budget 与 warn_ratio 目前只被 /config/public 读出来展示，
    没有任何代码据此拦截或告警（见 app/api/ws.py 的 _estimate_cost）。
    prices 里没有的模型名会静默按 0 成本计算。
    """

    currency: str = "CNY"
    budget: float = 30.0
    warn_ratio: float = 0.8
    prices: dict[str, ModelPriceConfig] = {
        "deepseek-chat": ModelPriceConfig(
            input_per_1k=0.001, output_per_1k=0.002
        ),
        "deepseek-reasoner": ModelPriceConfig(
            input_per_1k=0.004, output_per_1k=0.016
        ),
    }


class EmbeddingConfig(BaseModel):
    """嵌入模型配置，模型目录走 paths.embed_model_dir

    原有一个 adapter 字段（默认 local_sentence_transformer），
    但 Embedder 是直接读 paths.embed_model_dir 的，没有任何代码读过它，已删除。
    """

    device: str = "cpu"
    batch_size: int = 16
    normalize: bool = True


class MemoryConfig(BaseModel):
    """长期记忆配置，总开关走 features.memory

    原有两个字段也是死的：summarize_on_session_end（会话结束摘要）
    与 audit_enabled（审计开关）——没有任何代码读过它们，审计实际是常开的。
    已删除；要做这两个功能请连同实现一起加回来。
    """

    extract_per_turn: bool = True
    top_k: int = 10
    min_importance: float = 0.3
    time_decay_days: int = 30
    conflict_policy: str = "persona_first"    # persona_first / newest_first


class ToolsConfig(BaseModel):
    """工具系统配置，总开关走 features.tools"""

    allowed_dirs: List[str] = []        # read_file 白名单，空表示不限
    # 原有一个 enabled 字段，但代码里只读 features.tools，已删除。


class VisionConfig(BaseModel):
    """本机视觉观察配置。

    视觉模型只负责看图并返回观察摘要，不参与人格回复。默认连接 Ollama 的
    本机接口；模型不存在或服务未启动时，摄像头工具会返回可降级的错误。
    """

    base_url: str = "http://127.0.0.1:11434"
    model: str = "qwen3-vl:4b"
    timeout_s: float = 120.0
    capture_timeout_s: float = 20.0
    max_image_bytes: int = 2_000_000
    num_ctx: int = 4096
    num_predict: int = 1024
    keep_alive: str = "2m"


class PermissionConfig(BaseModel):
    """权限控制配置"""

    default: str = "confirm"        # allow / confirm / deny
    overrides: dict[str, str] = {}  # 按工具名覆盖
    confirm_timeout_s: float = 30.0


class RagConfig(BaseModel):
    """RAG 与 worldbook 配置。总开关走 features.rag。"""

    top_k: int = 5                       # 文档检索条数
    min_similarity: float = 0.3          # 最低相似度
    chunk_size: int = 500                # 切分目标字符数
    chunk_overlap: int = 100             # 相邻块重叠
    max_chunks_per_doc: int = 500        # 单文档最多切多少块
    worldbook_top_k: int = 3
    worldbook_keyword_boost: float = 0.3  # 关键词命中加权
    # 原有一个 enabled 字段，但代码里只读 features.rag，已删除。


class TtsConfig(BaseModel):
    """TTS 配置，总开关走 features.tts"""

    provider: str = "edge"           # edge / gpt_sovits
    voice: str = "zh-CN-XiaoxiaoNeural"
    rate: str = "+0%"                # 语速，Edge 格式
    volume: str = "+0%"              # 音量
    sentence_min_len: int = 4        # 短于此长度的句子不单独合成
    sentence_max_len: int = 120      # 超过则强制断句
    max_chars_per_turn: int = 2000   # 单轮 TTS 字符上限

    # ---------- 非 edge 提供方（本地引擎走 HTTP）----------
    # build_tts 目前只认 edge；这几个字段是给 http 适配器（app/tts/http_base.py）用的。
    # 想出现在设置面板，还要登记进 config_schema.py 的 FIELDS。
    base_url: str = ""               # 例如 http://127.0.0.1:9880
    ref_audio: str = ""              # 参考音频路径（音色克隆用）
    prompt_text: str = ""            # 参考音频对应的文本
    timeout_s: float = 60.0          # 单句合成超时；本地模型首次加载慢，给足
    engine_dir: str = ""             # 本地引擎目录（要有 pyproject.toml）；空 = 不自动拉起

    # ---------- 声音的起伏（GPT-SoVITS 的采样参数）----------
    # 默认值 = 引擎默认值（音色最稳）；想更有起伏试 1.2~1.4，代价是音色会飘
    temperature: float = 1.0
    top_k: int = 15
    top_p: float = 1.0

    # ---------- 按情绪换参考音频 ----------
    # 目录约定：<情绪>.wav + <情绪>.txt（情绪名见 app/agent/emotion.py）；
    # 挑不到就回落 ref_audio + prompt_text，所以没备素材时行为不变
    ref_audio_dir: str = ""
    # 原有 enabled（代码只读 features.tts）与 format（适配器硬编码 mp3，
    # 前端也硬编码 audio/mp3）两个字段都是死的，已删除


def _yaml_files() -> list[Path]:
    """
    返回要加载的 YAML 文件列表，按优先级从低到高

    - config.default.yaml：项目自带
    - config.local.yaml：开发者本地（可选）
    - config.overrides.yaml：设置面板写入（可选）
    - SOUL_MATE_USER_CONFIG 指到的文件：打包版用户配置（可选）

    这里读模块级常量而不是字面量，测试才能把它们指到临时目录；
    写入方（app/config_schema.py）读的是同一份常量，两边不会各写一份而对不上。
    """

    import os

    files = [
        CONFIG_DEFAULT_FILE,
        CONFIG_LOCAL_FILE,
        CONFIG_OVERRIDES_FILE,
    ]

    user_cfg = os.environ.get("SOUL_MATE_USER_CONFIG")
    if user_cfg:
        files.append(Path(user_cfg))

    return files


class Settings(BaseSettings):
    """
    全局配置入口

    通过 settings_customise_sources 指定加载顺序：
    1. 代码默认值
    2. config.default.yaml（出厂完整参考）
    3. config.local.yaml（本机覆盖，只需写要改的字段）
    4. config.overrides.yaml（设置面板写入，优先级最高）
    5. 环境变量（覆盖全部 YAML）

    所有子配置以嵌套模型形式组织，支持环境变量 SOUL_MATE_ 前缀 + __ 分隔
    """

    schema_version: int = 1
    app: AppConfig = AppConfig()
    server: ServerConfig = ServerConfig()
    logging: LoggingConfig = LoggingConfig()
    paths: PathsConfig = PathsConfig()
    database: DatabaseConfig = DatabaseConfig()
    contracts: ContractsConfig = ContractsConfig()
    features: FeaturesConfig = FeaturesConfig()
    model: ModelConfig = ModelConfig()
    persona: PersonaConfig = PersonaConfig()
    agent: AgentConfig = AgentConfig()
    cost: CostConfig = CostConfig()
    embedding: EmbeddingConfig = EmbeddingConfig()
    memory: MemoryConfig = MemoryConfig()
    tools: ToolsConfig = ToolsConfig()
    vision: VisionConfig = VisionConfig()
    permission: PermissionConfig = PermissionConfig()
    rag: RagConfig = RagConfig()
    tts: TtsConfig = TtsConfig()

    model_config = SettingsConfigDict(
        env_prefix="SOUL_MATE_",
        env_nested_delimiter="__",
        case_sensitive=False,
        extra="ignore",
        env_file=None
    )

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls,
        init_settings,
        env_settings,
        dotenv_settings,
        file_secret_settings,
    ):
        """
        自定义配置源顺序

        把 YAML 放在环境变量之后，保证环境变量能覆盖 YAML。
        优先级：init > env > dotenv > file_secret > YAML(default+local+overrides) > 代码默认值。

        YAML 说明：
        - 显式指定 utf-8 编码，避免在中文 Windows（GBK locale）下读取含中文注释的 YAML 时解码失败。
        - deep_merge=True，保证后加载的文件只写要覆盖的字段即可，同段内未写的字段仍沿用前一个文件。
        - 顺序即优先级：overrides 在最后，所以设置面板写入的值会盖住 config.local.yaml；
          想长期手写配置，应该改 config.local.yaml，并注意同名字段会被 overrides 覆盖。
        - 文件缺失时该源被跳过，不影响启动（三个文件都不存在就走代码默认值）。
        """
        yaml_source = YamlConfigSettingsSource(
            settings_cls,
            yaml_file=_yaml_files(),
            yaml_file_encoding="utf-8",
            deep_merge=True,
        )
        return (
            init_settings,
            env_settings,
            dotenv_settings,
            file_secret_settings,
            yaml_source,
        )


def load_settings() -> Settings:
    """
    加载并校验配置

    成功返回 Settings 实例；
    校验失败时抛出 SystemExit，并打印详细的 ValidationError 信息
    """
    try:
        return Settings()
    except ValidationError as e:
        raise SystemExit(f"配置校验失败：\n{e}")
