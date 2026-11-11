"""请求上下文、限流、可选令牌鉴权。"""

from __future__ import annotations

import hashlib
import hmac
import time
import uuid
from collections import defaultdict, deque
from typing import Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response

from app.core.config import STATIC_DIR
from app.core.logging import get_logger, request_id_var

logger = get_logger("app.access")

ACCESS_COOKIE = "dataagent_access"
_ACCESS_SALT = b"dataagent-access-v1"
_ACCESS_PAGE_PATHS = {"/", "/index.html"}
_ACCESS_OPEN_PATHS = {"/api/access", "/api/health", "/health", "/favicon.ico"}


def access_cookie_value(code: str) -> str:
    """把访问码派生成 Cookie 值，避免浏览器里存明文。"""
    return hmac.new(code.encode("utf-8"), _ACCESS_SALT, hashlib.sha256).hexdigest()


class RequestContextMiddleware(BaseHTTPMiddleware):
    """为每个请求生成 request_id，并记录耗时。"""

    async def dispatch(self, request: Request, call_next: Callable) -> Response:  # noqa: ANN001
        request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex[:12]
        token = request_id_var.set(request_id)
        started = time.perf_counter()
        try:
            response: Response = await call_next(request)
        except Exception:
            logger.exception("请求异常：%s %s", request.method, request.url.path)
            raise
        else:
            elapsed_ms = (time.perf_counter() - started) * 1000
            logger.info(
                "%s %s -> %s（%.0f ms）",
                request.method,
                request.url.path,
                response.status_code,
                elapsed_ms,
            )
            response.headers["X-Request-ID"] = request_id
            return response
        finally:
            request_id_var.reset(token)


class AccessGateMiddleware(BaseHTTPMiddleware):
    """可选的访问码门禁：公网演示时防止链接被随意转发。

    浏览器首次访问会看到 ``static/access.html``，提交访问码后拿到 HttpOnly
    Cookie；脚本调用也可以直接在请求头里带 ``X-Access-Code``。
    ``DATAAGENT_ACCESS_CODE`` 为空时整个门禁不生效。
    """

    def __init__(self, app, code: str) -> None:  # noqa: ANN001
        super().__init__(app)
        self._code = (code or "").strip()
        self._cookie_value = access_cookie_value(self._code) if self._code else ""

    def _authorized(self, request: Request) -> bool:
        cookie = request.cookies.get(ACCESS_COOKIE, "")
        if cookie and hmac.compare_digest(cookie, self._cookie_value):
            return True
        header = request.headers.get("X-Access-Code", "").strip()
        return bool(header) and hmac.compare_digest(
            header.encode("utf-8"), self._code.encode("utf-8")
        )

    async def dispatch(self, request: Request, call_next: Callable) -> Response:  # noqa: ANN001
        if not self._code or self._authorized(request):
            return await call_next(request)

        path = request.url.path
        if path in _ACCESS_OPEN_PATHS or path.startswith(("/static/", "/vendor/")):
            return await call_next(request)

        if request.method in {"GET", "HEAD"} and path in _ACCESS_PAGE_PATHS:
            page = STATIC_DIR / "access.html"
            if page.is_file():
                return FileResponse(
                    page,
                    media_type="text/html",
                    headers={"Cache-Control": "no-store"},
                )
            return Response("<h1>需要访问码</h1>", media_type="text/html", status_code=401)

        return JSONResponse(
            status_code=401,
            content={
                "status": "error",
                "code": "ACCESS_REQUIRED",
                "message": "这个演示需要访问码。",
                "hint": "向分享者索取访问码后刷新页面；脚本可用 X-Access-Code 请求头。",
                "request_id": request_id_var.get(),
            },
        )


class RateLimitMiddleware(BaseHTTPMiddleware):
    """按 IP 的滑动窗口限流，只作用于 /api。"""

    def __init__(self, app, limit_per_minute: int = 60) -> None:  # noqa: ANN001
        super().__init__(app)
        self._limit = max(1, limit_per_minute)
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    async def dispatch(self, request: Request, call_next: Callable) -> Response:  # noqa: ANN001
        path = request.url.path
        if not path.startswith("/api"):
            return await call_next(request)

        client = request.client.host if request.client else "unknown"
        now = time.time()
        window = self._hits[client]
        while window and now - window[0] > 60:
            window.popleft()
        if len(window) >= self._limit:
            return JSONResponse(
                status_code=429,
                content={
                    "status": "error",
                    "code": "RATE_LIMITED",
                    "message": f"请求过于频繁（每分钟上限 {self._limit} 次）。",
                    "hint": "免费模型本身也有 RPM 限制，稍等几秒再试。",
                    "request_id": request_id_var.get(),
                },
            )
        window.append(now)
        return await call_next(request)


class LlmQuotaMiddleware(BaseHTTPMiddleware):
    """自然语言生成配额：只卡 ``/api/chat``，拖字段出图不受影响。

    公网演示时真正会花钱的是模型调用，普通限流（30 次/分）拦不住
    "一个人连续点十分钟"。这里单独给每 IP 设分钟 / 天两级上限，
    默认 ``0`` 表示不启用，本地开发行为完全不变。
    """

    _LLM_PATHS = frozenset({"/api/chat", "/api/chat/stream"})

    def __init__(self, app, per_minute: int = 0, per_day: int = 0) -> None:  # noqa: ANN001
        super().__init__(app)
        self._per_minute = max(0, per_minute)
        self._per_day = max(0, per_day)
        self._minute_hits: dict[str, deque[float]] = defaultdict(deque)
        self._day_hits: dict[str, tuple[str, int]] = {}

    @property
    def enabled(self) -> bool:
        return self._per_minute > 0 or self._per_day > 0

    @staticmethod
    def _client_key(request: Request) -> str:
        """平台反代后会带 X-Forwarded-For，取第一跳即真实访客。"""
        forwarded = request.headers.get("X-Forwarded-For", "").split(",")[0].strip()
        if forwarded:
            return forwarded
        return request.client.host if request.client else "unknown"

    def _too_many(self, message: str, hint: str) -> JSONResponse:
        return JSONResponse(
            status_code=429,
            content={
                "status": "error",
                "code": "LLM_QUOTA_EXCEEDED",
                "message": message,
                "hint": hint,
                "request_id": request_id_var.get(),
            },
        )

    async def dispatch(self, request: Request, call_next: Callable) -> Response:  # noqa: ANN001
        if not self.enabled or request.url.path not in self._LLM_PATHS:
            return await call_next(request)

        client = self._client_key(request)
        now = time.time()

        if self._per_minute > 0:
            window = self._minute_hits[client]
            while window and now - window[0] > 60:
                window.popleft()
            if len(window) >= self._per_minute:
                return self._too_many(
                    f"AI 生成太频繁（每分钟上限 {self._per_minute} 次）。",
                    "稍等一分钟再试；拖字段出图不消耗这个额度。",
                )

        day_key = time.strftime("%Y-%m-%d", time.localtime(now))
        if self._per_day > 0:
            stored_day, used = self._day_hits.get(client, (day_key, 0))
            if stored_day != day_key:
                stored_day, used = day_key, 0
            if used >= self._per_day:
                return self._too_many(
                    f"今天的 AI 生成额度已用完（每天 {self._per_day} 次）。",
                    "明天会自动恢复；期间拖字段、点推荐图表仍然可用。",
                )
            self._day_hits[client] = (stored_day, used + 1)

        if self._per_minute > 0:
            self._minute_hits[client].append(now)

        response = await call_next(request)
        if self._per_minute > 0:
            left_minute = max(0, self._per_minute - len(self._minute_hits[client]))
            response.headers["X-LLM-Remaining-Minute"] = str(left_minute)
        if self._per_day > 0:
            response.headers["X-LLM-Remaining-Day"] = str(
                max(0, self._per_day - self._day_hits[client][1])
            )
        return response


class TokenAuthMiddleware(BaseHTTPMiddleware):
    """可选的项目级令牌：设置 DATAAGENT_AUTH_TOKEN 后，/api 需要带令牌。"""

    def __init__(self, app, token: str) -> None:  # noqa: ANN001
        super().__init__(app)
        self._token = token

    async def dispatch(self, request: Request, call_next: Callable) -> Response:  # noqa: ANN001
        path = request.url.path
        if (
            not self._token
            or not path.startswith("/api")
            or path in {"/api/health", "/api/access"}
        ):
            return await call_next(request)

        supplied = (
            request.headers.get("X-API-Token")
            or request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
        )
        if supplied != self._token:
            return JSONResponse(
                status_code=401,
                content={
                    "status": "error",
                    "code": "AUTH_REQUIRED",
                    "message": "缺少或错误的项目访问令牌。",
                    "hint": "在请求头里带 X-API-Token（或 Authorization: Bearer <token>）。",
                    "request_id": request_id_var.get(),
                },
            )
        return await call_next(request)
