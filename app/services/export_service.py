"""导出：PNG / HTML / CSV / 可读报告。

* HTML 用 CDN 版 Plotly，文件小、双击可看；
* CSV 把图表里的 trace 还原成"系列, x, y"三列，方便二次分析；
* PNG 需要服务端装 ``kaleido``；没装时返回 501，前端会自动改用浏览器端导出；
* 报告是一页独立 HTML，包含需求、数据概况、图表和生成代码。
"""

from __future__ import annotations

import base64
import csv
import html
import io
import json
import re
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import plotly.io as pio

from app.core.config import EXPORT_DIR
from app.core.errors import ExportError
from app.core.logging import get_logger

logger = get_logger(__name__)

_SAFE_NAME = re.compile(r"^[A-Za-z0-9._-]{1,120}$")
_MAX_CSV_ROWS = 20_000
_KINDS = {"png", "html", "csv", "report"}


def _figure(chart_json: str):
    try:
        return pio.from_json(chart_json)
    except Exception as exc:  # noqa: BLE001
        raise ExportError(f"图表 JSON 无法解析：{exc}") from exc


def to_html(chart_json: str, title: str = "DataAgent 图表") -> str:
    figure = _figure(chart_json)
    return pio.to_html(
        figure,
        include_plotlyjs="cdn",
        full_html=True,
        config={"responsive": True, "displaylogo": False, "locale": "zh-CN"},
    ).replace("<title>Plotly</title>", f"<title>{html.escape(title)}</title>")


def to_csv(chart_json: str) -> str:
    # 直接读原始 JSON：plotly 7 会把数值数组编码成 {dtype, bdata}，
    # 经 pio.from_json 解码后反而拿不到可迭代的 list。
    try:
        payload = json.loads(chart_json)
    except (TypeError, ValueError) as exc:
        raise ExportError(f"图表 JSON 无法解析：{exc}") from exc

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["series", "x", "y"])

    rows = 0
    for index, trace in enumerate(payload.get("data") or [], start=1):
        if not isinstance(trace, dict):
            continue
        name = trace.get("name") or f"系列{index}"
        xs = _as_list(trace.get("x"))
        ys = _as_list(trace.get("y"))

        # 饼图 / 环形图没有 x、y，用 labels + values 还原
        if not xs and not ys:
            xs = _as_list(trace.get("labels"))
            ys = _as_list(trace.get("values"))

        if xs and not ys:
            xs, ys = list(range(1, len(xs) + 1)), xs
        for x_value, y_value in zip(xs, ys):
            writer.writerow([name, _cell(x_value), _cell(y_value)])
            rows += 1
            if rows >= _MAX_CSV_ROWS:
                writer.writerow(["# 已截断", f"共 {_MAX_CSV_ROWS} 行", ""])
                return buffer.getvalue()

    if rows == 0:
        raise ExportError(
            "这张图没有可导出的数值数据（例如纯文本标注图或热力图）。"
        )
    return buffer.getvalue()


def to_png(chart_json: str) -> bytes:
    try:
        from kaleido.scopes.plotly import PlotlyScope  # noqa: F401
    except Exception as exc:  # noqa: BLE001
        raise ExportError(
            "服务端没有安装 kaleido，无法生成 PNG。",
            hint="pip install kaleido，或直接点页面上的「下载 PNG」（浏览器端渲染）。",
        ) from exc
    try:
        return pio.to_image(_figure(chart_json), format="png", width=1400, height=900, scale=2)
    except Exception as exc:  # noqa: BLE001
        raise ExportError(f"PNG 渲染失败：{exc}") from exc


def to_report(chart_json: str, prompt: str = "", meta: dict[str, Any] | None = None) -> str:
    figure = _figure(chart_json)
    chart_html = pio.to_html(
        figure,
        include_plotlyjs="cdn",
        full_html=False,
        config={"responsive": True, "displaylogo": False},
    )
    safe_prompt = html.escape(prompt or "（未提供需求描述）")
    meta = meta or {}
    shape = meta.get("shape") or {}
    summary = (
        f"{shape.get('rows', '-')} 行 × {shape.get('columns', '-')} 列"
        f" · 完整度 {float(meta.get('completeness') or 0):.0%}"
        if meta
        else "未附数据画像"
    )
    created = datetime.now().strftime("%Y-%m-%d %H:%M")

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>DataAgent 分析报告</title>
<style>
  :root {{ color-scheme: light; }}
  body {{ margin: 0; background: #f8fafc; color: #0f172a;
         font-family: "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif; }}
  .page {{ max-width: 1100px; margin: 0 auto; padding: 48px 28px 72px; }}
  .badge {{ display: inline-block; padding: 4px 10px; border-radius: 999px;
            background: #e0e7ff; color: #4338ca; font-size: 12px; font-weight: 600; }}
  h1 {{ font-size: 30px; margin: 14px 0 6px; letter-spacing: -0.02em; }}
  .meta {{ color: #64748b; font-size: 13px; }}
  .card {{ background: #fff; border: 1px solid #e2e8f0; border-radius: 16px;
           padding: 20px; margin-top: 22px; box-shadow: 0 1px 2px rgba(15,23,42,.04); }}
  .label {{ font-size: 12px; font-weight: 700; color: #64748b; letter-spacing: .08em;
            text-transform: uppercase; margin-bottom: 8px; }}
  .prompt {{ font-size: 18px; line-height: 1.6; }}
  pre {{ background: #0f172a; color: #e2e8f0; padding: 16px; border-radius: 12px;
         overflow: auto; font-size: 12px; line-height: 1.6; }}
  footer {{ margin-top: 32px; color: #94a3b8; font-size: 12px; text-align: center; }}
</style>
</head>
<body>
  <div class="page">
    <span class="badge">DataAgent Pro · 分析报告</span>
    <h1>一句话，一张图</h1>
    <p class="meta">生成时间：{created} ｜ 数据规模：{html.escape(summary)}</p>

    <div class="card">
      <div class="label">业务诉求</div>
      <p class="prompt">{safe_prompt}</p>
    </div>

    <div class="card">
      <div class="label">图表</div>
      {chart_html}
    </div>

    <footer>由 DataAgent Pro 自动生成 · 代码在受限沙箱中执行</footer>
  </div>
</body>
</html>"""


def save_artifact(content: str | bytes, kind: str) -> tuple[str, str]:
    """保存导出文件，返回 (文件名, 绝对路径)。"""
    if kind not in _KINDS:
        raise ExportError(f"不支持的导出类型：{kind}")
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    suffix = {"png": "png", "html": "html", "csv": "csv", "report": "html"}[kind]
    filename = f"{kind}-{datetime.now():%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:6]}.{suffix}"
    path = EXPORT_DIR / filename
    if isinstance(content, str):
        path.write_text(content, encoding="utf-8")
    else:
        path.write_bytes(content)
    logger.info("导出文件已保存：%s", filename)
    return filename, str(path)


def resolve_export(filename: str) -> Path:
    if not _SAFE_NAME.match(filename or ""):
        raise ExportError("非法的导出文件名。")
    path = (EXPORT_DIR / filename).resolve()
    if path.parent != EXPORT_DIR.resolve():
        raise ExportError("非法的导出路径。")
    if not path.is_file():
        raise ExportError("导出文件不存在或已被清理。")
    return path


def _as_list(value: Any) -> list[Any]:
    """把 plotly JSON 里的数组（普通 list 或 base64 类型化数组）还原成 list。"""
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, dict) and "bdata" in value:
        try:
            raw = base64.b64decode(str(value["bdata"]))
            dtype = np.dtype(str(value.get("dtype") or "f8"))
            return np.frombuffer(raw, dtype=dtype).tolist()
        except (ValueError, TypeError, KeyError):
            return []
    try:
        return list(value)
    except TypeError:
        return []


def _cell(value: Any) -> Any:
    if value is None:
        return ""
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, ensure_ascii=False)
    return value
