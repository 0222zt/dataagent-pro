"""图表编排：生成代码 → 受限执行 → 失败自愈 → 结果缓存 → 写入历史。

这是整个系统的大脑。每一步都能单独测试、单独加日志、单独换实现。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, replace
from typing import Any, Awaitable, Callable

from app.core.config import get_settings
from app.core.errors import (
    AppError,
    CodeExecutionError,
    CodeValidationError,
    InvalidInputError,
    SessionNotFoundError,
)
from app.core.logging import get_logger
from app.llm.client import LLMClient
from app.llm.prompts import build_generate_messages, build_repair_messages, extract_code
from app.security.sandbox import figure_points, run_chart_code
from app.services.dataset_store import Dataset
from app.services.history_store import get_history
from app.services.spec_service import compile_spec

logger = get_logger(__name__)

Emit = Callable[..., Awaitable[None]] | None


@dataclass(slots=True)
class ChartResult:
    chart_json: str
    code: str
    attempts: int
    cached: bool
    elapsed_ms: int
    model: str
    session_id: str
    dataset_id: str
    message_id: str = ""
    points: int = 0
    repaired: bool = False


class ResultCache:
    """按（数据指纹 + 规范化问题 + 主模型）缓存结果，同样的问题不重复烧额度。"""

    def __init__(self, ttl: int | None = None, capacity: int | None = None) -> None:
        settings = get_settings()
        self._ttl = ttl if ttl is not None else settings.result_cache_ttl
        self._capacity = capacity if capacity is not None else settings.result_cache_size
        self._items: OrderedDict[str, tuple[float, ChartResult]] = OrderedDict()
        self._lock = threading.Lock()
        self._hits = 0
        self._misses = 0

    def get(self, key: str) -> ChartResult | None:
        now = time.time()
        with self._lock:
            item = self._items.get(key)
            if item is None:
                self._misses += 1
                return None
            expires_at, result = item
            if expires_at <= now:
                self._items.pop(key, None)
                self._misses += 1
                return None
            self._items.move_to_end(key)
            self._hits += 1
        return replace(result, cached=True, elapsed_ms=0, message_id="")

    def set(self, key: str, result: ChartResult) -> None:
        with self._lock:
            while len(self._items) >= self._capacity:
                self._items.popitem(last=False)
            self._items[key] = (time.time() + self._ttl, replace(result, cached=False))

    def clear(self) -> None:
        with self._lock:
            self._items.clear()

    def stats(self) -> dict[str, Any]:
        with self._lock:
            total = self._hits + self._misses
            return {
                "entries": len(self._items),
                "capacity": self._capacity,
                "ttl_seconds": self._ttl,
                "hits": self._hits,
                "misses": self._misses,
                "hit_rate": round(self._hits / total, 3) if total else 0.0,
            }


# 模型很爱在开头补一遍 import；这些库执行器已经直接注入，重复导入只会被判违规。
_SAFE_IMPORT_RE = re.compile(
    r"^\s*(?:"
    r"import\s+(?:pandas\s+as\s+pd|numpy\s+as\s+np|"
    r"plotly\.express\s+as\s+px|plotly\.graph_objects\s+as\s+go)"
    r"(?:\s*,\s*(?:pandas\s+as\s+pd|numpy\s+as\s+np|"
    r"plotly\.express\s+as\s+px|plotly\.graph_objects\s+as\s+go))*"
    r"|from\s+plotly\s+import\s+(?:express\s+as\s+px|graph_objects\s+as\s+go)"
    r")\s*$"
)


def sanitize_code(code: str) -> str:
    """删掉无害的重复导入和 fig.show()，降低模型"习惯性写法"导致的首次失败。"""
    kept: list[str] = []
    removed = 0
    for line in code.splitlines():
        stripped = line.strip()
        if _SAFE_IMPORT_RE.match(stripped) or stripped in {"fig.show()", "fig.show ()"}:
            removed += 1
            continue
        kept.append(line)
    cleaned = "\n".join(kept).strip()
    if not cleaned:
        return code
    if removed:
        logger.debug("已移除 %s 行多余 import / fig.show()", removed)
    return cleaned


class AiService:
    """面向业务的 LLM 服务：生成代码 / 修复代码。"""

    def __init__(self, client: Any | None = None) -> None:
        self._client = client or LLMClient()
        self._settings = get_settings()

    async def generate_chart_code(
        self,
        meta: dict[str, Any],
        prompt: str,
        history: list[dict[str, str]] | None = None,
    ) -> tuple[str, str]:
        result = await self._client.achat(build_generate_messages(meta, prompt, history))
        code = sanitize_code(extract_code(result.text))
        if not code:
            raise _bad_response("模型没有返回任何代码。")
        self._log_code("生成", code)
        return code, result.model

    async def repair_chart_code(
        self,
        meta: dict[str, Any],
        prompt: str,
        code: str,
        error: str,
    ) -> tuple[str, str]:
        result = await self._client.achat(
            build_repair_messages(meta, prompt, code, error)
        )
        fixed = sanitize_code(extract_code(result.text))
        if not fixed:
            raise _bad_response("模型没有返回修正后的代码。")
        self._log_code("修复", fixed)
        return fixed, result.model

    def _log_code(self, stage: str, code: str) -> None:
        if self._settings.log_llm_code:
            logger.info("模型%s的代码：\n%s", stage, code)
        else:
            logger.debug("模型%s代码 %s 字符", stage, len(code))


def _bad_response(message: str) -> AppError:
    from app.core.errors import LLMResponseError

    return LLMResponseError(message)


class ChartService:
    def __init__(self, ai: AiService | None = None, cache: ResultCache | None = None) -> None:
        self._ai = ai or AiService()
        self._cache = cache or ResultCache()
        self._settings = get_settings()

    @property
    def cache(self) -> ResultCache:
        return self._cache

    async def generate(
        self,
        dataset: Dataset,
        prompt: str,
        *,
        session_id: str | None = None,
        refresh: bool = False,
        emit: Emit = None,
    ) -> ChartResult:
        settings = self._settings
        history_store = get_history()
        prompt = _normalize_prompt(prompt)

        session_id = self._resolve_session(history_store, dataset, prompt, session_id)

        cache_key = hashlib.sha256(
            f"{dataset.fingerprint}|{prompt}|{settings.model}".encode("utf-8")
        ).hexdigest()

        if not refresh:
            hit = self._cache.get(cache_key)
            if hit is not None:
                await _emit(emit, "cache", "命中缓存，直接复用上次结果", cached=True)
                logger.info("缓存命中，跳过模型调用")
                return replace(hit, session_id=session_id, dataset_id=dataset.dataset_id)

        started = time.perf_counter()
        try:
            history = history_store.recent_turns(session_id, settings.history_turns)
            await _emit(
                emit,
                "thinking",
                f"正在让 {settings.model} 读数据、写代码…",
                model=settings.model,
            )
            code, model = await self._ai.generate_chart_code(dataset.meta, prompt, history)
            await _emit(emit, "generated", "代码已生成，准备执行", code=code, model=model)

            max_attempts = max(1, settings.max_repair_attempts + 1)
            chart_json = ""
            attempts = 0
            last_error: Exception | None = None

            for attempt in range(1, max_attempts + 1):
                attempts = attempt
                await _emit(
                    emit, "executing", f"在受限沙箱中执行（第 {attempt} 次）", attempt=attempt
                )
                try:
                    chart_json = await asyncio.to_thread(
                        run_chart_code, code, dataset.frame, settings
                    )
                    break
                except (CodeValidationError, CodeExecutionError) as exc:
                    last_error = exc
                    logger.warning(
                        "第 %s/%s 次执行失败：%s",
                        attempt,
                        max_attempts,
                        getattr(exc, "message", exc),
                    )
                    if attempt >= max_attempts:
                        break
                    await _emit(
                        emit,
                        "repairing",
                        "代码报错了，正在把 traceback 回传给模型修复…",
                        attempt=attempt,
                        error=str(getattr(exc, "message", exc))[:400],
                    )
                    code, model = await self._ai.repair_chart_code(
                        dataset.meta, prompt, code, _error_context(exc)
                    )
                    await _emit(
                        emit, "repaired", "已生成修复版代码，重新执行", code=code, model=model
                    )

            if not chart_json:
                assert last_error is not None
                raise last_error

            elapsed_ms = int((time.perf_counter() - started) * 1000)
            points = figure_points(chart_json)
            result = ChartResult(
                chart_json=chart_json,
                code=code,
                attempts=attempts,
                cached=False,
                elapsed_ms=elapsed_ms,
                model=model,
                session_id=session_id,
                dataset_id=dataset.dataset_id,
                points=points,
                repaired=attempts > 1,
            )
            self._cache.set(cache_key, result)

            message_id = history_store.add_message(
                session_id,
                role="assistant",
                prompt=prompt,
                code=code,
                chart_json=chart_json,
                model=model,
                attempts=attempts,
                elapsed_ms=elapsed_ms,
            )
            history_store.record_metric(
                kind="generate", model=model, latency_ms=elapsed_ms, ok=True
            )
            result = replace(result, message_id=message_id)

            logger.info(
                "图表生成成功：模型=%s，尝试=%s 次，耗时=%s ms，点=%s，JSON=%s 字符",
                model,
                attempts,
                elapsed_ms,
                points,
                len(chart_json),
            )
            await _emit(
                emit,
                "done",
                "图表生成完成",
                attempts=attempts,
                cached=False,
                model=model,
                elapsed_ms=elapsed_ms,
                points=points,
            )
            return result

        except AppError as exc:
            history_store.add_message(
                session_id, role="assistant", prompt=prompt, error=exc.message
            )
            history_store.record_metric(kind="generate", ok=False)
            await _emit(emit, "error", exc.message, code=exc.code, hint=exc.hint)
            raise
        except Exception as exc:  # noqa: BLE001 - 未预期错误也要留痕
            logger.exception("图表生成出现未预期错误")
            history_store.add_message(
                session_id, role="assistant", prompt=prompt, error=str(exc)[:500]
            )
            await _emit(emit, "error", f"未预期错误：{exc}", code="INTERNAL_ERROR")
            raise

    # ------------------------------------------------------------------ #
    async def generate_from_spec(
        self,
        dataset: Dataset,
        spec: Any,
        *,
        session_id: str | None = None,
        emit: Emit = None,
    ) -> ChartResult:
        """拖字段路径：spec → 确定性代码 → 同一个沙箱执行，不调用模型。"""
        settings = self._settings
        history_store = get_history()
        compiled = compile_spec(dataset.meta, spec)
        prompt = compiled.prompt
        session_id = self._resolve_session(history_store, dataset, prompt, session_id)

        cache_key = hashlib.sha256(
            f"{dataset.fingerprint}|spec|{_spec_key(spec)}".encode("utf-8")
        ).hexdigest()
        hit = self._cache.get(cache_key)
        if hit is not None:
            await _emit(emit, "cache", "命中缓存，直接复用上次结果", cached=True)
            logger.info("spec 缓存命中，跳过执行")
            return replace(hit, session_id=session_id, dataset_id=dataset.dataset_id)

        if compiled.warnings:
            logger.info("spec 编译提示：%s", "；".join(compiled.warnings))

        started = time.perf_counter()
        try:
            await _emit(
                emit,
                "spec",
                f"已编译 {compiled.chart_type} 视觉通道，无需调用模型",
                chart=compiled.chart_type,
                title=compiled.title,
            )
            await _emit(emit, "executing", "在受限沙箱中执行编译后的代码", attempt=1)
            chart_json = await asyncio.to_thread(
                run_chart_code, compiled.code, dataset.frame, settings
            )

            elapsed_ms = int((time.perf_counter() - started) * 1000)
            points = figure_points(chart_json)
            result = ChartResult(
                chart_json=chart_json,
                code=compiled.code,
                attempts=1,
                cached=False,
                elapsed_ms=elapsed_ms,
                model="rules",
                session_id=session_id,
                dataset_id=dataset.dataset_id,
                points=points,
                repaired=False,
            )
            self._cache.set(cache_key, result)

            message_id = history_store.add_message(
                session_id,
                role="assistant",
                prompt=prompt,
                code=compiled.code,
                chart_json=chart_json,
                model="rules",
                attempts=1,
                elapsed_ms=elapsed_ms,
            )
            history_store.record_metric(
                kind="spec", model="rules", latency_ms=elapsed_ms, ok=True
            )
            result = replace(result, message_id=message_id)

            logger.info(
                "按 spec 生成成功：%s，耗时=%s ms，点=%s",
                compiled.title,
                elapsed_ms,
                points,
            )
            await _emit(
                emit,
                "done",
                "图表已生成",
                attempts=1,
                cached=False,
                model="rules",
                elapsed_ms=elapsed_ms,
                points=points,
            )
            return result

        except AppError as exc:
            history_store.add_message(
                session_id,
                role="assistant",
                prompt=prompt,
                code=compiled.code,
                error=exc.message,
            )
            history_store.record_metric(kind="spec", model="rules", ok=False)
            await _emit(emit, "error", exc.message, code=exc.code, hint=exc.hint)
            raise
        except Exception as exc:  # noqa: BLE001 - 未预期错误也要留痕
            logger.exception("按 spec 生成出现未预期错误")
            history_store.add_message(
                session_id,
                role="assistant",
                prompt=prompt,
                code=compiled.code,
                error=str(exc)[:500],
            )
            await _emit(emit, "error", f"未预期错误：{exc}", code="INTERNAL_ERROR")
            raise

    # ------------------------------------------------------------------ #
    def _resolve_session(
        self,
        history_store: Any,
        dataset: Dataset,
        prompt: str,
        session_id: str | None,
    ) -> str:
        if not session_id:
            return history_store.create_session(dataset.dataset_id, prompt[:40])["session_id"]

        session = history_store.get_session(session_id)
        if session is None:
            raise SessionNotFoundError(f"会话 {session_id} 不存在。")
        if session.get("dataset_id") != dataset.dataset_id:
            raise InvalidInputError(
                "该会话属于另一个数据集，请新建会话或上传对应文件。",
                hint="会话与数据集一一绑定，避免图表张冠李戴。",
            )
        return session_id


# --------------------------------------------------------------------------- #
# 工具
# --------------------------------------------------------------------------- #
def _spec_key(spec: Any) -> str:
    dump = getattr(spec, "model_dump_json", None)
    if callable(dump):
        try:
            return dump()
        except Exception:  # noqa: BLE001 - 退回 dict 序列化
            pass
    try:
        return json.dumps(spec, ensure_ascii=False, sort_keys=True)
    except TypeError:
        return str(spec)


def _normalize_prompt(prompt: str) -> str:
    text = " ".join((prompt or "").split())
    if not text:
        raise InvalidInputError(
            "请先描述你想看的图表，例如：按月统计销售额并画柱状图。"
        )
    limit = get_settings().max_prompt_chars
    if len(text) > limit:
        raise InvalidInputError(f"需求描述 {len(text)} 字，超过 {limit} 字上限，请精简。")
    return text


def _error_context(exc: Exception) -> str:
    if isinstance(exc, CodeExecutionError) and exc.traceback_text:
        return f"{exc.message}\n\n{exc.traceback_text}"[:2000]
    return str(getattr(exc, "message", exc))[:2000]


async def _emit(emit: Emit, stage: str, message: str, **extra: Any) -> None:
    if emit is None:
        return
    try:
        await emit(stage, message, **extra)
    except Exception:  # noqa: BLE001 - 推流失败不应该影响主流程
        logger.debug("SSE 事件推送失败", exc_info=True)


def _result_payload(result: ChartResult) -> dict[str, Any]:
    return {
        "chart_json": result.chart_json,
        "code": result.code,
        "attempts": result.attempts,
        "cached": result.cached,
        "elapsed_ms": result.elapsed_ms,
        "model": result.model,
        "session_id": result.session_id,
        "dataset_id": result.dataset_id,
        "message_id": result.message_id,
        "points": result.points,
        "repaired": result.repaired,
    }


_services: dict[str, ChartService] = {}
_service_lock = threading.Lock()


def get_chart_service() -> ChartService:
    with _service_lock:
        service = _services.get("default")
        if service is None:
            service = ChartService()
            _services["default"] = service
        return service
