"""HTTP 接口层：参数校验、错误映射、响应组装，不放业务逻辑。"""

from __future__ import annotations

import asyncio
import hmac
import threading
from typing import Any

from fastapi import APIRouter, File, Request, Response, UploadFile
from fastapi.responses import FileResponse, StreamingResponse

from app import __version__
from app.api.schemas import (
    AccessRequest,
    ChatRequest,
    ChatResponse,
    DatasetInfo,
    ErrorResponse,
    ExportRequest,
    ExportResponse,
    HealthResponse,
    SessionDetail,
    SessionInfo,
    SpecRequest,
)
from app.core.config import DEMO_FILE, get_settings
from app.core.errors import (
    AccessDeniedError,
    AppError,
    DatasetNotFoundError,
    EmptyDatasetError,
    FileTooLargeError,
    InvalidInputError,
)
from app.core.logging import get_logger, request_id_var
from app.core.middleware import ACCESS_COOKIE, access_cookie_value
from app.core.sse import sse_comment, sse_pack
from app.llm.catalog import model_catalog
from app.llm.client import GLOBAL_USAGE
from app.services.chart_service import ChartResult, get_chart_service
from app.services.data_service import (
    describe_frame,
    fingerprint_frame,
    load_table,
    preview_records,
)
from app.services.dataset_store import get_store
from app.services.export_service import (
    resolve_export,
    save_artifact,
    to_csv,
    to_html,
    to_png,
    to_report,
)
from app.services.history_store import get_history
from app.services.profile_service import suggest_questions, summary_text
from app.services.spec_service import recommend_specs

router = APIRouter()
logger = get_logger(__name__)

_CHUNK = 64 * 1024

# 演示数据只解析一次，所有访客复用同一个 dataset_id；过期后自动重建。
_demo_lock = threading.Lock()
_demo_dataset_id: str | None = None


# --------------------------------------------------------------------------- #
# 工具
# --------------------------------------------------------------------------- #
async def _read_limited(file: UploadFile, limit: int) -> bytes:
    """边读边判大小，避免先把 500MB 读进内存再拒绝。"""
    chunks: list[bytes] = []
    size = 0
    while chunk := await file.read(_CHUNK):
        size += len(chunk)
        if size > limit:
            raise FileTooLargeError(
                f"文件超过 {limit / 1024 / 1024:.1f} MB 限制。",
                details={"limit_bytes": limit},
            )
        chunks.append(chunk)
    if size == 0:
        raise EmptyDatasetError("上传的文件是空的。")
    return b"".join(chunks)


def _dataset_payload(dataset: Any, *, demo: bool = False) -> DatasetInfo:
    meta = dataset.meta
    return DatasetInfo(
        dataset_id=dataset.dataset_id,
        filename=dataset.filename,
        rows=dataset.rows,
        columns=dataset.columns,
        meta=meta,
        preview=preview_records(dataset.frame),
        expires_in=dataset.ttl_remaining,
        summary=summary_text(meta),
        suggestions=suggest_questions(meta),
        demo=demo,
    )


def _chat_payload(result: ChartResult) -> ChatResponse:
    return ChatResponse(
        chart_json=result.chart_json,
        code=result.code,
        attempts=result.attempts,
        cached=result.cached,
        elapsed_ms=result.elapsed_ms,
        model=result.model,
        session_id=result.session_id,
        message_id=result.message_id,
        points=result.points,
        repaired=result.repaired,
        request_id=request_id_var.get(),
    )


# --------------------------------------------------------------------------- #
# 系统
# --------------------------------------------------------------------------- #
@router.get("/health", response_model=HealthResponse, summary="健康检查")
async def health() -> HealthResponse:
    settings = get_settings()
    return HealthResponse(
        version=__version__,
        provider=settings.provider,
        model=settings.model,
        model_chain=list(settings.model_chain),
        api_key_configured=settings.has_api_key,
        demo_enabled=settings.demo_enabled,
        access_required=settings.has_access_code,
        max_file_mb=settings.max_file_size_mb,
        llm_rate_limit=settings.llm_rate_limit_per_min,
        llm_daily_limit=settings.llm_daily_limit,
        datasets=get_store().stats(),
        cache=get_chart_service().cache.stats(),
        history=get_history().stats(),
        usage=GLOBAL_USAGE.snapshot(),
    )


@router.post("/access", summary="用访问码换取 HttpOnly Cookie")
async def access(payload: AccessRequest, request: Request, response: Response) -> dict[str, Any]:
    settings = get_settings()
    if not settings.has_access_code:
        return {"status": "ok", "required": False}
    if not hmac.compare_digest(
        payload.code.strip().encode("utf-8"), settings.access_code.encode("utf-8")
    ):
        logger.warning("访问码错误：ip=%s", request.client.host if request.client else "unknown")
        raise AccessDeniedError("访问码不正确。")

    secure = (
        request.url.scheme == "https"
        or request.headers.get("x-forwarded-proto", "").split(",")[0].strip() == "https"
    )
    response.set_cookie(
        ACCESS_COOKIE,
        access_cookie_value(settings.access_code),
        max_age=7 * 24 * 3600,
        httponly=True,
        samesite="lax",
        secure=secure,
        path="/",
    )
    return {"status": "ok", "required": True}


@router.get("/models", summary="免费模型目录与当前选择")
async def list_models() -> dict[str, Any]:
    settings = get_settings()
    return {
        "current": {
            "provider": settings.provider,
            "model": settings.model,
            "chain": list(settings.model_chain),
            "base_url": settings.base_url,
            "api_key_configured": settings.has_api_key,
        },
        "providers": model_catalog(),
        "note": "免费名单会调整，部署前以 https://cloud.siliconflow.cn/pricing 为准。",
    }


@router.get("/metrics", summary="运行指标")
async def metrics() -> dict[str, Any]:
    settings = get_settings()
    return {
        "datasets": get_store().stats(),
        "cache": get_chart_service().cache.stats(),
        "history": get_history().stats(),
        "usage": GLOBAL_USAGE.snapshot(),
        "model_chain": list(settings.model_chain),
    }


# --------------------------------------------------------------------------- #
# 数据集
# --------------------------------------------------------------------------- #
@router.post(
    "/demo",
    response_model=DatasetInfo,
    summary="载入内置示例数据（公网演示首屏）",
)
async def demo_dataset() -> DatasetInfo:
    global _demo_dataset_id
    settings = get_settings()
    if not settings.demo_enabled:
        raise InvalidInputError("演示模式未开启。", hint="设置 DATAAGENT_DEMO=1 后重启。")
    if not DEMO_FILE.is_file():
        raise InvalidInputError(
            "示例数据文件缺失。",
            hint=f"确认 {DEMO_FILE.name} 位于 sample_data 目录。",
        )

    with _demo_lock:
        if _demo_dataset_id:
            try:
                return _dataset_payload(get_store().get(_demo_dataset_id), demo=True)
            except DatasetNotFoundError:
                _demo_dataset_id = None

        content = DEMO_FILE.read_bytes()
        filename = "示例数据_销售明细.csv"
        frame = load_table(filename, content)
        dataset = get_store().put(
            filename=filename,
            content=content,
            frame=frame,
            meta=describe_frame(frame),
            fingerprint=fingerprint_frame(frame),
        )
        _demo_dataset_id = dataset.dataset_id

    logger.info("演示数据集已载入：%s", dataset.dataset_id[:8])
    return _dataset_payload(dataset, demo=True)


@router.post(
    "/datasets",
    response_model=DatasetInfo,
    summary="上传表格，返回 dataset_id、数据画像与推荐问题",
    responses={413: {"model": ErrorResponse}, 415: {"model": ErrorResponse}},
)
async def upload_dataset(file: UploadFile = File(...)) -> DatasetInfo:
    settings = get_settings()
    filename = file.filename or "upload.csv"
    content = await _read_limited(file, settings.max_file_size)
    frame = load_table(filename, content)
    meta = describe_frame(frame)
    dataset = get_store().put(
        filename=filename,
        content=content,
        frame=frame,
        meta=meta,
        fingerprint=fingerprint_frame(frame),
    )
    logger.info(
        "数据集已登记：%s（%s 行 × %s 列）",
        dataset.dataset_id[:8],
        dataset.rows,
        dataset.columns,
    )
    return _dataset_payload(dataset)


@router.get("/datasets/{dataset_id}", response_model=DatasetInfo, summary="查看数据集画像")
async def get_dataset(dataset_id: str) -> DatasetInfo:
    return _dataset_payload(get_store().get(dataset_id))


@router.get(
    "/datasets/{dataset_id}/specs",
    summary="按列类型推荐视觉通道配置（不调用模型）",
)
async def dataset_specs(dataset_id: str) -> list[dict[str, Any]]:
    dataset = get_store().get(dataset_id)
    return recommend_specs(dataset.meta)


@router.delete("/datasets/{dataset_id}", summary="释放数据集")
async def delete_dataset(dataset_id: str) -> dict[str, Any]:
    removed = get_store().discard(dataset_id)
    return {"status": "ok", "removed": removed}


# --------------------------------------------------------------------------- #
# 会话
# --------------------------------------------------------------------------- #
@router.post("/sessions", response_model=SessionInfo, summary="新建会话")
async def create_session(payload: dict[str, Any]) -> SessionInfo:
    dataset_id = str(payload.get("dataset_id") or "")
    dataset = get_store().get(dataset_id)
    session = get_history().create_session(dataset.dataset_id, str(payload.get("title") or ""))
    return SessionInfo(**session)


@router.get("/sessions", response_model=list[SessionInfo], summary="会话列表")
async def list_sessions(limit: int = 30) -> list[SessionInfo]:
    return [SessionInfo(**item) for item in get_history().list_sessions(limit)]


@router.get("/sessions/{session_id}", response_model=SessionDetail, summary="会话详情")
async def get_session(session_id: str) -> SessionDetail:
    session = get_history().get_session(session_id)
    if session is None:
        from app.core.errors import SessionNotFoundError

        raise SessionNotFoundError(f"会话 {session_id} 不存在。")
    return SessionDetail(**session)


@router.delete("/sessions/{session_id}", summary="删除会话")
async def delete_session(session_id: str) -> dict[str, Any]:
    return {"status": "ok", "removed": get_history().delete_session(session_id)}


# --------------------------------------------------------------------------- #
# 生成
# --------------------------------------------------------------------------- #
@router.post(
    "/chat",
    response_model=ChatResponse,
    summary="根据 dataset_id + 自然语言需求生成图表",
    responses={502: {"model": ErrorResponse}, 504: {"model": ErrorResponse}},
)
async def chat(payload: ChatRequest) -> ChatResponse:
    dataset = get_store().get(payload.dataset_id)
    result = await get_chart_service().generate(
        dataset,
        payload.prompt,
        session_id=payload.session_id,
        refresh=payload.refresh,
    )
    return _chat_payload(result)


@router.post("/chat/stream", summary="SSE 流式生成：阶段进度 + 最终结果")
async def chat_stream(payload: ChatRequest) -> StreamingResponse:
    dataset = get_store().get(payload.dataset_id)
    queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

    async def emit(stage: str, message: str, **extra: Any) -> None:
        await queue.put({"type": "stage", "stage": stage, "message": message, **extra})

    async def worker() -> None:
        try:
            result = await get_chart_service().generate(
                dataset,
                payload.prompt,
                session_id=payload.session_id,
                refresh=payload.refresh,
                emit=emit,
            )
            await queue.put(
                {"type": "result", "data": _chat_payload(result).model_dump()}
            )
        except AppError as exc:
            await queue.put(
                {
                    "type": "error",
                    "error": {**exc.to_payload(), "request_id": request_id_var.get()},
                }
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("流式生成出现未预期错误")
            await queue.put(
                {
                    "type": "error",
                    "error": {
                        "status": "error",
                        "code": "INTERNAL_ERROR",
                        "message": f"服务器内部错误：{exc}"[:300],
                        "request_id": request_id_var.get(),
                    },
                }
            )
        finally:
            await queue.put({"type": "end"})

    async def event_stream():
        yield sse_comment("stream opened")
        while True:
            item = await queue.get()
            yield sse_pack(item)
            if item["type"] == "end":
                break

    task = asyncio.create_task(worker())
    task.add_done_callback(lambda _: None)
    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post(
    "/chart/spec",
    response_model=ChatResponse,
    summary="按视觉通道配置直接生成图表（确定性编译，不调用模型）",
    responses={422: {"model": ErrorResponse}},
)
async def chart_from_spec(payload: SpecRequest) -> ChatResponse:
    dataset = get_store().get(payload.dataset_id)
    result = await get_chart_service().generate_from_spec(
        dataset,
        payload.spec,
        session_id=payload.session_id,
    )
    return _chat_payload(result)


# --------------------------------------------------------------------------- #
# 历史 / 导出
# --------------------------------------------------------------------------- #
@router.get("/charts", summary="图表历史")
async def list_charts(limit: int = 40) -> list[dict[str, Any]]:
    return get_history().list_charts(limit)


@router.get("/charts/{message_id}", summary="读取某张历史图表的完整数据")
async def get_chart(message_id: str) -> dict[str, Any]:
    message = get_history().get_message_chart(message_id)
    if message is None:
        from app.core.errors import InvalidInputError

        raise InvalidInputError("图表记录不存在。")
    return message


@router.post("/export", response_model=ExportResponse, summary="导出 PNG / HTML / CSV / 报告")
async def create_export(payload: ExportRequest) -> ExportResponse:
    if payload.kind == "png":
        content: str | bytes = to_png(payload.chart_json)
    elif payload.kind == "html":
        content = to_html(payload.chart_json, payload.title)
    elif payload.kind == "csv":
        content = to_csv(payload.chart_json)
    else:
        content = to_report(payload.chart_json, payload.prompt, payload.meta)

    filename, _ = save_artifact(content, payload.kind)
    return ExportResponse(kind=payload.kind, filename=filename, url=f"/api/exports/{filename}")


@router.get("/exports/{filename}", summary="下载导出文件")
async def download_export(filename: str) -> FileResponse:
    path = resolve_export(filename)
    media_types = {
        ".png": "image/png",
        ".html": "text/html; charset=utf-8",
        ".csv": "text/csv; charset=utf-8",
    }
    return FileResponse(
        path,
        media_type=media_types.get(path.suffix.lower(), "application/octet-stream"),
        filename=path.name,
    )
