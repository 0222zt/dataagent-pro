"""免费模型目录与供应商预设。

选型原则（2026-11 核验）：

* **默认 DeepSeek-V4.1-Flash**：SiliconFlow 免费档里的新一代旗舰，代码生成
  与指令遵循明显强于 8B 级，复杂图表一次写对的概率更高；
* **兜底 Qwen/Qwen3-8B**：中文 + 代码均衡，8B 级延迟低；
* **兜底 Qwen/Qwen3.5-4B**：免费档里限速最宽松（80k TPM），主模型 429 时最快接上；
* **兜底 DeepSeek-R1-0528-Qwen3-8B**：带思维链，代码报错自愈时更可靠；
* **兜底 GLM-4-9B-0414**：中文语义好，作为最后一层。

价格与免费名单会变，部署前请以控制台
<https://cloud.siliconflow.cn/pricing> 为准；本项目把模型链全部做成配置项，
改 ``DATAAGENT_MODEL`` / ``DATAAGENT_FALLBACK_MODELS`` 即可，不需要动代码。
"""

from __future__ import annotations

from typing import Any

DEFAULT_PROVIDER = "siliconflow"
DEFAULT_BASE_URL = "https://api.siliconflow.cn/v1/chat/completions"

PROVIDERS: dict[str, dict[str, Any]] = {
    "siliconflow": {
        "label": "SiliconFlow 硅基流动（国内直连，免费档模型丰富）",
        "base_url": DEFAULT_BASE_URL,
        "requires_key": True,
        "models": [
            {
                "id": "deepseek-ai/DeepSeek-V4.1-Flash",
                "free": True,
                "role": "默认：免费档旗舰，代码一次写对率最高",
            },
            {
                "id": "Qwen/Qwen3-8B",
                "free": True,
                "role": "兜底：中文 + 代码均衡，8B 级延迟低",
            },
            {
                "id": "Qwen/Qwen3.5-4B",
                "free": True,
                "role": "兜底：限速最宽松，响应最快",
            },
            {
                "id": "deepseek-ai/DeepSeek-R1-0528-Qwen3-8B",
                "free": True,
                "role": "兜底：带思维链，适合修代码",
            },
            {
                "id": "THUDM/GLM-4-9B-0414",
                "free": True,
                "role": "兜底：中文语义好",
            },
        ],
    },
    "openrouter": {
        "label": "OpenRouter（海外，:free 模型有每日限额）",
        "base_url": "https://openrouter.ai/api/v1/chat/completions",
        "requires_key": True,
        "models": [
            {"id": "qwen/qwen3-coder:free", "free": True, "role": "代码专用"},
            {"id": "deepseek/deepseek-chat-v3-0324:free", "free": True, "role": "通用"},
            {
                "id": "meta-llama/llama-3.3-70b-instruct:free",
                "free": True,
                "role": "通用兜底",
            },
        ],
    },
    "groq": {
        "label": "Groq（免费额度，速度极快）",
        "base_url": "https://api.groq.com/openai/v1/chat/completions",
        "requires_key": True,
        "models": [
            {"id": "llama-3.3-70b-versatile", "free": True, "role": "通用"},
            {"id": "qwen/qwen3-32b", "free": True, "role": "中文 / 代码"},
        ],
    },
    "ollama": {
        "label": "Ollama 本地（完全免费，需先 ollama pull）",
        "base_url": "http://127.0.0.1:11434/v1/chat/completions",
        "requires_key": False,
        "models": [
            {"id": "qwen2.5-coder:7b", "free": True, "role": "本地代码模型"},
            {"id": "qwen3:8b", "free": True, "role": "本地通用模型"},
        ],
    },
    "custom": {
        "label": "自定义 OpenAI 兼容端点",
        "base_url": "",
        "requires_key": True,
        "models": [],
    },
}


def preset(provider: str) -> dict[str, Any]:
    """取供应商预设，未知供应商回落到 SiliconFlow。"""
    return PROVIDERS.get((provider or "").strip().lower(), PROVIDERS[DEFAULT_PROVIDER])


def preset_models(provider: str) -> list[str]:
    return [item["id"] for item in preset(provider).get("models", [])]


def model_catalog() -> list[dict[str, Any]]:
    """给前端 / ``/api/models`` 用的完整目录。"""
    catalog: list[dict[str, Any]] = []
    for name, item in PROVIDERS.items():
        catalog.append(
            {
                "provider": name,
                "label": item["label"],
                "base_url": item["base_url"],
                "requires_key": item["requires_key"],
                "models": item["models"],
            }
        )
    return catalog
