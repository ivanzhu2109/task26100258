"""SQLite 持久化层：运行、事件、时间线、节点执行记录。"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Any, Optional

from .models import (
    ACTION_ASSIGN_LEAD,
    ACTION_CONTAINMENT,
    RUN_STATUS_RUNNING,
    RunInfo,
    TimelineRecord,
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    event_id TEXT NOT NULL,
    playbook_json TEXT NOT NULL,
    playbook_id TEXT NOT NULL,
    ioc_confidence REAL NOT NULL,
    severity TEXT NOT NULL,
    status TEXT NOT NULL,
    next_node TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
    event_id TEXT PRIMARY KEY,
    assignee TEXT,
    status TEXT
);
CREATE TABLE IF NOT EXISTS timeline (
    record_id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    node_id TEXT NOT NULL,
    action TEXT NOT NULL,
    detail TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (run_id, node_id)
);
CREATE TABLE IF NOT EXISTS node_executions (
    run_id TEXT NOT NULL,
    node_id TEXT NOT NULL,
    executed_at TEXT NOT NULL,
    PRIMARY KEY (run_id, node_id)
);
"""

# 动作对事件本地状态的影响
ACTION_EFFECTS = {
    ACTION_ASSIGN_LEAD: ("assignee", "Lead"),
    ACTION_CONTAINMENT: ("status", "containment"),
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Store:
    """对 SQLite 的薄封装；写操作在单个事务内完成。"""

    def __init__(self, db_path: str) -> None:
        self._conn = sqlite3.connect(db_path)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    # ---- 运行 ----

    def create_run(
        self,
        run_id: str,
        event_id: str,
        playbook_json: str,
        playbook_id: str,
        ioc_confidence: float,
        severity: str,
        next_node: Optional[str],
    ) -> None:
        with self._conn:
            self._conn.execute(
                "INSERT INTO runs (run_id, event_id, playbook_json, playbook_id,"
                " ioc_confidence, severity, status, next_node, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    run_id,
                    event_id,
                    playbook_json,
                    playbook_id,
                    ioc_confidence,
                    severity,
                    RUN_STATUS_RUNNING,
                    next_node,
                    _now(),
                ),
            )

    def get_run_record(self, run_id: str) -> Optional[dict[str, Any]]:
        row = self._conn.execute(
            "SELECT * FROM runs WHERE run_id = ?", (run_id,)
        ).fetchone()
        return dict(row) if row is not None else None

    def update_run_progress(
        self, run_id: str, next_node: Optional[str], status: str
    ) -> None:
        with self._conn:
            self._conn.execute(
                "UPDATE runs SET next_node = ?, status = ? WHERE run_id = ?",
                (next_node, status, run_id),
            )

    # ---- 事件 ----

    def ensure_event(self, event_id: str) -> None:
        with self._conn:
            self._conn.execute(
                "INSERT OR IGNORE INTO events (event_id, assignee, status)"
                " VALUES (?, NULL, NULL)",
                (event_id,),
            )

    def get_event(self, event_id: str) -> Optional[dict[str, Any]]:
        row = self._conn.execute(
            "SELECT * FROM events WHERE event_id = ?", (event_id,)
        ).fetchone()
        return dict(row) if row is not None else None

    # ---- 节点执行与动作生效（原子） ----

    def is_node_executed(self, run_id: str, node_id: str) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM node_executions WHERE run_id = ? AND node_id = ?",
            (run_id, node_id),
        ).fetchone()
        return row is not None

    def apply_action(
        self,
        run_id: str,
        node_id: str,
        event_id: str,
        action: str,
        detail: str,
    ) -> None:
        """在单个事务中：标记节点已执行、修改事件、追加处置记录。

        同一 (run_id, node_id) 重复调用不产生额外效果或记录。
        """
        column, value = ACTION_EFFECTS[action]
        with self._conn:
            self._conn.execute(
                "INSERT OR IGNORE INTO node_executions (run_id, node_id, executed_at)"
                " VALUES (?, ?, ?)",
                (run_id, node_id, _now()),
            )
            self._conn.execute(
                f"UPDATE events SET {column} = ? WHERE event_id = ?",
                (value, event_id),
            )
            self._conn.execute(
                "INSERT OR IGNORE INTO timeline"
                " (event_id, run_id, node_id, action, detail, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (event_id, run_id, node_id, action, detail, _now()),
            )

    def mark_condition_executed(self, run_id: str, node_id: str) -> None:
        with self._conn:
            self._conn.execute(
                "INSERT OR IGNORE INTO node_executions (run_id, node_id, executed_at)"
                " VALUES (?, ?, ?)",
                (run_id, node_id, _now()),
            )

    # ---- 时间线 ----

    def get_timeline(self, event_id: str) -> list[TimelineRecord]:
        rows = self._conn.execute(
            "SELECT * FROM timeline WHERE event_id = ? ORDER BY record_id",
            (event_id,),
        ).fetchall()
        return [
            TimelineRecord(
                record_id=r["record_id"],
                event_id=r["event_id"],
                run_id=r["run_id"],
                node_id=r["node_id"],
                action=r["action"],
                detail=r["detail"],
                created_at=r["created_at"],
            )
            for r in rows
        ]


def run_record_to_info(record: dict[str, Any]) -> RunInfo:
    return RunInfo(
        run_id=record["run_id"],
        event_id=record["event_id"],
        status=record["status"],
        playbook_id=record["playbook_id"],
        ioc_confidence=record["ioc_confidence"],
        severity=record["severity"],
    )
