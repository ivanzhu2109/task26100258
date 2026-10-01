"""剧本执行引擎：启动运行、推进运行、查询运行。"""
from __future__ import annotations

import json
import uuid
from typing import Any, Optional

from .models import (
    CHECK_IOC_CONFIDENCE_GT_80,
    CHECK_SEVERITY_EQ_CRITICAL,
    END_MARKERS,
    RUN_STATUS_COMPLETED,
    RUN_STATUS_RUNNING,
    Node,
    Playbook,
    RunInfo,
    TimelineRecord,
    parse_playbook,
)
from .store import Store, run_record_to_info


class PlaybookEngine:
    """上层直接调用的入口。所有状态持久化到 SQLite，可在进程重启后继续。"""

    def __init__(self, db_path: str) -> None:
        self._store = Store(db_path)

    def close(self) -> None:
        self._store.close()

    def __enter__(self) -> "PlaybookEngine":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ---- 启动 ----

    def start_run(
        self,
        event_id: str,
        playbook: Any,
        ioc_confidence: float,
        severity: str,
    ) -> str:
        """启动一次运行，固定剧本快照与 IOC/严重级别，返回 run_id。"""
        parsed = parse_playbook(playbook)
        snapshot = json.dumps(
            json.loads(playbook) if isinstance(playbook, str) else playbook,
            sort_keys=True,
        )
        run_id = uuid.uuid4().hex
        self._store.ensure_event(event_id)
        self._store.create_run(
            run_id=run_id,
            event_id=event_id,
            playbook_json=snapshot,
            playbook_id=parsed.playbook_id,
            ioc_confidence=float(ioc_confidence),
            severity=severity,
            next_node=parsed.start,
        )
        return run_id

    # ---- 推进 ----

    def advance_run(self, run_id: str) -> RunInfo:
        """推进运行一个节点；已完成运行再次推进不产生任何新处置。"""
        record = self._require_run(run_id)
        if record["status"] == RUN_STATUS_COMPLETED:
            return run_record_to_info(record)
        current_id = record["next_node"]
        if current_id is None:
            self._store.update_run_progress(run_id, None, RUN_STATUS_COMPLETED)
            return run_record_to_info(self._require_run(run_id))

        playbook = parse_playbook(record["playbook_json"])
        node = playbook.nodes[current_id]
        if not self._store.is_node_executed(run_id, current_id):
            self._execute_node(record, playbook, node)

        next_id = self._resolve_next(record, node)
        status = RUN_STATUS_COMPLETED if next_id is None else RUN_STATUS_RUNNING
        self._store.update_run_progress(run_id, next_id, status)
        return run_record_to_info(self._require_run(run_id))

    def _execute_node(
        self, record: dict[str, Any], playbook: Playbook, node: Node
    ) -> None:
        run_id = record["run_id"]
        if node.kind == "condition":
            self._store.mark_condition_executed(run_id, node.node_id)
            return
        detail = (
            f"playbook={playbook.playbook_id} node={node.node_id}"
            f" action={node.action} run={run_id}"
        )
        self._store.apply_action(
            run_id=run_id,
            node_id=node.node_id,
            event_id=record["event_id"],
            action=node.action,
            detail=detail,
        )

    @staticmethod
    def _resolve_next(record: dict[str, Any], node: Node) -> Optional[str]:
        if node.kind == "condition":
            if node.check == CHECK_IOC_CONFIDENCE_GT_80:
                taken = record["ioc_confidence"] > 80
            elif node.check == CHECK_SEVERITY_EQ_CRITICAL:
                taken = record["severity"] == "critical"
            else:  # pragma: no cover - 解析阶段已校验
                raise ValueError(f"未知条件: {node.check}")
            target = node.on_true if taken else node.on_false
        else:
            target = node.next
        return None if target in END_MARKERS else target

    # ---- 查询 ----

    def get_run(self, run_id: str) -> RunInfo:
        """查询运行状态（区分 running / completed）。"""
        return run_record_to_info(self._require_run(run_id))

    def get_event(self, event_id: str) -> Optional[dict[str, Any]]:
        """查询事件本地状态（assignee / status）。"""
        return self._store.get_event(event_id)

    def get_timeline(self, event_id: str) -> list[TimelineRecord]:
        """查询事件的持久处置时间线。"""
        return self._store.get_timeline(event_id)

    def _require_run(self, run_id: str) -> dict[str, Any]:
        record = self._store.get_run_record(run_id)
        if record is None:
            raise KeyError(f"运行不存在: {run_id}")
        return record
