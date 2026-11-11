"""受限代码执行器（纵深防御的第一层）。

大模型生成的代码是不可信输入。直接 ``exec`` 等于把"任意 Python 执行"
暴露给公网：提示词注入后可以读文件、发请求、删数据。

这里的防线：

1. **AST 静态校验**：禁 import / def / class / lambda / while / with / raise /
   删除语句 / 下划线属性（``__class__``、``__globals__`` 这类逃逸链）；
2. **名字与属性黑名单**：``open``/``eval``/``read_csv``/``to_csv``/
   ``write_image``/``system`` 等文件、网络、进程入口；
3. **极简 builtins**：只给数学与容器相关函数，``range`` 带长度上限；
4. **节点数上限**：防止超长代码把 CPU 拉满；
5. **线程级超时**：超过 ``DATAAGENT_EXEC_TIMEOUT`` 秒判定失败；
6. **出口校验**：结果必须是 Plotly ``Figure``，且 JSON 体积受限。

注意：Python 层没有真正的沙箱，这是"抬高成本"而不是"绝对隔离"。
生产环境应把执行放进独立子进程 / 容器（见 README）。
"""

from __future__ import annotations

import ast
import traceback
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from typing import Any

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import plotly.io as pio

from app.core.config import Settings, get_settings
from app.core.errors import CodeExecutionError, CodeTimeoutError, CodeValidationError
from app.core.logging import get_logger

logger = get_logger(__name__)

# 画图场景完全用不到、但能造成破坏或逃逸的语法结构。
_BLOCKED_NODES: tuple[type[ast.AST], ...] = (
    ast.Import,
    ast.ImportFrom,
    ast.FunctionDef,
    ast.AsyncFunctionDef,
    ast.ClassDef,
    ast.Lambda,
    ast.While,
    ast.With,
    ast.AsyncWith,
    ast.AsyncFor,
    ast.Global,
    ast.Nonlocal,
    ast.Delete,
    ast.Await,
    ast.Yield,
    ast.YieldFrom,
    ast.Raise,
)

_BLOCKED_NAMES = frozenset(
    {
        "__import__",
        "__builtins__",
        "eval",
        "exec",
        "compile",
        "open",
        "input",
        "globals",
        "locals",
        "vars",
        "getattr",
        "setattr",
        "delattr",
        "hasattr",
        "breakpoint",
        "exit",
        "quit",
        "help",
        "memoryview",
        "object",
        "type",
        "super",
        "property",
        "classmethod",
        "staticmethod",
        "os",
        "sys",
        "subprocess",
        "socket",
        "shutil",
        "pathlib",
        "importlib",
        "pickle",
        "marshal",
        "ctypes",
    }
)

_BLOCKED_ATTRS = frozenset(
    {
        "show",
        "write_html",
        "write_image",
        "write_json",
        "write_text",
        "write_bytes",
        "to_html",
        "to_image",
        "to_json",
        "to_csv",
        "to_excel",
        "to_pickle",
        "to_parquet",
        "to_sql",
        "to_hdf",
        "to_feather",
        "to_stata",
        "to_clipboard",
        "read_csv",
        "read_excel",
        "read_json",
        "read_html",
        "read_pickle",
        "read_sql",
        "read_sql_query",
        "read_table",
        "read_parquet",
        "read_feather",
        "read_orc",
        "read_stata",
        "read_sas",
        "read_spss",
        "read_xml",
        "read_clipboard",
        "read_hdf",
        "read_gbq",
        "read_fwf",
        "save",
        "savez",
        "savez_compressed",
        "savetxt",
        "load",
        "loadtxt",
        "fromfile",
        "tofile",
        "system",
        "popen",
        "spawn",
        "Popen",
        "call",
        "run",
        "eval",
        "query",
        "environ",
        "getenv",
        "mkdir",
        "makedirs",
        "unlink",
        "remove",
        "rmdir",
        "rename",
        "listdir",
        "walk",
        "glob",
    }
)

_MAX_AST_NODES = 4000
_EXECUTOR = ThreadPoolExecutor(max_workers=2, thread_name_prefix="chart-exec")


def validate_code(code: str, settings: Settings | None = None) -> ast.Module:
    """AST 静态校验，返回解析树供执行复用。"""
    settings = settings or get_settings()
    if not code or not code.strip():
        raise CodeValidationError("模型返回了空代码。")
    if len(code) > settings.max_code_chars:
        raise CodeValidationError(
            f"代码 {len(code)} 字符，超过 {settings.max_code_chars} 字符上限。"
        )

    try:
        tree = ast.parse(code, mode="exec")
    except SyntaxError as exc:
        raise CodeValidationError(
            f"代码语法错误：{exc.msg}（第 {exc.lineno} 行）。"
        ) from exc

    nodes = list(ast.walk(tree))
    if len(nodes) > _MAX_AST_NODES:
        raise CodeValidationError(f"代码节点数 {len(nodes)} 超过上限，拒绝执行。")

    for node in nodes:
        if isinstance(node, _BLOCKED_NODES):
            raise CodeValidationError(
                f"不允许使用 {type(node).__name__}（第 {getattr(node, 'lineno', '?')} 行）。"
            )

        if isinstance(node, ast.Name):
            name = node.id
            if name in _BLOCKED_NAMES:
                raise CodeValidationError(f"不允许使用名称 {name}（第 {node.lineno} 行）。")
            if name.startswith("__"):
                raise CodeValidationError(f"不允许访问双下划线名称 {name}。")

        elif isinstance(node, ast.Attribute):
            attr = node.attr
            if attr.startswith("_"):
                raise CodeValidationError(
                    f"不允许访问私有属性 {attr}（第 {node.lineno} 行）。"
                )
            if attr in _BLOCKED_ATTRS:
                raise CodeValidationError(f"不允许调用 {attr}（第 {node.lineno} 行）。")

        elif isinstance(node, ast.Constant) and isinstance(node.value, bytes):
            raise CodeValidationError("不允许使用 bytes 字面量。")

    return tree


def _guarded_range(max_iterations: int):
    def bounded_range(*args: Any) -> range:
        value = range(*args)
        if len(value) > max_iterations:
            raise ValueError(f"range 长度 {len(value)} 超过上限 {max_iterations}。")
        return value

    return bounded_range


def _safe_builtins(max_iterations: int) -> dict[str, Any]:
    def _print(*_args: Any, **_kwargs: Any) -> None:
        return None

    return {
        "len": len,
        "range": _guarded_range(max_iterations),
        "enumerate": enumerate,
        "zip": zip,
        "sorted": sorted,
        "sum": sum,
        "min": min,
        "max": max,
        "abs": abs,
        "round": round,
        "divmod": divmod,
        "pow": pow,
        "any": any,
        "all": all,
        "int": int,
        "float": float,
        "str": str,
        "bool": bool,
        "list": list,
        "dict": dict,
        "set": set,
        "tuple": tuple,
        "frozenset": frozenset,
        "slice": slice,
        "repr": repr,
        "format": format,
        "isinstance": isinstance,
        "print": _print,
    }


def _exec_in_env(tree: ast.Module, code: str, env: dict[str, Any]) -> None:
    try:
        exec(compile(tree, "<ai-chart>", "exec"), env, env)  # noqa: S102 - AST 已校验
    except Exception as exc:  # noqa: BLE001 - 转成可自愈的业务异常
        raise CodeExecutionError(
            f"{type(exc).__name__}: {exc}",
            code_text=code,
            traceback_text=traceback.format_exc(limit=4),
        ) from exc


def run_chart_code(code: str, frame: pd.DataFrame, settings: Settings | None = None) -> str:
    """执行图表代码，返回 Plotly 的 JSON 字符串。"""
    settings = settings or get_settings()
    tree = validate_code(code, settings)

    env: dict[str, Any] = {
        "__builtins__": _safe_builtins(settings.max_loop_iterations),
        "df": frame,
        "pd": pd,
        "np": np,
        "px": px,
        "go": go,
    }

    future = _EXECUTOR.submit(_exec_in_env, tree, code, env)
    try:
        future.result(timeout=settings.exec_timeout)
    except FutureTimeout as exc:
        raise CodeTimeoutError(
            f"图表代码执行超过 {settings.exec_timeout:g} 秒，已中止。",
            code_text=code,
        ) from exc

    figure = env.get("fig")
    if not isinstance(figure, go.Figure):
        raise CodeExecutionError(
            "代码执行成功，但没有得到 Plotly 图表对象 fig（必须把结果赋给 fig）。",
            code_text=code,
        )

    try:
        chart_json = pio.to_json(figure)
    except Exception as exc:  # noqa: BLE001
        raise CodeExecutionError(
            f"图表序列化失败：{exc}",
            code_text=code,
            traceback_text=traceback.format_exc(limit=3),
        ) from exc

    if len(chart_json) > settings.max_chart_json_chars:
        raise CodeExecutionError(
            f"图表 JSON 体积 {len(chart_json)} 字符，超过上限；请先聚合或减少数据点。",
            code_text=code,
        )

    logger.debug("图表代码执行成功，JSON %s 字符", len(chart_json))
    return chart_json


def figure_points(chart_json: str) -> int:
    """粗略统计图表数据点数量（用于展示与限流提示）。"""
    try:
        import json

        data = json.loads(chart_json)
        return sum(len(trace.get("x") or trace.get("y") or []) for trace in data.get("data", []))
    except Exception:  # noqa: BLE001
        return 0
