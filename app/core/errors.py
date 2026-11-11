"""统一的业务异常体系。

每个异常带三样东西：稳定的 ``code``、合适的 HTTP 状态码、可操作的 ``hint``。
前端因此可以针对不同错误给出不同引导，而不是统一弹一句"出错了"。
"""

from __future__ import annotations

from typing import Any


class AppError(Exception):
    """所有可预期错误的基类。"""

    code: str = "APP_ERROR"
    status_code: int = 400
    hint: str = ""

    def __init__(
        self,
        message: str,
        *,
        hint: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        if hint:
            self.hint = hint
        self.details = details or {}

    def to_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "status": "error",
            "code": self.code,
            "message": self.message,
        }
        if self.hint:
            payload["hint"] = self.hint
        if self.details:
            payload["details"] = self.details
        return payload


class InvalidInputError(AppError):
    code = "INVALID_INPUT"
    status_code = 422


class UnsupportedFileError(AppError):
    code = "UNSUPPORTED_FILE"
    status_code = 415
    hint = "支持 .csv / .tsv / .xlsx / .xls / .json 五种格式。"


class FileTooLargeError(AppError):
    code = "FILE_TOO_LARGE"
    status_code = 413
    hint = "先在 Excel 里抽样，或另存为 CSV 再上传。"


class EmptyDatasetError(AppError):
    code = "EMPTY_DATASET"
    status_code = 422
    hint = "请确认表格有表头，且至少有一行数据。"


class DatasetNotFoundError(AppError):
    code = "DATASET_NOT_FOUND"
    status_code = 404
    hint = "数据集会过期，请重新上传文件。"


class SessionNotFoundError(AppError):
    code = "SESSION_NOT_FOUND"
    status_code = 404
    hint = "会话不存在或已被删除，新建一个即可。"


class AuthError(AppError):
    code = "AUTH_ERROR"
    status_code = 401
    hint = "检查 .env 中的 DATAAGENT_API_KEY 是否正确、是否已过期。"


class AccessDeniedError(AppError):
    code = "ACCESS_DENIED"
    status_code = 401
    hint = "请向分享者索取访问码，或在请求头带 X-Access-Code。"


class RateLimitError(AppError):
    code = "RATE_LIMITED"
    status_code = 429
    hint = "免费模型有固定 RPM 限额，稍等几秒再试，或换一个兜底模型。"


class LLMError(AppError):
    code = "LLM_ERROR"
    status_code = 502
    hint = "检查网络、API Key、账户余额，或换一个免费模型。"


class LLMResponseError(LLMError):
    code = "LLM_BAD_RESPONSE"
    hint = "模型返回的不是可用代码，系统会自动重试或修复。"


class CodeValidationError(AppError):
    code = "CODE_REJECTED"
    status_code = 422
    hint = "模型生成的代码触碰了安全规则，已被阻止执行。"


class CodeExecutionError(AppError):
    code = "CODE_EXECUTION_FAILED"
    status_code = 500

    def __init__(
        self,
        message: str,
        *,
        code_text: str = "",
        traceback_text: str = "",
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message, details=details)
        self.code_text = code_text
        self.traceback_text = traceback_text


class CodeTimeoutError(CodeExecutionError):
    code = "CODE_TIMEOUT"
    status_code = 504
    hint = "图表代码执行超时，已中止；可以简化需求后重试。"


class ExportError(AppError):
    code = "EXPORT_FAILED"
    status_code = 501
    hint = "PNG 需要在服务端安装 kaleido；也可以直接点前端的「下载 PNG」。"
