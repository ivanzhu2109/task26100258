"""运行引擎：启动、推进、查询。"""

from __future__ import annotations

from typing import Any, Optional

from .models import (
    ACTION_ASSIGN_LEAD,
    ACTION_SET_CONTAINMENT,
    CHECK_IOC_CONFIDENCE_GT_80,
    CHECK_SEVERITY_EQ_CRITICAL,
    END,
    RUN_STATUS_COMPLETED,
    Incident,
    RunInfo,
    TimelineEntry,
)
from .playbook import validate_playbook
from .storage import Storage


class PlaybookEngine:
    def __init__(self, db_path: str) -> None:
        self._storage = Storage(db_path)

    def close(self) -> None:
        self._storage.close()

    def create_incident(
        self,
        ioc_confidence: int,
        severity: str,
        status: str = "open",
        assignee: Optional[str] = None,
    ) -> int:
        return self._storage.create_incident(ioc_confidence, severity, status, assignee)

    def start_run(self, incident_id: int, playbook: dict[str, Any]) -> int:
        """启动一次运行：固定剧本快照与当时的 IOC 置信度、严重级别。"""
        validate_playbook(playbook)
        incident = self._storage.get_incident(incident_id)
        return self._storage.create_run(
            incident_id=incident.id,
            playbook=playbook,
            ioc_confidence=incident.ioc_confidence,
            severity=incident.severity,
            start_node=playbook["start"],
        )

    def advance(self, run_id: int) -> RunInfo:
        """推进一步：执行当前节点并移动到下一节点。已完成/已结束的运行为空操作。"""
        run = self._storage.get_run(run_id)
        if run.status == RUN_STATUS_COMPLETED:
            return run
        if run.current_node == END:
            self._storage.update_run(run_id, status=RUN_STATUS_COMPLETED, current_node=END)
            self._storage.commit()
            return self._storage.get_run(run_id)

        node = run.playbook["nodes"][run.current_node]
        if node["type"] == "condition":
            next_node = self._execute_condition(run, run.current_node, node)
        else:
            next_node = self._execute_action(run, run.current_node, node)

        self._storage.update_run(run_id, status=run.status, current_node=next_node)
        self._storage.commit()
        return self._storage.get_run(run_id)

    def get_run(self, run_id: int) -> RunInfo:
        return self._storage.get_run(run_id)

    def get_incident(self, incident_id: int) -> Incident:
        return self._storage.get_incident(incident_id)

    def update_incident(self, incident_id: int, **fields: Any) -> None:
        """更新事件属性（status/assignee/ioc_confidence/severity）。

        不影响已启动运行：运行使用的是启动时固定的快照。
        """
        self._storage.update_incident(incident_id, **fields)
        self._storage.commit()

    def get_timeline(self, incident_id: int) -> list[TimelineEntry]:
        return self._storage.get_timeline(incident_id)

    # ---- internals ----

    def _execute_condition(self, run: RunInfo, node_id: str, node: dict[str, Any]) -> str:
        check = node["check"]
        if check == CHECK_IOC_CONFIDENCE_GT_80:
            result = run.ioc_confidence > 80
        elif check == CHECK_SEVERITY_EQ_CRITICAL:
            result = run.severity == "critical"
        else:  # pragma: no cover - validate_playbook 已拦截
            raise ValueError(f"unknown check {check!r}")
        if not self._storage.has_node_execution(run.id, node_id):
            self._storage.record_node_execution(
                run.id, node_id, "condition", "true" if result else "false"
            )
        return node["on_true"] if result else node["on_false"]

    def _execute_action(self, run: RunInfo, node_id: str, node: dict[str, Any]) -> str:
        if not self._storage.has_node_execution(run.id, node_id):
            action = node["action"]
            incident = self._storage.get_incident(run.incident_id)
            if action == ACTION_ASSIGN_LEAD:
                self._storage.update_incident(
                    incident.id, status=incident.status, assignee="Lead"
                )
                detail = "dispatched incident to Lead"
            elif action == ACTION_SET_CONTAINMENT:
                self._storage.update_incident(
                    incident.id, status="containment", assignee=incident.assignee
                )
                detail = "moved incident to containment"
            else:  # pragma: no cover - validate_playbook 已拦截
                raise ValueError(f"unknown action {action!r}")
            self._storage.append_timeline(
                incident.id, run.id, node_id, action, detail
            )
            self._storage.record_node_execution(run.id, node_id, "action", "done")
        return node["next"]
