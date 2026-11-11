"""数据画像的"人话"部分：一句话总结 + 推荐问题。

这些都是规则生成的，不消耗 token；模型只在真正生成图表时调用。
"""

from __future__ import annotations

from typing import Any

MAX_SUGGESTIONS = 6

# 推荐问题时优先选"业务指标"列，而不是数量 / 编号 / 折扣这类辅助列。
_AMOUNT_HINTS = ("销售额", "金额", "收入", "营收", "营业额", "利润", "毛利", "净利", "gmv")
_COUNT_HINTS = ("数量", "订单数", "件数", "人数", "次数", "编号", "序号", "id", "代码")
_AUX_HINTS = ("折扣", "率", "评分", "占比", "百分比")
_CATEGORY_TIERS: tuple[tuple[tuple[str, ...], float], ...] = (
    (("区域", "地区", "省份", "城市", "国家", "市场"), 4.0),
    (("类别", "品类", "产品", "类型", "等级", "部门"), 3.5),
    (("渠道", "来源", "平台"), 3.0),
    (("销售员", "客户", "会员", "门店"), 2.5),
)


def _rank_numeric(meta: dict[str, Any]) -> list[str]:
    """给数值列按"适合当分析指标"排序，保持同分时的原始顺序。"""
    numeric: list[str] = list(meta.get("numeric_columns") or [])

    def score(name: str) -> float:
        lower = str(name).lower()
        value = 0.0
        if any(hint in lower for hint in _AMOUNT_HINTS):
            value += 5
        if any(hint in lower for hint in ("单价", "价格", "成本")):
            value += 2
        if any(hint in lower for hint in _COUNT_HINTS):
            value -= 4
        if any(hint in lower for hint in _AUX_HINTS):
            value -= 3
        return value

    return sorted(numeric, key=score, reverse=True)


def _rank_categories(meta: dict[str, Any], exclude: set[str]) -> list[str]:
    """给类别列排序：优先业务维度（区域/渠道/品类），而不是是否新客、订单号。"""
    column_info = {c["name"]: c for c in meta.get("columns") or []}
    original = list(meta.get("category_columns") or [])

    def unique_of(name: str) -> int:
        try:
            return int(column_info.get(name, {}).get("unique") or 0)
        except (TypeError, ValueError):
            return 0

    candidates = [
        name
        for name in original
        if name not in exclude and 1 < unique_of(name) <= 30
    ]

    def score(name: str) -> float:
        lower = str(name).lower()
        unique = unique_of(name)
        value = 0.0
        for hints, weight in _CATEGORY_TIERS:
            if any(hint in lower for hint in hints):
                value += weight
                break
        if 3 <= unique <= 12:
            value += 2
        elif unique > 20:
            value -= 2
        if "是否" in lower or lower.startswith("is_"):
            value -= 3
        if any(hint in lower for hint in ("订单", "编号", "序号", "id")):
            value -= 4
        return value

    order = {name: index for index, name in enumerate(candidates)}
    return sorted(candidates, key=lambda name: (-score(name), unique_of(name), order[name]))



def rank_numeric(meta: dict[str, Any]) -> list[str]:
    """对外暴露的数值列排序，供 spec 推荐复用同一套业务权重。"""
    return _rank_numeric(meta)


def rank_categories(meta: dict[str, Any], exclude: set[str] | None = None) -> list[str]:
    """对外暴露的类别列排序，exclude 用来排除已被时间轴占用的列。"""
    return _rank_categories(meta, exclude or set())


def summary_text(meta: dict[str, Any]) -> str:
    shape = meta.get("shape") or {}
    rows = shape.get("rows", 0)
    columns = shape.get("columns", 0)
    completeness = float(meta.get("completeness") or 0)
    parts = [f"{rows:,} 行 × {columns} 列", f"完整度 {completeness:.0%}"]

    numeric = meta.get("numeric_columns") or []
    if numeric:
        parts.append(f"{len(numeric)} 个数值列")
    time_range = meta.get("time_range")
    if time_range:
        parts.append(f"时间跨度 {time_range['start'][:10]} → {time_range['end'][:10]}")
    if meta.get("duplicated_rows"):
        parts.append(f"{meta['duplicated_rows']} 行重复")
    return " · ".join(parts)


def suggest_questions(meta: dict[str, Any]) -> list[dict[str, str]]:
    """按列类型组合出可直接点击的问题。"""
    numeric: list[str] = _rank_numeric(meta)
    datetimes: list[str] = list(meta.get("datetime_columns") or [])
    categories: list[str] = list(meta.get("category_columns") or [])

    # 时间列常常被解析成字符串：用 time_range 猜出来的列补上
    time_range = meta.get("time_range")
    if not datetimes and time_range:
        datetimes = [time_range["column"]]

    # 类别列优先选业务维度（区域/渠道/品类），避免"订单号""是否新客"这种列
    categories = _rank_categories(meta, exclude=set(datetimes))

    suggestions: list[dict[str, str]] = []

    def add(title: str, prompt: str, chart: str) -> None:
        if len(suggestions) >= MAX_SUGGESTIONS:
            return
        if any(item["prompt"] == prompt for item in suggestions):
            return
        suggestions.append({"title": title, "prompt": prompt, "chart": chart})

    if datetimes and numeric:
        time_col, value_col = datetimes[0], numeric[0]
        add(
            f"{value_col} 的时间趋势",
            f"按时间汇总 {value_col}，画折线图展示趋势，并标出最高点",
            "line",
        )

    if categories and numeric:
        category, value = categories[0], numeric[0]
        add(
            f"各 {category} 的 {value} 对比",
            f"按 {category} 分组统计 {value} 的总和，用柱状图从高到低排序，只显示前 10 名",
            "bar",
        )

    if len(numeric) >= 2:
        add(
            f"{numeric[0]} 与 {numeric[1]} 的关系",
            f"画散点图分析 {numeric[0]} 与 {numeric[1]} 的关系，并添加趋势线",
            "scatter",
        )

    if numeric:
        add(
            f"{numeric[0]} 的分布",
            f"画直方图展示 {numeric[0]} 的分布，并标出均值和中位数",
            "histogram",
        )

    if categories:
        add(
            f"各 {categories[0]} 的占比",
            f"用饼图展示各 {categories[0]} 的占比，合并占比小于 5% 的类别为「其他」",
            "pie",
        )

    if float(meta.get("completeness") or 1) < 0.99:
        add(
            "缺失值体检",
            "统计每一列的缺失数量，用横向条形图展示缺失最多的前 10 列",
            "bar",
        )

    if numeric and len(numeric) >= 3:
        add(
            "相关性热力图",
            "计算所有数值列之间的相关系数，用热力图展示",
            "heatmap",
        )

    add(
        "数据总览",
        "用一组小图展示数据整体情况：行数、各列类型分布、数值列的范围",
        "overview",
    )

    return suggestions[:MAX_SUGGESTIONS]
