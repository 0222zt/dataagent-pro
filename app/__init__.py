"""DataAgent Pro —— 用一句自然语言把表格变成可交互图表。

分层：

* ``app/api``      HTTP 入口，只做校验与响应组装
* ``app/services`` 业务编排：解析 / 画像 / 生成 / 缓存 / 历史 / 导出
* ``app/llm``      免费模型目录 + OpenAI 兼容客户端（含多模型降级）
* ``app/security`` LLM 生成代码的受限执行
* ``app/core``     配置、错误、日志、中间件、SSE
"""

__version__ = "3.0.0"
__all__ = ["__version__"]
