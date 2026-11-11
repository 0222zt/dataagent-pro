"""会话历史：SQLite 持久化。

为什么要落库：内存里的 DataFrame 会过期，但"我上周让它画过什么图"
是有价值的。这里保存：

* ``sessions``  一个数据集上的多轮对话；
* ``messages``  每一轮的需求、生成代码、图表 JSON、模型、耗时、错误；
* ``metrics``   每次模型调用的 token / 延迟，用于成本和稳定性观察。
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from app.core.config import DB_PATH
from app.core.logging import get_logger

logger = get_logger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    dataset_id TEXT NOT NULL,
    title TEXT NOT NULL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS messages (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    role TEXT NOT NULL,
    prompt TEXT,
    code TEXT,
    chart_json TEXT,
    model TEXT,
    attempts INTEGER DEFAULT 0,
    elapsed_ms INTEGER DEFAULT 0,
    error TEXT,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id, created_at);
CREATE TABLE IF NOT EXISTS metrics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    kind TEXT NOT NULL,
    model TEXT,
    prompt_tokens INTEGER DEFAULT 0,
    completion_tokens INTEGER DEFAULT 0,
    latency_ms INTEGER DEFAULT 0,
    ok INTEGER DEFAULT 1
);
"""


class HistoryStore:
    def __init__(self, path: Path | str | None = None) -> None:
        self._path = Path(path or DB_PATH)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._ts_lock = threading.Lock()
        self._last_ts = 0.0
        self._conn = sqlite3.connect(str(self._path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock, self._conn:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.executescript(_SCHEMA)

    def _now(self) -> float:
        """单调递增时间戳：Windows 时钟精度粗，快速连写会拿到相同值。"""
        with self._ts_lock:
            now = time.time()
            if now <= self._last_ts:
                now = self._last_ts + 0.0001
            self._last_ts = now
            return now

    # ------------------------------------------------------------------ #
    # 会话
    # ------------------------------------------------------------------ #
    def create_session(self, dataset_id: str, title: str = "") -> dict[str, Any]:
        now = self._now()
        session_id = uuid.uuid4().hex
        clean_title = (title or "新会话").strip()[:40] or "新会话"
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO sessions (id, dataset_id, title, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (session_id, dataset_id, clean_title, now, now),
            )
        return {
            "session_id": session_id,
            "dataset_id": dataset_id,
            "title": clean_title,
            "created_at": now,
            "updated_at": now,
            "messages": 0,
        }

    def get_session(self, session_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM sessions WHERE id = ?", (session_id,)
            ).fetchone()
            if row is None:
                return None
            messages = [
                dict(item)
                for item in self._conn.execute(
                    "SELECT * FROM messages WHERE session_id = ? ORDER BY created_at ASC",
                    (session_id,),
                ).fetchall()
            ]
        payload = dict(row)
        payload["session_id"] = payload.pop("id")
        for message in messages:
            message["chart_json"] = _safe_json(message.get("chart_json"))
        payload["messages"] = messages
        return payload

    def list_sessions(self, limit: int = 30) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT s.*, COUNT(m.id) AS message_count,
                       MAX(m.created_at) AS last_message_at
                FROM sessions s
                LEFT JOIN messages m ON m.session_id = s.id
                GROUP BY s.id
                ORDER BY s.updated_at DESC
                LIMIT ?
                """,
                (max(1, limit),),
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["session_id"] = item.pop("id")
            result.append(item)
        return result

    def touch_session(self, session_id: str, title: str | None = None) -> None:
        now = self._now()
        with self._lock, self._conn:
            if title:
                self._conn.execute(
                    "UPDATE sessions SET updated_at = ?, title = ? WHERE id = ?",
                    (now, title[:40], session_id),
                )
            else:
                self._conn.execute(
                    "UPDATE sessions SET updated_at = ? WHERE id = ?", (now, session_id)
                )

    def delete_session(self, session_id: str) -> bool:
        with self._lock, self._conn:
            cursor = self._conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
            self._conn.execute("DELETE FROM messages WHERE session_id = ?", (session_id,))
            return cursor.rowcount > 0

    # ------------------------------------------------------------------ #
    # 消息
    # ------------------------------------------------------------------ #
    def add_message(
        self,
        session_id: str,
        *,
        role: str,
        prompt: str = "",
        code: str = "",
        chart_json: str = "",
        model: str = "",
        attempts: int = 0,
        elapsed_ms: int = 0,
        error: str = "",
    ) -> str:
        message_id = uuid.uuid4().hex
        now = self._now()
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO messages
                    (id, session_id, role, prompt, code, chart_json, model,
                     attempts, elapsed_ms, error, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    message_id,
                    session_id,
                    role,
                    prompt,
                    code,
                    chart_json,
                    model,
                    int(attempts),
                    int(elapsed_ms),
                    error,
                    now,
                ),
            )
            self._conn.execute(
                "UPDATE sessions SET updated_at = ? WHERE id = ?", (now, session_id)
            )
        return message_id

    def recent_turns(self, session_id: str, limit: int = 3) -> list[dict[str, str]]:
        """最近几轮成功的需求 + 代码，作为下一轮的上下文。"""
        if not session_id:
            return []
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT prompt, code FROM messages
                WHERE session_id = ? AND role = 'assistant' AND error = ''
                ORDER BY created_at DESC LIMIT ?
                """,
                (session_id, max(1, limit)),
            ).fetchall()
        return [
            {"prompt": row["prompt"] or "", "code": row["code"] or ""}
            for row in reversed(rows)
        ]

    def list_charts(self, limit: int = 40, include_chart: bool = False) -> list[dict[str, Any]]:
        columns = "id, session_id, prompt, model, attempts, elapsed_ms, created_at"
        if include_chart:
            columns += ", chart_json, code"
        with self._lock:
            rows = self._conn.execute(
                f"SELECT {columns} FROM messages "
                "WHERE role = 'assistant' AND error = '' AND chart_json IS NOT NULL "
                "AND chart_json != '' ORDER BY created_at DESC LIMIT ?",
                (max(1, limit),),
            ).fetchall()
        result = [dict(row) for row in rows]
        if include_chart:
            for item in result:
                item["chart_json"] = _safe_json(item.get("chart_json"))
        return result

    def get_message_chart(self, message_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM messages WHERE id = ?", (message_id,)
            ).fetchone()
        if row is None:
            return None
        payload = dict(row)
        payload["chart_json"] = _safe_json(payload.get("chart_json"))
        return payload

    # ------------------------------------------------------------------ #
    # 指标
    # ------------------------------------------------------------------ #
    def record_metric(
        self,
        *,
        kind: str,
        model: str = "",
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        latency_ms: int = 0,
        ok: bool = True,
    ) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO metrics (ts, kind, model, prompt_tokens, completion_tokens, "
                "latency_ms, ok) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    self._now(),
                    kind,
                    model,
                    int(prompt_tokens),
                    int(completion_tokens),
                    int(latency_ms),
                    1 if ok else 0,
                ),
            )

    def stats(self) -> dict[str, Any]:
        with self._lock:
            sessions = self._conn.execute("SELECT COUNT(*) AS c FROM sessions").fetchone()["c"]
            messages = self._conn.execute("SELECT COUNT(*) AS c FROM messages").fetchone()["c"]
            charts = self._conn.execute(
                "SELECT COUNT(*) AS c FROM messages WHERE chart_json IS NOT NULL "
                "AND chart_json != '' AND error = ''"
            ).fetchone()["c"]
            metric = self._conn.execute(
                "SELECT COUNT(*) AS calls, COALESCE(SUM(prompt_tokens), 0) AS pt, "
                "COALESCE(SUM(completion_tokens), 0) AS ct FROM metrics"
            ).fetchone()
        return {
            "sessions": sessions,
            "messages": messages,
            "charts": charts,
            "llm_calls": metric["calls"],
            "prompt_tokens": metric["pt"],
            "completion_tokens": metric["ct"],
            "database": str(self._path.name),
        }

    def close(self) -> None:
        with self._lock:
            self._conn.close()


def _safe_json(raw: Any) -> Any:
    if not raw:
        return None
    if isinstance(raw, (dict, list)):
        return raw
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return None


_history: HistoryStore | None = None
_history_lock = threading.Lock()


def get_history() -> HistoryStore:
    global _history
    with _history_lock:
        if _history is None:
            _history = HistoryStore()
        return _history
