"""SQLite 持久化层。"""

from __future__ import annotations

import json
import sqlite3
from typing import Any, Optional

from .models import (
    RUN_STATUS_RUNNING,
    Incident,
    RunInfo,
    TimelineEntry,
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS incidents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    status TEXT NOT NULL,
    assignee TEXT,
    ioc_confidence INTEGER NOT NULL,
    severity TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    incident_id INTEGER NOT NULL REFERENCES incidents(id),
    playbook_json TEXT NOT NULL,
    ioc_confidence INTEGER NOT NULL,
    severity TEXT NOT NULL,
    status TEXT NOT NULL,
    current_node TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS node_executions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL REFERENCES runs(id),
    node_id TEXT NOT NULL,
    node_type TEXT NOT NULL,
    outcome TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (run_id, node_id)
);
CREATE TABLE IF NOT EXISTS incident_timeline (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    incident_id INTEGER NOT NULL REFERENCES incidents(id),
    run_id INTEGER NOT NULL REFERENCES runs(id),
    node_id TEXT NOT NULL,
    action TEXT NOT NULL,
    detail TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


class Storage:
    def __init__(self, db_path: str) -> None:
        self._conn = sqlite3.connect(db_path)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    # ---- incidents ----

    def create_incident(
        self, ioc_confidence: int, severity: str, status: str, assignee: Optional[str]
    ) -> int:
        cur = self._conn.execute(
            "INSERT INTO incidents (status, assignee, ioc_confidence, severity)"
            " VALUES (?, ?, ?, ?)",
            (status, assignee, ioc_confidence, severity),
        )
        self._conn.commit()
        return cur.lastrowid

    def get_incident(self, incident_id: int) -> Incident:
        row = self._conn.execute(
            "SELECT * FROM incidents WHERE id = ?", (incident_id,)
        ).fetchone()
        if row is None:
            raise KeyError(f"incident {incident_id} not found")
        return Incident(
            id=row["id"],
            status=row["status"],
            assignee=row["assignee"],
            ioc_confidence=row["ioc_confidence"],
            severity=row["severity"],
        )

    def update_incident(self, incident_id: int, **fields: Any) -> None:
        allowed = {"status", "assignee", "ioc_confidence", "severity"}
        unknown = set(fields) - allowed
        if unknown:
            raise ValueError(f"unknown incident fields: {sorted(unknown)}")
        if not fields:
            return
        cols = ", ".join(f"{k} = ?" for k in fields)
        self._conn.execute(
            f"UPDATE incidents SET {cols} WHERE id = ?",
            (*fields.values(), incident_id),
        )

    # ---- runs ----

    def create_run(
        self,
        incident_id: int,
        playbook: dict[str, Any],
        ioc_confidence: int,
        severity: str,
        start_node: str,
    ) -> int:
        cur = self._conn.execute(
            "INSERT INTO runs (incident_id, playbook_json, ioc_confidence, severity, status, current_node)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (incident_id, json.dumps(playbook), ioc_confidence, severity,
             RUN_STATUS_RUNNING, start_node),
        )
        self._conn.commit()
        return cur.lastrowid

    def get_run(self, run_id: int) -> RunInfo:
        row = self._conn.execute(
            "SELECT * FROM runs WHERE id = ?", (run_id,)
        ).fetchone()
        if row is None:
            raise KeyError(f"run {run_id} not found")
        return RunInfo(
            id=row["id"],
            incident_id=row["incident_id"],
            status=row["status"],
            current_node=row["current_node"],
            ioc_confidence=row["ioc_confidence"],
            severity=row["severity"],
            playbook=json.loads(row["playbook_json"]),
        )

    def update_run(self, run_id: int, *, status: str, current_node: str) -> None:
        self._conn.execute(
            "UPDATE runs SET status = ?, current_node = ? WHERE id = ?",
            (status, current_node, run_id),
        )

    # ---- node executions ----

    def has_node_execution(self, run_id: int, node_id: str) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM node_executions WHERE run_id = ? AND node_id = ?",
            (run_id, node_id),
        ).fetchone()
        return row is not None

    def record_node_execution(
        self, run_id: int, node_id: str, node_type: str, outcome: str
    ) -> None:
        self._conn.execute(
            "INSERT INTO node_executions (run_id, node_id, node_type, outcome)"
            " VALUES (?, ?, ?, ?)",
            (run_id, node_id, node_type, outcome),
        )

    # ---- timeline ----

    def append_timeline(
        self, incident_id: int, run_id: int, node_id: str, action: str, detail: str
    ) -> None:
        self._conn.execute(
            "INSERT INTO incident_timeline (incident_id, run_id, node_id, action, detail)"
            " VALUES (?, ?, ?, ?, ?)",
            (incident_id, run_id, node_id, action, detail),
        )

    def get_timeline(self, incident_id: int) -> list[TimelineEntry]:
        rows = self._conn.execute(
            "SELECT * FROM incident_timeline WHERE incident_id = ? ORDER BY id",
            (incident_id,),
        ).fetchall()
        return [
            TimelineEntry(
                id=r["id"],
                incident_id=r["incident_id"],
                run_id=r["run_id"],
                node_id=r["node_id"],
                action=r["action"],
                detail=r["detail"],
                created_at=r["created_at"],
            )
            for r in rows
        ]

    # ---- transactions ----

    def commit(self) -> None:
        self._conn.commit()
