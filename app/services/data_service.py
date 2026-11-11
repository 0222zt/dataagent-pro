"""表格解析、清洗、元数据抽取、JSON 安全化。

相比"能跑"的原型，这里补齐了线上必须做的事：

1. 上传体按块限流读取，不先读进内存再拒绝；
2. CSV 多编码 fallback（UTF-8 / GB18030 / GBK / BIG5）+ 分隔符嗅探；
3. 支持 .csv / .tsv / .xlsx / .xls / .json；
4. 列名清洗、去重、丢空列、去空行；
5. 元数据带 dtype / 缺失率 / 样本值 / 数值统计 / 相关性，让模型知道
   "这一列能不能求和"；
6. 预览与统计统一转成 JSON 安全类型（NaN / NaT / numpy 标量）。
"""

from __future__ import annotations

import hashlib
import io
import json
import math
from datetime import date, datetime
from typing import Any

import pandas as pd
from pandas.api import types as ptypes

from app.core.config import get_settings
from app.core.errors import (
    EmptyDatasetError,
    FileTooLargeError,
    InvalidInputError,
    UnsupportedFileError,
)
from app.core.logging import get_logger

logger = get_logger(__name__)

SUPPORTED_SUFFIXES = (".csv", ".tsv", ".xlsx", ".xls", ".json")
_ENCODINGS = ("utf-8-sig", "utf-8", "gb18030", "gbk", "big5")
PREVIEW_ROWS = 10
_SAMPLE_VALUES = 3
_MAX_CORRELATION_PAIRS = 3


# --------------------------------------------------------------------------- #
# 读取
# --------------------------------------------------------------------------- #
def load_table(filename: str, content: bytes) -> pd.DataFrame:
    settings = get_settings()
    if not content:
        raise EmptyDatasetError("上传的文件是空的。")
    if len(content) > settings.max_file_size:
        raise FileTooLargeError(
            f"文件 {len(content) / 1024 / 1024:.2f} MB，超过 "
            f"{settings.max_file_size / 1024 / 1024:.1f} MB 限制。"
        )

    suffix = suffix_of(filename)
    if suffix not in SUPPORTED_SUFFIXES:
        raise UnsupportedFileError(f"不支持的文件类型：{suffix or '未知'}")

    if suffix == ".csv":
        frame = _read_csv(content)
    elif suffix == ".tsv":
        frame = _read_tsv(content)
    elif suffix in (".xlsx", ".xls"):
        frame = _read_excel(content)
    else:
        frame = _read_json(content)

    return normalize_frame(frame, filename)


def suffix_of(filename: str) -> str:
    name = (filename or "").strip().lower()
    dot = name.rfind(".")
    return name[dot:] if dot > 0 else ""


def _read_csv(content: bytes) -> pd.DataFrame:
    last_error: Exception | None = None
    for encoding in _ENCODINGS:
        try:
            frame = pd.read_csv(io.BytesIO(content), encoding=encoding)
        except (UnicodeDecodeError, pd.errors.ParserError, pd.errors.EmptyDataError) as exc:
            last_error = exc
            continue

        # 分隔符不是逗号时（分号 / Tab / 竖线），pandas 会解析成单列，再嗅探一次。
        if frame.shape[1] == 1:
            sniffed = _read_with_sniffing(content, encoding)
            if sniffed is not None and sniffed.shape[1] > 1:
                return sniffed
        return frame

    raise InvalidInputError(
        f"CSV 解析失败：{last_error}",
        hint="请确认文件没有加密，或另存为 UTF-8 编码的 CSV。",
    ) from last_error


def _read_with_sniffing(content: bytes, encoding: str) -> pd.DataFrame | None:
    try:
        return pd.read_csv(io.BytesIO(content), encoding=encoding, sep=None, engine="python")
    except Exception:  # noqa: BLE001 - 嗅探失败就退回默认解析
        return None


def _read_tsv(content: bytes) -> pd.DataFrame:
    for encoding in _ENCODINGS:
        try:
            return pd.read_csv(io.BytesIO(content), encoding=encoding, sep="\t")
        except (UnicodeDecodeError, pd.errors.ParserError, pd.errors.EmptyDataError):
            continue
    raise InvalidInputError("TSV 解析失败。", hint="请确认文件是以 Tab 分隔的纯文本。")


def _read_excel(content: bytes) -> pd.DataFrame:
    try:
        return pd.read_excel(io.BytesIO(content), sheet_name=0)
    except Exception as exc:  # noqa: BLE001 - 统一转换成业务异常
        raise InvalidInputError(
            f"Excel 解析失败：{exc}",
            hint="请确认是 .xlsx / .xls 文件，且第一个工作表里有表头。",
        ) from exc


def _read_json(content: bytes) -> pd.DataFrame:
    text: str | None = None
    for encoding in _ENCODINGS:
        try:
            text = content.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        raise InvalidInputError("JSON 文件编码无法识别。")

    try:
        payload = json.loads(text)
    except ValueError as exc:
        raise InvalidInputError(f"JSON 解析失败：{exc}") from exc

    if isinstance(payload, list):
        return pd.json_normalize(payload)
    if isinstance(payload, dict):
        # 兼容 {"data": [...]} 这类包了一层的结构
        for value in payload.values():
            if isinstance(value, list) and value and isinstance(value[0], dict):
                return pd.json_normalize(value)
        return pd.json_normalize(payload)
    raise InvalidInputError("JSON 顶层必须是数组或对象。", hint="例如 [{...}, {...}]。")


# --------------------------------------------------------------------------- #
# 清洗
# --------------------------------------------------------------------------- #
def normalize_frame(frame: pd.DataFrame, filename: str = "") -> pd.DataFrame:
    if frame.shape[1] == 0 or frame.shape[0] == 0:
        raise EmptyDatasetError("表格里没有可用的行或列。")

    frame = frame.copy()
    frame.columns = [_clean_column(name) for name in frame.columns]
    # 先重命名去重，避免同名列让 frame[col] 返回 DataFrame。
    frame = _dedupe_columns(frame)
    unnamed = [
        col
        for col in frame.columns
        if str(col).startswith("未命名列") and frame[col].isna().all()
    ]
    if unnamed:
        frame = frame.drop(columns=unnamed)
    frame = frame.dropna(how="all")
    frame = frame.loc[:, ~frame.isna().all()]
    frame = frame.reset_index(drop=True)

    if frame.empty or frame.shape[1] == 0:
        raise EmptyDatasetError("清理空行 / 空列后没有剩余数据。")

    settings = get_settings()
    if len(frame) > settings.max_rows:
        logger.warning(
            "文件 %s 共 %s 行，超过上限 %s，已截断。",
            filename or "<upload>",
            len(frame),
            settings.max_rows,
        )
        frame = frame.head(settings.max_rows)

    return frame


def _clean_column(name: Any) -> str:
    text = str(name).replace("\r", " ").replace("\n", " ").strip()
    while "  " in text:
        text = text.replace("  ", " ")
    return text or "未命名列"


def _dedupe_columns(frame: pd.DataFrame) -> pd.DataFrame:
    seen: dict[str, int] = {}
    columns: list[str] = []
    for column in frame.columns:
        base = str(column)
        if base in seen:
            seen[base] += 1
            columns.append(f"{base}_{seen[base]}")
        else:
            seen[base] = 1
            columns.append(base)
    frame.columns = columns
    return frame


# --------------------------------------------------------------------------- #
# 元数据 / 预览
# --------------------------------------------------------------------------- #
def describe_frame(frame: pd.DataFrame) -> dict[str, Any]:
    """生成给大模型看的数据字典 + 给前端展示的数据画像。"""
    columns: list[dict[str, Any]] = []
    for name in frame.columns:
        series = frame[name]
        total = int(series.shape[0])
        nulls = int(series.isna().sum())
        try:
            unique = int(series.nunique(dropna=True))
        except TypeError:
            unique = -1
        kind = kind_of(series)
        columns.append(
            {
                "name": str(name),
                "dtype": str(series.dtype),
                "kind": kind,
                "nulls": nulls,
                "null_ratio": round(nulls / total, 4) if total else 0.0,
                "unique": unique,
                "sample": sample_values(series),
                "stats": _stats_of(series, kind),
            }
        )

    kinds = {item["kind"] for item in columns}
    numeric_columns = [c["name"] for c in columns if c["kind"] == "numeric"]
    completeness = 1.0
    if columns:
        completeness = 1.0 - sum(c["null_ratio"] for c in columns) / len(columns)

    duplicated = int(frame.duplicated().sum())
    duplicate_ratio = duplicated / len(frame) if len(frame) else 0.0
    quality = round(
        max(0.0, min(1.0, 0.65 * completeness + 0.35 * (1 - duplicate_ratio))), 3
    )

    return {
        "shape": {"rows": int(frame.shape[0]), "columns": int(frame.shape[1])},
        "columns": columns,
        "numeric_columns": numeric_columns,
        "datetime_columns": [c["name"] for c in columns if c["kind"] == "datetime"],
        "boolean_columns": [c["name"] for c in columns if c["kind"] == "boolean"],
        "text_columns": [c["name"] for c in columns if c["kind"] == "text"],
        "category_columns": [c["name"] for c in columns if c["kind"] in {"text", "boolean"}],
        "has_datetime": "datetime" in kinds,
        "duplicated_rows": duplicated,
        "duplicate_ratio": round(duplicate_ratio, 4),
        "missing_cells": int(frame.isna().sum().sum()),
        "completeness": round(completeness, 4),
        "quality_score": quality,
        "memory_bytes": int(frame.memory_usage(deep=True).sum()),
        "correlations": _top_correlations(frame, numeric_columns),
        "time_range": _time_range(frame, columns),
    }


def kind_of(series: pd.Series) -> str:
    if ptypes.is_bool_dtype(series):
        return "boolean"
    if ptypes.is_datetime64_any_dtype(series):
        return "datetime"
    if ptypes.is_numeric_dtype(series):
        return "numeric"
    return "text"


def sample_values(series: pd.Series, limit: int = _SAMPLE_VALUES) -> list[Any]:
    try:
        values = series.dropna().unique()[:limit]
    except Exception:  # noqa: BLE001 - 取样本值失败不影响主流程
        return []
    return [json_safe(value) for value in values]


def _stats_of(series: pd.Series, kind: str) -> dict[str, Any]:
    """只给真正有用的统计，避免元数据太长把 token 烧光。"""
    if kind != "numeric":
        return {}
    clean = series.dropna()
    if clean.empty:
        return {}
    try:
        stats = {
            "min": json_safe(clean.min()),
            "max": json_safe(clean.max()),
            "mean": json_safe(clean.mean()),
            "median": json_safe(clean.median()),
            "sum": json_safe(clean.sum()),
        }
        if len(clean) > 3:
            q1, q3 = clean.quantile(0.25), clean.quantile(0.75)
            iqr = q3 - q1
            if iqr:
                outliers = clean[(clean < q1 - 1.5 * iqr) | (clean > q3 + 1.5 * iqr)]
                stats["outliers"] = int(len(outliers))
        return {key: value for key, value in stats.items() if value is not None}
    except Exception:  # noqa: BLE001
        return {}


def _top_correlations(frame: pd.DataFrame, numeric_columns: list[str]) -> list[dict[str, Any]]:
    if len(numeric_columns) < 2:
        return []
    try:
        corr = frame[numeric_columns].corr(numeric_only=True)
    except Exception:  # noqa: BLE001
        return []

    pairs: list[dict[str, Any]] = []
    for i, left in enumerate(numeric_columns):
        for right in numeric_columns[i + 1 :]:
            try:
                value = corr.loc[left, right]
            except KeyError:
                continue
            if pd.isna(value) or abs(float(value)) < 0.5:
                continue
            pairs.append(
                {"left": left, "right": right, "r": round(float(value), 2)}
            )
    pairs.sort(key=lambda item: abs(item["r"]), reverse=True)
    return pairs[:_MAX_CORRELATION_PAIRS]


def _time_range(frame: pd.DataFrame, columns: list[dict[str, Any]]) -> dict[str, str] | None:
    names = [c["name"] for c in columns if c["kind"] == "datetime"]
    if not names:
        # 列名像时间、但 dtype 还是字符串时，尝试解析第一列
        guessed = [c["name"] for c in columns if any(
            token in str(c["name"]).lower()
            for token in ("date", "time", "日期", "时间", "月份", "年月")
        )]
        names = guessed[:1]
    if not names:
        return None
    try:
        series = pd.to_datetime(
            frame[names[0]], errors="coerce", format="mixed"
        ).dropna()
        if series.empty:
            return None
        return {
            "column": names[0],
            "start": json_safe(series.min()),
            "end": json_safe(series.max()),
        }
    except Exception:  # noqa: BLE001
        return None


def preview_records(frame: pd.DataFrame, limit: int = PREVIEW_ROWS) -> list[dict[str, Any]]:
    head = frame.head(limit)
    return [
        {str(key): json_safe(value) for key, value in row.items()}
        for row in head.to_dict(orient="records")
    ]


def json_safe(value: Any) -> Any:
    """把 numpy / pandas / 时间类型转成 JSON 能序列化的值。"""
    if value is None or value is pd.NaT:
        return None
    if isinstance(value, (bool, str)):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return None if (math.isnan(value) or math.isinf(value)) else value
    if isinstance(value, (pd.Timestamp, datetime)):
        return None if pd.isna(value) else value.isoformat(sep=" ")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, (list, tuple, dict, set)):
        return str(value)

    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        return str(value)

    item = getattr(value, "item", None)
    if callable(item):
        try:
            return json_safe(item())
        except Exception:  # noqa: BLE001
            pass
    return str(value)


def fingerprint_frame(frame: pd.DataFrame) -> str:
    """内容指纹：列 + 形状 + 值的哈希，用于结果缓存。"""
    digest = hashlib.sha256()
    digest.update("|".join(str(col) for col in frame.columns).encode("utf-8"))
    digest.update(str(frame.shape).encode("utf-8"))
    try:
        hashed = pd.util.hash_pandas_object(frame, index=False).values.tobytes()
    except Exception:  # noqa: BLE001 - 含不可哈希对象时退回字符串化
        hashed = frame.astype(str).to_csv(index=False).encode("utf-8")
    digest.update(hashed)
    return digest.hexdigest()
