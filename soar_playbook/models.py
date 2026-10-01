"""数据类型定义。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


RUN_STATUS_RUNNING = "running"
RUN_STATUS_COMPLETED = "completed"

ACTION_ASSIGN_LEAD = "assign_lead"
ACTION_SET_CONTAINMENT = "set_containment"

CHECK_IOC_CONFIDENCE_GT_80 = "ioc_confidence_gt_80"
CHECK_SEVERITY_EQ_CRITICAL = "severity_eq_critical"

END = "end"


@dataclass
class Incident:
    id: int
    status: str
    assignee: Optional[str]
    ioc_confidence: int
    severity: str


@dataclass
class TimelineEntry:
    id: int
    incident_id: int
    run_id: int
    node_id: str
    action: str
    detail: str
    created_at: str


@dataclass
class RunInfo:
    id: int
    incident_id: int
    status: str
    current_node: Optional[str]
    ioc_confidence: int
    severity: str
    playbook: dict[str, Any] = field(default_factory=dict)
