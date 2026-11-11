"""提示词工程。

要点：

1. 把数据字典（列名 / 类型 / 语义 / 缺失率 / 样本值）结构化地交给模型；
2. 把"执行器的硬规则"写清楚，减少生成必被拒的代码；
3. 支持多轮：把最近几轮的代码作为上下文，实现"把柱子改成折线"这类增量修改；
4. 修复时回传真实 traceback，而不是只给一句 ``str(e)``。
"""

from __future__ import annotations

import re
from typing import Any, Sequence

_FENCE_RE = re.compile(r"```(?:python|py|Python)?\s*(.+?)```", re.DOTALL)

CODE_CONTRACT = """
【执行契约】违反任意一条都会被拒绝：
1. 可用对象只有：df（pandas DataFrame）、pd、np、px、go；
2. 禁止 import（pd、np、px、go 已直接提供，不要重复导入）、读写文件、网络请求、print、fig.show()；
3. 禁止 def / class / lambda / while / with / raise / async；
4. 禁止访问下划线开头的属性或名称（如 __class__、__globals__）；
5. 禁止调用 read_* / to_* / save* / load* / system / eval 等危险方法；
6. 必须把最终图表赋值给变量 fig，且 fig 是 plotly 的 Figure；
7. 图表标题、坐标轴标题、图例、悬浮提示统一使用中文；
8. 列名必须与给定元数据完全一致；只有数值列才能求和 / 求均值；
9. 只输出 Python 代码本身，不要 Markdown 代码块，不要解释文字；
10. 数据可能含缺失值，绘图前自行 dropna / fillna 处理；
11. 画趋势线 / 回归线时禁止使用 px 的 trendline 参数（环境未安装 statsmodels），
    改用 np.polyfit 求系数，再用 go.Scatter(mode="lines") 添加趋势线。
""".strip()


def _schema_text(meta: dict[str, Any]) -> str:
    shape = meta.get("shape") or {}
    lines = [f"数据规模：{shape.get('rows', '?')} 行 × {shape.get('columns', '?')} 列"]
    lines.append("列信息（列名 | 类型 | 语义 | 缺失率 | 样本值 | 统计）：")

    for column in meta.get("columns") or []:
        sample = "、".join(str(item) for item in (column.get("sample") or [])[:3]) or "-"
        stats = column.get("stats") or {}
        stat_text = ""
        if stats:
            pairs = [f"{key}={value}" for key, value in stats.items() if value is not None]
            stat_text = "；" + "，".join(pairs[:5]) if pairs else ""
        lines.append(
            f"- {column.get('name')} | {column.get('dtype')} | {column.get('kind')} | "
            f"缺失 {float(column.get('null_ratio') or 0):.0%} | 例如 {sample}{stat_text}"
        )

    numeric = "、".join(meta.get("numeric_columns") or []) or "无"
    category = "、".join(meta.get("category_columns") or []) or "无"
    datetime_cols = "、".join(meta.get("datetime_columns") or []) or "无"
    lines.append(f"数值列：{numeric}")
    lines.append(f"可分组的类别列：{category}")
    lines.append(f"时间列：{datetime_cols}")

    time_range = meta.get("time_range")
    if time_range:
        lines.append(
            f"时间范围：{time_range.get('column')} "
            f"{time_range.get('start')} → {time_range.get('end')}"
            "（若 dtype 是字符串，可用 pd.to_datetime 转换后再按年/月聚合）"
        )

    correlations = meta.get("correlations") or []
    if correlations:
        pairs = "；".join(
            f"{item['left']} ↔ {item['right']} r={item['r']}" for item in correlations[:3]
        )
        lines.append(f"显著相关：{pairs}")

    return "\n".join(lines)


def _history_text(history: Sequence[dict[str, Any]] | None) -> str:
    if not history:
        return ""
    blocks = ["【当前会话已有的图表（越靠后越新，可在此基础上做增量修改）】"]
    for index, turn in enumerate(history[-3:], start=1):
        prompt = str(turn.get("prompt") or "").strip()
        code = str(turn.get("code") or "").strip()
        blocks.append(f"第 {index} 轮需求：{prompt}")
        if code:
            blocks.append(f"第 {index} 轮代码：\n{code}")
    return "\n".join(blocks)


def build_generate_messages(
    meta: dict[str, Any],
    prompt: str,
    history: Sequence[dict[str, Any]] | None = None,
) -> list[dict[str, str]]:
    system = (
        "你是一名资深数据可视化工程师，负责把业务问题翻译成可直接执行的 Plotly 代码。\n"
        "优先选择最适合数据的图型；数据量大时先聚合再画，避免几十万个点。\n\n"
        f"{_schema_text(meta)}\n\n"
        f"{CODE_CONTRACT}"
    )
    history_text = _history_text(history)
    user = f"用户的业务需求：{prompt}"
    if history_text:
        user += f"\n\n{history_text}\n\n请输出满足新需求的完整代码（不要只输出 diff）。"
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def build_repair_messages(
    meta: dict[str, Any],
    prompt: str,
    code: str,
    error: str,
) -> list[dict[str, str]]:
    system = (
        "你是一名资深数据可视化工程师，正在修复一段自动生成的 Plotly 代码。\n"
        "请针对报错做最小改动，不要改变图表意图。\n\n"
        f"{_schema_text(meta)}\n\n"
        f"{CODE_CONTRACT}"
    )
    user = (
        f"用户的业务需求：{prompt}\n\n"
        f"上一版代码：\n{code}\n\n"
        f"运行时报错：\n{error}\n\n"
        "请输出修正后的完整代码（仍然只输出代码）。"
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def extract_code(text: str) -> str:
    """从模型返回里提取纯代码，兼容 Markdown 代码块与前后废话。"""
    if not text:
        return ""
    match = _FENCE_RE.search(text)
    code = match.group(1) if match else text
    code = code.strip()
    if code.lower().startswith("python\n"):
        code = code[len("python\n") :]
    return code.strip("`").strip()
