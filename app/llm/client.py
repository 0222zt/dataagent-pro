"""OpenAI 兼容的模型客户端。

关键设计：

* **多模型降级**：主模型 429 / 5xx / 超时后自动切到下一档免费模型；
* **鉴权失败不重试**：401/403 直接给出可操作的提示，避免白等；
* **不阻塞事件循环**：同步 requests 放进线程池；
* **用量可观测**：每次调用的 tokens、耗时、成功与否都进统计。
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Sequence

import requests

from app.core.config import Settings, get_settings
from app.core.errors import AuthError, LLMError, LLMResponseError
from app.core.logging import get_logger

logger = get_logger(__name__)

_RETRYABLE_STATUS = {408, 409, 425, 429, 500, 502, 503, 504}
_SWITCH_MODEL_STATUS = {400, 404, 422}


@dataclass(slots=True)
class ChatResult:
    text: str
    model: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_ms: int = 0
    attempts: int = 1


@dataclass(slots=True)
class UsageTracker:
    """线程安全的轻量用量统计（进程内）。"""

    calls: int = 0
    failures: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_latency_ms: int = 0
    by_model: dict[str, int] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def record(
        self,
        model: str,
        *,
        ok: bool,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        latency_ms: int = 0,
    ) -> None:
        with self._lock:
            self.calls += 1
            if not ok:
                self.failures += 1
            self.prompt_tokens += max(0, prompt_tokens)
            self.completion_tokens += max(0, completion_tokens)
            self.total_latency_ms += max(0, latency_ms)
            self.by_model[model] = self.by_model.get(model, 0) + 1

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            avg = self.total_latency_ms / self.calls if self.calls else 0
            return {
                "calls": self.calls,
                "failures": self.failures,
                "prompt_tokens": self.prompt_tokens,
                "completion_tokens": self.completion_tokens,
                "total_tokens": self.prompt_tokens + self.completion_tokens,
                "avg_latency_ms": int(avg),
                "by_model": dict(self.by_model),
            }


GLOBAL_USAGE = UsageTracker()


class LLMClient:
    """可注入 transport / sleep 的轻量客户端（测试不需要联网）。"""

    def __init__(
        self,
        settings: Settings | None = None,
        transport: Callable[..., Any] | None = None,
        sleep: Callable[[float], None] | None = None,
        usage: UsageTracker | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._transport = transport or requests.post
        self._sleep = sleep or time.sleep
        self.usage = usage or GLOBAL_USAGE

    # ------------------------------------------------------------------ #
    # 公开接口
    # ------------------------------------------------------------------ #
    def chat(
        self,
        messages: Sequence[dict[str, str]],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> ChatResult:
        settings = self._settings
        if not settings.has_api_key and settings.provider != "ollama":
            raise AuthError(
                "未配置大模型 API Key。",
                hint="复制 .env.example 为 .env，把 DATAAGENT_API_KEY 填成你的 SiliconFlow Key。",
            )

        errors: list[str] = []
        for model in settings.model_chain:
            result = self._run_model(model, messages, temperature, max_tokens, errors)
            if result is not None:
                return result

        raise LLMError(
            "所有模型都调用失败：" + (" | ".join(errors[:4]) or "未知原因"),
            hint="免费模型可能正在限流，稍后重试；或在 .env 里调整 DATAAGENT_MODEL。",
        )

    async def achat(
        self,
        messages: Sequence[dict[str, str]],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> ChatResult:
        import asyncio

        return await asyncio.to_thread(
            self.chat, messages, temperature=temperature, max_tokens=max_tokens
        )

    # ------------------------------------------------------------------ #
    # 内部实现
    # ------------------------------------------------------------------ #
    def _run_model(
        self,
        model: str,
        messages: Sequence[dict[str, str]],
        temperature: float | None,
        max_tokens: int | None,
        errors: list[str],
    ) -> ChatResult | None:
        settings = self._settings
        payload: dict[str, Any] = {
            "model": model,
            "messages": list(messages),
            "temperature": settings.temperature if temperature is None else temperature,
            "stream": False,
        }
        if max_tokens:
            payload["max_tokens"] = max_tokens

        headers = {"Content-Type": "application/json"}
        if settings.api_key:
            headers["Authorization"] = f"Bearer {settings.api_key}"

        attempts = max(1, settings.llm_max_retries + 1)
        for attempt in range(1, attempts + 1):
            started = time.perf_counter()
            try:
                response = self._transport(
                    settings.base_url,
                    headers=headers,
                    json=payload,
                    timeout=settings.llm_timeout,
                )
            except requests.RequestException as exc:
                latency = int((time.perf_counter() - started) * 1000)
                self.usage.record(model, ok=False, latency_ms=latency)
                errors.append(f"{model} 网络异常({type(exc).__name__})")
                if not self._backoff(attempt, attempts):
                    break
                continue

            latency = int((time.perf_counter() - started) * 1000)
            status = response.status_code

            if status == 200:
                try:
                    content, prompt_tokens, completion_tokens = _parse_response(response)
                except LLMResponseError as exc:
                    self.usage.record(model, ok=False, latency_ms=latency)
                    errors.append(f"{model} 响应异常({exc.message[:60]})")
                    break
                self.usage.record(
                    model,
                    ok=True,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    latency_ms=latency,
                )
                logger.info(
                    "模型 %s 调用成功：%s tokens，%s ms（第 %s 次尝试）",
                    model,
                    prompt_tokens + completion_tokens,
                    latency,
                    attempt,
                )
                return ChatResult(
                    text=content,
                    model=model,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    latency_ms=latency,
                    attempts=attempt,
                )

            snippet = (response.text or "")[:200].replace("\n", " ")
            self.usage.record(model, ok=False, latency_ms=latency)

            if status in (401, 403):
                raise AuthError(
                    f"鉴权失败（HTTP {status}）。",
                    hint="API Key 无效、过期或没有该模型权限，请到平台重新生成。",
                    details={"model": model, "status": status},
                )

            if status == 429:
                errors.append(f"{model} 限流(429)")
                logger.warning("模型 %s 被限流，准备重试/切换", model)
                if not self._backoff(attempt, attempts):
                    break
                continue

            if status in _RETRYABLE_STATUS:
                errors.append(f"{model} HTTP {status}")
                if not self._backoff(attempt, attempts):
                    break
                continue

            if status in _SWITCH_MODEL_STATUS:
                errors.append(f"{model} HTTP {status}: {snippet}")
                break

            errors.append(f"{model} HTTP {status}: {snippet}")
            break

        return None

    def _backoff(self, attempt: int, attempts: int) -> bool:
        """还有下一次尝试吗？有就退避等待。"""
        if attempt >= attempts:
            return False
        self._sleep(min(0.6 * (2 ** (attempt - 1)), 3.0))
        return True


def _parse_response(response: Any) -> tuple[str, int, int]:
    try:
        data = response.json()
    except ValueError as exc:
        raise LLMResponseError("大模型返回的不是合法 JSON。") from exc

    choices = data.get("choices") or []
    if not choices:
        raise LLMResponseError(f"响应缺少 choices：{str(data)[:160]}")

    message = choices[0].get("message") or {}
    content = message.get("content")
    if isinstance(content, list):
        content = "".join(
            part.get("text", "") if isinstance(part, dict) else str(part) for part in content
        )
    if not isinstance(content, str) or not content.strip():
        raise LLMResponseError("大模型返回了空内容。")

    usage = data.get("usage") or {}

    def as_int(value: Any) -> int:
        try:
            return max(0, int(value))
        except (TypeError, ValueError):
            return 0

    return content.strip(), as_int(usage.get("prompt_tokens")), as_int(usage.get("completion_tokens"))
