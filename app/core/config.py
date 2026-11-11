"""集中式配置。

原则：**代码里不出现任何密钥、地址、限额的魔法值**。
所有可变项通过环境变量（前缀 ``DATAAGENT_``）或项目根目录的 ``.env`` 注入。
``.env`` 已被 ``.gitignore`` 忽略，密钥泄露时只需要换环境变量。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from app.llm.catalog import DEFAULT_BASE_URL, DEFAULT_PROVIDER, preset, preset_models

BASE_DIR = Path(__file__).resolve().parents[2]
ENV_FILE = BASE_DIR / ".env"
STATIC_DIR = BASE_DIR / "static"
SAMPLE_DIR = BASE_DIR / "sample_data"
DEMO_FILE = SAMPLE_DIR / "demo_sales.csv"
DATA_DIR = BASE_DIR / "data"
UPLOAD_DIR = DATA_DIR / "uploads"
EXPORT_DIR = DATA_DIR / "exports"
CACHE_DIR = DATA_DIR / "cache"
DB_PATH = DATA_DIR / "history.sqlite3"


def _load_env_file(path: Path) -> None:
    """极简 .env 解析器：已存在的系统环境变量优先。"""
    if not path.is_file():
        return
    for raw_line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            os.environ.setdefault(key, value)


def _env_str(name: str, default: str = "") -> str:
    value = os.environ.get(name)
    return default if value is None or not value.strip() else value.strip()


def _env_int(name: str, default: int) -> int:
    try:
        return int(_env_str(name, str(default)))
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(_env_str(name, str(default)))
    except ValueError:
        return default


def _env_bool(name: str, default: bool) -> bool:
    return _env_str(name, "1" if default else "0").lower() in {"1", "true", "yes", "on"}


def _env_list(name: str, default: tuple[str, ...] = ()) -> tuple[str, ...]:
    raw = _env_str(name, "")
    if not raw:
        return default
    return tuple(item.strip() for item in raw.split(",") if item.strip())


@dataclass(frozen=True, slots=True)
class Settings:
    """运行期配置快照（不可变，便于测试整体替换）。"""

    provider: str
    api_key: str
    base_url: str
    model: str
    fallback_models: tuple[str, ...]

    temperature: float
    llm_timeout: float
    llm_max_retries: int
    max_repair_attempts: int
    history_turns: int

    max_file_size: int
    max_rows: int
    max_prompt_chars: int
    max_code_chars: int
    max_chart_json_chars: int

    exec_timeout: float
    max_loop_iterations: int

    dataset_ttl: int
    max_datasets: int

    result_cache_ttl: int
    result_cache_size: int

    auth_token: str
    access_code: str
    demo_enabled: bool
    rate_limit_per_min: int
    # 模型调用配额：0 = 不限制。拖字段出图不经过这里，额度只被自然语言消耗。
    llm_rate_limit_per_min: int
    llm_daily_limit: int
    cors_origins: tuple[str, ...]

    log_level: str
    log_llm_code: bool

    @property
    def has_api_key(self) -> bool:
        return bool(self.api_key)

    @property
    def has_access_code(self) -> bool:
        return bool(self.access_code)

    @property
    def max_file_size_mb(self) -> float:
        return round(self.max_file_size / 1024 / 1024, 1)

    @property
    def masked_key(self) -> str:
        if not self.api_key:
            return "<未配置>"
        if len(self.api_key) <= 12:
            return "***"
        return f"{self.api_key[:6]}***{self.api_key[-4:]}"

    @property
    def model_chain(self) -> tuple[str, ...]:
        """去重后的模型链：主模型 → 兜底模型。"""
        chain: list[str] = []
        for item in (self.model, *self.fallback_models):
            name = (item or "").strip()
            if name and name not in chain:
                chain.append(name)
        return tuple(chain)

    def require_api_key(self) -> str:
        if not self.has_api_key:
            raise RuntimeError(
                "未配置大模型 API Key：复制 .env.example 为 .env，"
                "填写 DATAAGENT_API_KEY 后重启。"
            )
        return self.api_key


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """读取配置（进程内只解析一次）。"""
    _load_env_file(ENV_FILE)

    provider = _env_str("DATAAGENT_PROVIDER", DEFAULT_PROVIDER).lower()
    preset_data = preset(provider)
    preset_models_list = preset_models(provider)

    default_base = preset_data.get("base_url") or DEFAULT_BASE_URL
    base_url = _env_str("DATAAGENT_BASE_URL", default_base)

    default_model = preset_models_list[0] if preset_models_list else "Qwen/Qwen3-8B"
    model = _env_str("DATAAGENT_MODEL", default_model)

    fallback_default = tuple(preset_models_list[1:])
    fallback_models = _env_list("DATAAGENT_FALLBACK_MODELS", fallback_default)

    return Settings(
        provider=provider,
        api_key=_env_str("DATAAGENT_API_KEY", ""),
        base_url=base_url,
        model=model,
        fallback_models=fallback_models,
        temperature=_env_float("DATAAGENT_TEMPERATURE", 0.1),
        llm_timeout=_env_float("DATAAGENT_LLM_TIMEOUT", 90.0),
        llm_max_retries=_env_int("DATAAGENT_LLM_MAX_RETRIES", 1),
        max_repair_attempts=_env_int("DATAAGENT_MAX_REPAIR_ATTEMPTS", 2),
        history_turns=_env_int("DATAAGENT_HISTORY_TURNS", 3),
        max_file_size=_env_int("DATAAGENT_MAX_FILE_SIZE", 100 * 1024 * 1024),
        max_rows=_env_int("DATAAGENT_MAX_ROWS", 200_000),
        max_prompt_chars=_env_int("DATAAGENT_MAX_PROMPT_CHARS", 2000),
        max_code_chars=_env_int("DATAAGENT_MAX_CODE_CHARS", 8_000),
        max_chart_json_chars=_env_int("DATAAGENT_MAX_CHART_JSON_CHARS", 6 * 1024 * 1024),
        exec_timeout=_env_float("DATAAGENT_EXEC_TIMEOUT", 10.0),
        max_loop_iterations=_env_int("DATAAGENT_MAX_LOOP_ITERATIONS", 1_000_000),
        dataset_ttl=_env_int("DATAAGENT_DATASET_TTL", 24 * 3600),
        max_datasets=_env_int("DATAAGENT_MAX_DATASETS", 30),
        result_cache_ttl=_env_int("DATAAGENT_RESULT_CACHE_TTL", 1800),
        result_cache_size=_env_int("DATAAGENT_RESULT_CACHE_SIZE", 128),
        auth_token=_env_str("DATAAGENT_AUTH_TOKEN", ""),
        access_code=_env_str("DATAAGENT_ACCESS_CODE", ""),
        demo_enabled=_env_bool("DATAAGENT_DEMO", True),
        rate_limit_per_min=_env_int("DATAAGENT_RATE_LIMIT", 60),
        llm_rate_limit_per_min=_env_int("DATAAGENT_LLM_RATE_LIMIT", 0),
        llm_daily_limit=_env_int("DATAAGENT_LLM_DAILY_LIMIT", 0),
        cors_origins=_env_list("DATAAGENT_CORS_ORIGINS", ()),
        log_level=_env_str("DATAAGENT_LOG_LEVEL", "INFO").upper(),
        log_llm_code=_env_bool("DATAAGENT_LOG_LLM_CODE", False),
    )


def ensure_dirs() -> None:
    """启动时确保运行目录存在。"""
    for path in (DATA_DIR, UPLOAD_DIR, EXPORT_DIR, CACHE_DIR):
        path.mkdir(parents=True, exist_ok=True)
