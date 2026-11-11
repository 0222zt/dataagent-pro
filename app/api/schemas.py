"""API 层：请求/响应模型（pydantic 自动校验 + OpenAPI 文档）。"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class DatasetInfo(BaseModel):
    dataset_id: str
    filename: str
    rows: int
    columns: int
    meta: dict[str, Any]
    preview: list[dict[str, Any]]
    expires_in: int
    summary: str = ""
    suggestions: list[dict[str, str]] = Field(default_factory=list)
    demo: bool = False


class AccessRequest(BaseModel):
    code: str = Field(min_length=1, max_length=200, description="分享者提供的访问码")


class ChatRequest(BaseModel):
    dataset_id: str = Field(min_length=4, max_length=64, description="上传接口返回的数据集 id")
    prompt: str = Field(default="", max_length=4000, description="业务需求")
    session_id: str | None = Field(default=None, description="多轮会话 id，留空会自动新建")
    refresh: bool = Field(default=False, description="true 表示忽略缓存重新生成")


class ChartSpec(BaseModel):
    """视觉通道规范：拖字段生成图表时前后端共用的契约。"""

    chart_type: Literal["bar", "line", "scatter", "pie", "area", "box"] = "bar"
    x: str = Field(default="", max_length=200)
    y: str = Field(default="", max_length=200)
    color: str = Field(default="", max_length=200)
    aggregation: Literal["none", "sum", "mean", "count", "max", "min"] = "none"
    title: str = Field(default="", max_length=120)
    top_n: int = Field(default=0, ge=0, le=200)
    sort: Literal["none", "asc", "desc"] = "none"


class SpecRequest(BaseModel):
    dataset_id: str = Field(min_length=4, max_length=64)
    spec: ChartSpec
    session_id: str | None = Field(default=None)


class ChatResponse(BaseModel):
    status: Literal["success"] = "success"
    chart_json: str
    code: str
    attempts: int
    cached: bool
    elapsed_ms: int
    model: str
    session_id: str
    message_id: str = ""
    points: int = 0
    repaired: bool = False
    request_id: str


class SessionInfo(BaseModel):
    session_id: str
    dataset_id: str
    title: str
    created_at: float
    updated_at: float
    message_count: int = 0


class SessionDetail(BaseModel):
    session_id: str
    dataset_id: str
    title: str
    created_at: float
    updated_at: float
    messages: list[dict[str, Any]] = Field(default_factory=list)


class ExportRequest(BaseModel):
    chart_json: str = Field(min_length=2)
    kind: Literal["png", "html", "csv", "report"]
    title: str = Field(default="DataAgent 图表", max_length=120)
    prompt: str = Field(default="", max_length=4000)
    meta: dict[str, Any] | None = None


class ExportResponse(BaseModel):
    status: Literal["ok"] = "ok"
    kind: str
    filename: str
    url: str


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"
    version: str
    provider: str
    model: str
    model_chain: list[str]
    api_key_configured: bool
    demo_enabled: bool = False
    access_required: bool = False
    max_file_mb: float = 100.0
    llm_rate_limit: int = 0
    llm_daily_limit: int = 0
    datasets: dict[str, Any]
    cache: dict[str, Any]
    history: dict[str, Any]
    usage: dict[str, Any]


class ErrorResponse(BaseModel):
    status: Literal["error"] = "error"
    code: str
    message: str
    request_id: str | None = None
    hint: str | None = None
    details: dict[str, Any] | None = None
