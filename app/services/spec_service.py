"""视觉通道规范 → Plotly 代码编译器。

拖字段生成图表走的是"确定性编译"：模型不参与，``ChartSpec`` 直接编译成
只使用注入变量（``df/pd/np/px/go``）的代码，再交给同一个沙箱执行。
好处是首次成功率 100%、响应在毫秒级；模糊或复杂需求仍走
``/api/chat`` 的"模型生成 + 失败自愈"链路。

这里同时负责两件事：

* ``compile_spec``  校验字段类型，生成可执行代码与中文标题；
* ``recommend_specs`` 上传后按列类型给出 3–5 个可直接点击的图表方案。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from app.core.errors import InvalidInputError
from app.services.profile_service import rank_categories, rank_numeric

CHART_TYPES = ("bar", "line", "scatter", "pie", "area", "box")
AGGREGATIONS = ("none", "sum", "mean", "count", "max", "min")
SORTS = ("none", "asc", "desc")

CHART_LABELS = {
    "bar": "柱状图",
    "line": "折线图",
    "scatter": "散点图",
    "pie": "饼图",
    "area": "面积图",
    "box": "箱线图",
}

AGG_LABELS = {
    "none": "不聚合",
    "sum": "总和",
    "mean": "平均值",
    "count": "计数",
    "max": "最大值",
    "min": "最小值",
}

# 这些图直接使用原始行，聚合/排序只影响取样顺序，意义不大。
_RAW_CHARTS = {"scatter", "box"}

# 逐日数据画折线会很密；如果同时存在「月份 / 季度 / 周」这类文本列，优先用它。
_PERIOD_HINTS = ("月份", "月度", "年月", "季度", "周", "month", "quarter", "week", "period")


@dataclass(slots=True)
class CompiledSpec:
    code: str
    title: str
    prompt: str
    chart_type: str
    warnings: list[str] = field(default_factory=list)


def _value(spec: Any, key: str, default: Any = None) -> Any:
    """同时兼容 pydantic 模型与普通 dict。"""
    if isinstance(spec, dict):
        return spec.get(key, default)
    return getattr(spec, key, default)


def _literal(text: str) -> str:
    return json.dumps(str(text), ensure_ascii=False)


def _column_map(meta: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(item.get("name")): item for item in meta.get("columns") or []}


def _require(
    name: str, columns: dict[str, dict[str, Any]], channel: str, chart_type: str
) -> None:
    label = CHART_LABELS.get(chart_type, "图表")
    if not name:
        raise InvalidInputError(
            f"{label}需要先给「{channel}」通道分配字段。",
            hint="从左侧字段列表拖一个字段到右侧槽位，或直接点击字段快速分配。",
        )
    if name not in columns:
        raise InvalidInputError(
            f"{channel} 字段「{name}」不在当前数据集里。",
            hint="重新上传数据或刷新页面后再试。",
        )


def compile_spec(meta: dict[str, Any], spec: Any) -> CompiledSpec:
    """校验 spec 并编译成沙箱可执行的 Plotly 代码。"""
    columns = _column_map(meta)
    if not columns:
        raise InvalidInputError("当前数据集没有可用字段。")

    chart_type = str(_value(spec, "chart_type", "bar") or "bar")
    if chart_type not in CHART_TYPES:
        raise InvalidInputError(f"不支持的图表类型：{chart_type}。")

    aggregation = str(_value(spec, "aggregation", "none") or "none")
    if aggregation not in AGGREGATIONS:
        raise InvalidInputError(f"不支持的聚合方式：{aggregation}。")

    sort = str(_value(spec, "sort", "none") or "none")
    if sort not in SORTS:
        raise InvalidInputError(f"不支持的排序方式：{sort}。")

    x = str(_value(spec, "x", "") or "").strip()
    y = str(_value(spec, "y", "") or "").strip()
    color = str(_value(spec, "color", "") or "").strip()
    raw_title = str(_value(spec, "title", "") or "").strip()
    top_n = int(_value(spec, "top_n", 0) or 0)
    top_n = max(0, min(200, top_n))

    warnings: list[str] = []

    # 饼图必须有名字；其余图按需校验。
    if chart_type == "pie":
        if not x and color:
            x, color = color, ""
        _require(x, columns, "X 轴 / 分类", chart_type)
    elif chart_type == "box":
        _require(y, columns, "Y 轴", chart_type)
        if x:
            _require(x, columns, "X 轴", chart_type)
    else:
        _require(x, columns, "X 轴", chart_type)
        if not y and aggregation != "count":
            raise InvalidInputError(
                f"{CHART_LABELS[chart_type]}需要给「Y 轴」分配一个数值字段。",
                hint="也可以把聚合方式改成「计数」，这样就只需要一个分类字段。",
            )

    if color:
        _require(color, columns, "颜色", chart_type)
        if color == x or color == y:
            warnings.append("颜色字段与坐标轴相同，已自动忽略颜色通道。")
            color = ""

    y_kind = str(columns[y].get("kind")) if y else ""
    x_kind = str(columns[x].get("kind")) if x else ""

    if chart_type == "scatter":
        if aggregation != "none":
            warnings.append("散点图展示原始数据点，聚合方式已忽略。")
            aggregation = "none"
        if x_kind not in {"numeric", "datetime"}:
            raise InvalidInputError(
                f"散点图的 X 轴需要数值或时间字段，「{x}」是文本列。",
                hint="可以改用柱状图做分类对比。",
            )
        if y_kind != "numeric":
            raise InvalidInputError(f"散点图的 Y 轴需要数值字段，「{y}」不是数值列。")
        if top_n:
            warnings.append("散点图不会截断数据，Top N 已忽略。")
            top_n = 0

    if chart_type == "box":
        if aggregation != "none":
            warnings.append("箱线图展示原始分布，聚合方式已忽略。")
            aggregation = "none"
        if y_kind != "numeric":
            raise InvalidInputError(f"箱线图的 Y 轴需要数值字段，「{y}」不是数值列。")
        if top_n:
            warnings.append("箱线图不会截断数据，Top N 已忽略。")
            top_n = 0

    if y and y_kind != "numeric" and aggregation not in {"none", "count"}:
        warnings.append(f"「{y}」不是数值列，非数值行会被忽略后再做{AGG_LABELS[aggregation]}。")
    if y and y_kind == "numeric" and aggregation == "count":
        warnings.append("Y 轴是数值列，计数统计的是非空行数，而不是数值之和。")

    if chart_type == "pie" and aggregation == "none":
        aggregation = "sum"
        if y:
            warnings.append("饼图需要占比，已自动按总和聚合。")

    if chart_type in {"bar", "line", "area"} and aggregation == "none" and not y:
        raise InvalidInputError("不聚合时需要先给 Y 轴分配字段。")

    if x:
        unique = int(columns[x].get("unique") or 0)
        if chart_type in {"bar", "pie"} and unique > 40 and not top_n:
            warnings.append(f"「{x}」有 {unique} 个不同取值，建议设置 Top N 避免图形过密。")
    if color:
        unique = int(columns[color].get("unique") or 0)
        if unique > 12:
            warnings.append(f"颜色字段「{color}」有 {unique} 个取值，图例会比较多。")

    title = raw_title or _default_title(chart_type, x, y, color, aggregation)
    code = _build_code(chart_type, x, y, color, aggregation, sort, top_n, title)
    prompt = _describe(chart_type, x, y, color, aggregation, sort, top_n, title)
    return CompiledSpec(
        code=code,
        title=title,
        prompt=prompt,
        chart_type=chart_type,
        warnings=warnings,
    )


# --------------------------------------------------------------------------- #
# 代码生成
# --------------------------------------------------------------------------- #
def _build_code(
    chart_type: str,
    x: str,
    y: str,
    color: str,
    aggregation: str,
    sort: str,
    top_n: int,
    title: str,
) -> str:
    lines: list[str] = []
    needs_agg = chart_type in {"bar", "line", "area", "pie"} and aggregation != "none"

    if needs_agg and aggregation != "count":
        lines.append("_frame = df.copy()")
        lines.append(f"_y = {_literal(y)}")
        lines.append('_frame["_value"] = pd.to_numeric(_frame[_y], errors="coerce")')
    else:
        lines.append("_frame = df")

    if x:
        lines.append(f"_x = {_literal(x)}")
    if y:
        lines.append(f"_y = {_literal(y)}")
    if color:
        lines.append(f"_c = {_literal(color)}")

    if needs_agg:
        keys = [key for key in (x, color) if key]
        lines.append(f"_keys = {json.dumps(keys, ensure_ascii=False)}")
        if aggregation == "count":
            lines.append(
                '_plot = _frame.groupby(_keys, dropna=False).size().reset_index(name="_value")'
            )
        else:
            lines.append(
                '_plot = _frame.groupby(_keys, dropna=False)["_value"].agg('
                f'{_literal(aggregation)}).reset_index()'
            )
        lines.append('_plot = _plot.dropna(subset=["_value"])')
        if top_n:
            lines.append(f'_plot = _plot.nlargest({top_n}, "_value")')
        if sort == "asc":
            lines.append('_plot = _plot.sort_values("_value", ascending=True)')
        elif sort == "desc":
            lines.append('_plot = _plot.sort_values("_value", ascending=False)')
        elif chart_type in {"line", "area"} and x:
            lines.append("_plot = _plot.sort_values(_x)")
        y_arg = '"_value"'
    else:
        keep = [key for key in (x, y, color) if key]
        lines.append(f"_keep = {json.dumps(keep, ensure_ascii=False)}")
        lines.append("_plot = _frame[_keep].dropna()")
        if sort == "asc" and y:
            lines.append("_plot = _plot.sort_values(_y, ascending=True)")
        elif sort == "desc" and y:
            lines.append("_plot = _plot.sort_values(_y, ascending=False)")
        elif chart_type in {"line", "area"} and x:
            lines.append("_plot = _plot.sort_values(_x)")
        y_arg = "_y"

    lines.append(f"_title = {_literal(title)}")
    color_arg = ", color=_c" if color else ""

    if chart_type == "bar":
        lines.append(f"fig = px.bar(_plot, x=_x, y={y_arg}{color_arg}, title=_title)")
        lines.append("fig.update_layout(bargap=0.28)")
    elif chart_type == "line":
        lines.append(
            f"fig = px.line(_plot, x=_x, y={y_arg}{color_arg}, title=_title, markers=True)"
        )
        lines.append("fig.update_traces(line=dict(width=2.2))")
    elif chart_type == "area":
        lines.append(f"fig = px.area(_plot, x=_x, y={y_arg}{color_arg}, title=_title)")
    elif chart_type == "scatter":
        lines.append(
            f"fig = px.scatter(_plot, x=_x, y=_y{color_arg}, title=_title, opacity=0.85)"
        )
        lines.append("fig.update_traces(marker=dict(size=9, line=dict(width=0)))")
    elif chart_type == "pie":
        lines.append(
            'fig = px.pie(_plot, names=_x, values="_value", title=_title, hole=0.45)'
        )
        lines.append('fig.update_traces(textposition="inside", textinfo="percent+label")')
    else:  # box
        x_arg = "x=_x, " if x else ""
        lines.append(
            f'fig = px.box(_plot, {x_arg}y=_y{color_arg}, title=_title, points="outliers")'
        )

    lines.append("fig.update_layout(")
    lines.append('    template="plotly_white",')
    lines.append(f"    margin=dict(l=56, r=28, t={112 if color else 64}, b=52),")
    if color:
        lines.append(
            '    title=dict(x=0.02, xanchor="left", y=1.0, yanchor="top"),'
        )
    else:
        lines.append('    title=dict(x=0.02, xanchor="left"),')
    lines.append(f"    showlegend={'True' if color else 'False'},")
    lines.append(")")
    if color:
        lines.append(
            'fig.update_layout(legend=dict(orientation="h", yanchor="bottom", '
            "y=1.02, xanchor=\"right\", x=1))"
        )

    if x:
        lines.append(f"_x_label = {_literal(x)}")
        lines.append("fig.update_xaxes(title_text=_x_label)")
    if y:
        value_label = f"{AGG_LABELS.get(aggregation, '')}({y})" if needs_agg else y
        if aggregation == "count":
            value_label = f"计数({y})"
        lines.append(f"_y_label = {_literal(value_label)}")
        lines.append("fig.update_yaxes(title_text=_y_label)")

    return "\n".join(lines)


def _default_title(
    chart_type: str, x: str, y: str, color: str, aggregation: str
) -> str:
    if chart_type == "scatter":
        return f"{y} 与 {x} 的关系"
    if chart_type == "pie":
        return f"各 {x} 的 {y} 占比" if y else f"各 {x} 的占比"
    if chart_type in {"line", "area"}:
        return f"{y} 随 {x} 的变化"
    if chart_type == "box":
        return f"{y} 按 {x} 的分布" if x else f"{y} 的分布"
    if aggregation == "count":
        return f"各 {x} 的记录数"
    return f"各 {x} 的 {y}"


def _describe(
    chart_type: str,
    x: str,
    y: str,
    color: str,
    aggregation: str,
    sort: str,
    top_n: int,
    title: str,
) -> str:
    parts = [CHART_LABELS.get(chart_type, chart_type), title]
    if x:
        parts.append(f"X={x}")
    if y:
        parts.append(f"Y={y}")
    if color:
        parts.append(f"颜色={color}")
    if aggregation != "none":
        parts.append(f"聚合={AGG_LABELS.get(aggregation, aggregation)}")
    if sort != "none":
        parts.append("排序=升序" if sort == "asc" else "排序=降序")
    if top_n:
        parts.append(f"Top {top_n}")
    return " · ".join(parts)


# --------------------------------------------------------------------------- #
# 推荐
# --------------------------------------------------------------------------- #
def recommend_specs(meta: dict[str, Any], limit: int = 5) -> list[dict[str, Any]]:
    """按列类型给出一组可直接应用的图表方案，第一条作为默认。"""
    numeric = rank_numeric(meta)
    datetimes = list(meta.get("datetime_columns") or [])
    time_range = meta.get("time_range")
    if not datetimes and time_range:
        datetimes = [str(time_range.get("column") or "")]
    categories = rank_categories(meta, exclude=set(datetimes))

    items: list[dict[str, Any]] = []

    def add(label: str, reason: str, **spec: Any) -> None:
        if len(items) >= limit:
            return
        payload = {"chart_type": "bar", "aggregation": "none", "top_n": 0, "sort": "none"}
        payload.update(spec)
        if any(item["spec"] == payload for item in items):
            return
        items.append({"label": label, "reason": reason, "spec": payload})

    time_col = datetimes[0] if datetimes else ""
    category = categories[0] if categories else ""
    measure = numeric[0] if numeric else ""

    # 逐日 / 逐小时数据直接画折线会有几百个点，视觉上很吵。
    # 若同时存在「月份」这类低基数时间列，优先用它做趋势轴。
    by_name = {str(item.get("name")): item for item in meta.get("columns") or []}
    if time_col and int(by_name.get(time_col, {}).get("unique") or 0) > 60:
        period = next(
            (
                name
                for name in categories
                if any(hint in str(name).lower() for hint in _PERIOD_HINTS)
            ),
            "",
        )
        if period and int(by_name.get(period, {}).get("unique") or 0) <= 60:
            time_col = period

    if time_col and measure:
        add(
            "时间趋势",
            f"按 {time_col} 看 {measure} 的变化",
            chart_type="line",
            x=time_col,
            y=measure,
            aggregation="sum",
            sort="none",
            title=f"{measure} 随 {time_col} 的变化",
        )

    if category and measure:
        add(
            "分类对比",
            f"比较各 {category} 的 {measure} 总和",
            chart_type="bar",
            x=category,
            y=measure,
            aggregation="sum",
            sort="desc",
            top_n=10,
            title=f"各 {category} 的 {measure} 对比",
        )

    if len(numeric) >= 2:
        add(
            "相关性",
            f"看 {numeric[0]} 与 {numeric[1]} 是否相关",
            chart_type="scatter",
            x=numeric[0],
            y=numeric[1],
            aggregation="none",
            sort="none",
            title=f"{numeric[1]} 与 {numeric[0]} 的关系",
        )

    if category and measure:
        add(
            "占比",
            f"看 {measure} 在不同 {category} 上的占比",
            chart_type="pie",
            x=category,
            y=measure,
            aggregation="sum",
            sort="desc",
            top_n=8,
            title=f"各 {category} 的 {measure} 占比",
        )
        add(
            "分布对比",
            f"比较各 {category} 的 {measure} 分布",
            chart_type="box",
            x=category,
            y=measure,
            aggregation="none",
            sort="none",
            title=f"{measure} 按 {category} 的分布",
        )

    if not items and numeric:
        add(
            "数值分布",
            f"看 {numeric[0]} 的分布",
            chart_type="box",
            y=numeric[0],
            aggregation="none",
            title=f"{numeric[0]} 的分布",
        )

    return items
