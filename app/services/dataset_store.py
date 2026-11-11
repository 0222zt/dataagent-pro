"""数据集仓库：内存 LRU + TTL，并把原始文件落盘，重启后还能恢复。

初版是"上传 → 立刻分析"，用户每改一次问题就要重新上传同一个文件。
这里把解析后的 DataFrame 存在服务端，换成 ``dataset_id``：

* 换问题 / 多轮对话不再重复上传、重复解析；
* TTL 到期自动清理，容量满了淘汰最旧的；
* 原始字节保存在 ``data/uploads``，进程重启后按需懒加载。
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from app.core.config import UPLOAD_DIR, get_settings
from app.core.errors import DatasetNotFoundError
from app.core.logging import get_logger
from app.services.data_service import load_table

logger = get_logger(__name__)


@dataclass(slots=True)
class Dataset:
    dataset_id: str
    filename: str
    suffix: str
    frame: pd.DataFrame
    meta: dict[str, Any]
    fingerprint: str
    created_at: float
    expires_at: float
    source_path: Path | None = None

    @property
    def ttl_remaining(self) -> int:
        return max(0, int(self.expires_at - time.time()))

    @property
    def rows(self) -> int:
        return int(self.frame.shape[0])

    @property
    def columns(self) -> int:
        return int(self.frame.shape[1])

    def public(self, preview: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "dataset_id": self.dataset_id,
            "filename": self.filename,
            "meta": self.meta,
            "rows": self.rows,
            "columns": self.columns,
            "expires_in": self.ttl_remaining,
        }
        if preview is not None:
            payload["preview"] = preview
        return payload


class DatasetStore:
    """线程安全的内存仓库 + 磁盘持久化。"""

    def __init__(
        self,
        ttl: int | None = None,
        capacity: int | None = None,
        directory: Path | None = None,
    ) -> None:
        settings = get_settings()
        self._ttl = ttl if ttl is not None else settings.dataset_ttl
        self._capacity = capacity if capacity is not None else settings.max_datasets
        self._dir = directory or UPLOAD_DIR
        self._dir.mkdir(parents=True, exist_ok=True)
        self._items: OrderedDict[str, Dataset] = OrderedDict()
        self._lock = threading.RLock()

    # ------------------------------------------------------------------ #
    # 写入
    # ------------------------------------------------------------------ #
    def put(
        self,
        *,
        filename: str,
        content: bytes,
        frame: pd.DataFrame,
        meta: dict[str, Any],
        fingerprint: str,
    ) -> Dataset:
        dataset_id = uuid.uuid4().hex
        suffix = Path(filename).suffix.lower() or ".csv"
        source_path = self._dir / f"{dataset_id}{suffix}"
        meta_path = self._meta_path(dataset_id)

        try:
            source_path.write_bytes(content)
        except OSError as exc:  # 磁盘满 / 权限问题不应该阻断本次分析
            logger.warning("数据集落盘失败（仅保留内存副本）：%s", exc)
            source_path = None  # type: ignore[assignment]

        now = time.time()
        dataset = Dataset(
            dataset_id=dataset_id,
            filename=filename,
            suffix=suffix,
            frame=frame,
            meta=meta,
            fingerprint=fingerprint,
            created_at=now,
            expires_at=now + self._ttl,
            source_path=source_path,
        )

        with self._lock:
            self._evict_if_needed()
            self._items[dataset_id] = dataset
            self._write_meta(dataset)

        return dataset

    def _evict_if_needed(self) -> None:
        while len(self._items) >= self._capacity:
            _, evicted = self._items.popitem(last=False)
            self._remove_files(evicted.dataset_id)

    def _write_meta(self, dataset: Dataset) -> None:
        if dataset.source_path is None:
            return
        payload = {
            "dataset_id": dataset.dataset_id,
            "filename": dataset.filename,
            "suffix": dataset.suffix,
            "fingerprint": dataset.fingerprint,
            "meta": dataset.meta,
            "created_at": dataset.created_at,
            "expires_at": dataset.expires_at,
            "source": dataset.source_path.name,
        }
        try:
            self._meta_path(dataset.dataset_id).write_text(
                json.dumps(payload, ensure_ascii=False), encoding="utf-8"
            )
        except OSError as exc:
            logger.warning("数据集元信息落盘失败：%s", exc)

    # ------------------------------------------------------------------ #
    # 读取
    # ------------------------------------------------------------------ #
    def get(self, dataset_id: str) -> Dataset:
        self.purge()
        with self._lock:
            dataset = self._items.get(dataset_id)
            if dataset is not None:
                self._items.move_to_end(dataset_id)
                return dataset

        dataset = self._load_from_disk(dataset_id)
        if dataset is not None:
            return dataset
        raise DatasetNotFoundError(f"数据集 {dataset_id} 不存在或已过期。")

    def _load_from_disk(self, dataset_id: str) -> Dataset | None:
        meta_path = self._meta_path(dataset_id)
        if not meta_path.is_file():
            return None
        try:
            payload = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self._remove_files(dataset_id)
            return None

        if float(payload.get("expires_at") or 0) <= time.time():
            self._remove_files(dataset_id)
            return None

        source = self._dir / str(payload.get("source") or "")
        if not source.is_file():
            self._remove_files(dataset_id)
            return None

        try:
            content = source.read_bytes()
            frame = load_table(str(payload.get("filename") or "upload.csv"), content)
        except Exception as exc:  # noqa: BLE001 - 损坏的缓存直接丢弃
            logger.warning("从磁盘恢复数据集失败：%s", exc)
            self._remove_files(dataset_id)
            return None

        dataset = Dataset(
            dataset_id=dataset_id,
            filename=str(payload.get("filename") or "upload.csv"),
            suffix=str(payload.get("suffix") or ".csv"),
            frame=frame,
            meta=payload.get("meta") or {},
            fingerprint=str(payload.get("fingerprint") or ""),
            created_at=float(payload.get("created_at") or time.time()),
            expires_at=float(payload.get("expires_at") or time.time()),
            source_path=source,
        )
        with self._lock:
            self._evict_if_needed()
            self._items[dataset_id] = dataset
        logger.info("数据集 %s 已从磁盘恢复", dataset_id[:8])
        return dataset

    # ------------------------------------------------------------------ #
    # 删除 / 清理
    # ------------------------------------------------------------------ #
    def discard(self, dataset_id: str) -> bool:
        with self._lock:
            removed = self._items.pop(dataset_id, None) is not None
            self._remove_files(dataset_id)
            return removed

    def purge(self) -> int:
        now = time.time()
        expired: list[str] = []
        with self._lock:
            for key, item in list(self._items.items()):
                if item.expires_at <= now:
                    expired.append(key)
            for key in expired:
                self._items.pop(key, None)
                self._remove_files(key)

        # 清理只在磁盘上的过期会话
        for meta_path in self._dir.glob("*.json"):
            if meta_path.stem in self._items:
                continue
            try:
                payload = json.loads(meta_path.read_text(encoding="utf-8"))
                if float(payload.get("expires_at") or 0) <= now:
                    self._remove_files(meta_path.stem)
            except (OSError, ValueError):
                continue

        return len(expired)

    def _meta_path(self, dataset_id: str) -> Path:
        return self._dir / f"{dataset_id}.json"

    def _remove_files(self, dataset_id: str) -> None:
        meta_path = self._meta_path(dataset_id)
        try:
            if meta_path.is_file():
                payload = json.loads(meta_path.read_text(encoding="utf-8"))
                source = self._dir / str(payload.get("source") or "")
                if source.is_file() and source.parent == self._dir:
                    source.unlink(missing_ok=True)
        except (OSError, ValueError):
            pass
        try:
            meta_path.unlink(missing_ok=True)
        except OSError:
            pass

    # ------------------------------------------------------------------ #
    # 统计
    # ------------------------------------------------------------------ #
    def stats(self) -> dict[str, Any]:
        with self._lock:
            memory = len(self._items)
        try:
            disk = sum(1 for _ in self._dir.glob("*.json"))
        except OSError:
            disk = 0
        return {
            "datasets": memory,
            "on_disk": disk,
            "capacity": self._capacity,
            "ttl_seconds": self._ttl,
        }


_store: DatasetStore | None = None
_store_lock = threading.Lock()


def get_store() -> DatasetStore:
    global _store
    with _store_lock:
        if _store is None:
            _store = DatasetStore()
        return _store
