"""FastAPI 应用入口。

职责边界：装配路由 / 中间件 / 异常处理 / 静态资源，业务逻辑一律不写在这里。
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from app import __version__
from app.api.routes import router
from app.core.config import STATIC_DIR, ensure_dirs, get_settings
from app.core.errors import AppError
from app.core.logging import get_logger, request_id_var, setup_logging
from app.core.middleware import (
    AccessGateMiddleware,
    LlmQuotaMiddleware,
    RateLimitMiddleware,
    RequestContextMiddleware,
    TokenAuthMiddleware,
)
from app.services.dataset_store import get_store

logger = get_logger("app.main")


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    setup_logging(settings.log_level)
    ensure_dirs()
    get_store().purge()

    logger.info(
        "DataAgent Pro %s 启动 | 供应商=%s | 模型链=%s | Key=%s",
        __version__,
        settings.provider,
        " → ".join(settings.model_chain),
        settings.masked_key,
    )
    if not settings.has_api_key and settings.provider != "ollama":
        logger.warning(
            "未配置 DATAAGENT_API_KEY：/api/chat 会直接返回 AUTH_ERROR。"
            "请复制 .env.example 为 .env 并填写 Key。"
        )
    if not STATIC_DIR.is_dir():
        logger.warning("静态资源目录不存在：%s", STATIC_DIR)
    if settings.has_access_code:
        logger.info("访问码门禁已开启：浏览器首次访问需输入 DATAAGENT_ACCESS_CODE")
    if settings.demo_enabled:
        logger.info("演示模式已开启：首屏自动载入内置示例数据")
    if settings.llm_rate_limit_per_min or settings.llm_daily_limit:
        logger.info(
            "AI 生成配额：每 IP 每分钟 %s 次 / 每天 %s 次（0 表示不限）",
            settings.llm_rate_limit_per_min or "不限",
            settings.llm_daily_limit or "不限",
        )
    yield
    logger.info("DataAgent Pro 已关闭")


def _plotly_js() -> Path | None:
    """优先用 plotly 自带的前端 JS，保证与后端版本一致。"""
    try:
        import plotly

        base = Path(plotly.__file__).resolve().parent / "package_data"
        for name in ("plotly.min.js", "plotly.js"):
            candidate = base / name
            if candidate.is_file():
                return candidate
    except Exception:  # noqa: BLE001
        return None
    return None


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="DataAgent Pro",
        version=__version__,
        description=(
            "上传表格 + 一句自然语言，返回可交互的 Plotly 图表。"
            "内置免费模型降级、受限代码执行、自愈重试、多轮会话与导出。"
        ),
        lifespan=lifespan,
    )

    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=list(settings.cors_origins),
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    app.add_middleware(TokenAuthMiddleware, token=settings.auth_token)
    app.add_middleware(RateLimitMiddleware, limit_per_minute=settings.rate_limit_per_min)
    app.add_middleware(
        LlmQuotaMiddleware,
        per_minute=settings.llm_rate_limit_per_min,
        per_day=settings.llm_daily_limit,
    )
    app.add_middleware(RequestContextMiddleware)
    # 最后添加 = 最外层：访问码校验先于限流 / 鉴权执行
    app.add_middleware(AccessGateMiddleware, code=settings.access_code)

    app.include_router(router, prefix="/api")

    if STATIC_DIR.is_dir():
        app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    @app.get("/", include_in_schema=False)
    async def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    @app.get("/health", include_in_schema=False)
    async def health_alias() -> dict[str, str]:
        """平台探活常用路径；业务健康检查仍是 /api/health。"""
        return {"status": "ok"}

    @app.get("/vendor/plotly.min.js", include_in_schema=False)
    async def plotly_js() -> Response:
        path = _plotly_js()
        if path is None:
            return JSONResponse(
                status_code=404,
                content={"status": "error", "code": "NOT_FOUND", "message": "plotly.js 不可用"},
            )
        return FileResponse(
            path,
            media_type="application/javascript",
            headers={"Cache-Control": "public, max-age=86400"},
        )

    @app.get("/vendor/plotly-fallback.js", include_in_schema=False)
    async def plotly_fallback() -> Response:
        return Response(
            content="window.__PLOTLY_CDN_FALLBACK__ = true;",
            media_type="application/javascript",
        )

    # ----------------------------------------------------------------- #
    # 统一错误出口：前端只需要看 status / code / message / request_id
    # ----------------------------------------------------------------- #
    @app.exception_handler(AppError)
    async def handle_app_error(_: Request, exc: AppError) -> JSONResponse:
        logger.warning("业务异常 %s：%s", exc.code, exc.message)
        return JSONResponse(
            status_code=exc.status_code,
            content={**exc.to_payload(), "request_id": request_id_var.get()},
        )

    @app.exception_handler(RequestValidationError)
    async def handle_validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        details = []
        for error in exc.errors():
            location = ".".join(str(part) for part in error.get("loc", ()) if part != "body")
            details.append(f"{location or 'body'}: {error.get('msg')}")
        return JSONResponse(
            status_code=422,
            content={
                "status": "error",
                "code": "INVALID_INPUT",
                "message": "参数校验失败：" + "；".join(details),
                "request_id": request_id_var.get(),
            },
        )

    @app.exception_handler(Exception)
    async def handle_unexpected(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("未捕获异常：%s %s", request.method, request.url.path)
        return JSONResponse(
            status_code=500,
            content={
                "status": "error",
                "code": "INTERNAL_ERROR",
                "message": "服务器内部错误，请稍后重试。",
                "request_id": request_id_var.get(),
            },
        )

    return app


app = create_app()
