"""类型定义与剧本 JSON 解析/校验。"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Optional

# 条件判断类型
CHECK_IOC_CONFIDENCE_GT_80 = "ioc_confidence_gt_80"
CHECK_SEVERITY_EQ_CRITICAL = "severity_eq_critical"
SUPPORTED_CHECKS = {CHECK_IOC_CONFIDENCE_GT_80, CHECK_SEVERITY_EQ_CRITICAL}

# 动作类型
ACTION_ASSIGN_LEAD = "assign_lead"
ACTION_CONTAINMENT = "containment"
SUPPORTED_ACTIONS = {ACTION_ASSIGN_LEAD, ACTION_CONTAINMENT}

# 运行状态
RUN_STATUS_RUNNING = "running"
RUN_STATUS_COMPLETED = "completed"

# 表示结束的去向取值
END_MARKERS = {None, "", "end"}


class PlaybookError(ValueError):
    """剧本描述非法。"""


@dataclass(frozen=True)
class Node:
    node_id: str
    kind: str  # "condition" | "action"
    check: Optional[str] = None
    on_true: Optional[str] = None
    on_false: Optional[str] = None
    action: Optional[str] = None
    next: Optional[str] = None

    def successors(self) -> list[str]:
        if self.kind == "condition":
            return [t for t in (self.on_true, self.on_false) if t not in END_MARKERS]
        return [self.next] if self.next not in END_MARKERS else []


@dataclass(frozen=True)
class Playbook:
    playbook_id: str
    start: str
    nodes: dict[str, Node]


@dataclass(frozen=True)
class RunInfo:
    run_id: str
    event_id: str
    status: str
    playbook_id: str
    ioc_confidence: float
    severity: str


@dataclass(frozen=True)
class TimelineRecord:
    record_id: int
    event_id: str
    run_id: str
    node_id: str
    action: str
    detail: str
    created_at: str


def _parse_node(node_id: str, raw: Any) -> Node:
    if not isinstance(raw, dict):
        raise PlaybookError(f"节点 {node_id!r} 必须是对象")
    kind = raw.get("type")
    if kind == "condition":
        check = raw.get("check")
        if check not in SUPPORTED_CHECKS:
            raise PlaybookError(f"节点 {node_id!r} 的 check 非法: {check!r}")
        return Node(
            node_id=node_id,
            kind=kind,
            check=check,
            on_true=raw.get("on_true"),
            on_false=raw.get("on_false"),
        )
    if kind == "action":
        action = raw.get("action")
        if action not in SUPPORTED_ACTIONS:
            raise PlaybookError(f"节点 {node_id!r} 的 action 非法: {action!r}")
        return Node(node_id=node_id, kind=kind, action=action, next=raw.get("next"))
    raise PlaybookError(f"节点 {node_id!r} 的 type 非法: {kind!r}")


def _check_targets_and_cycles(playbook: Playbook) -> None:
    for node in playbook.nodes.values():
        for target in node.successors():
            if target not in playbook.nodes:
                raise PlaybookError(
                    f"节点 {node.node_id!r} 指向不存在的节点 {target!r}"
                )
    # 从起点可达范围内做 DFS 三色标记检测环
    color: dict[str, int] = {}  # 0=未访问 1=访问中 2=完成

    def visit(node_id: str, stack: tuple[str, ...]) -> None:
        state = color.get(node_id, 0)
        if state == 1:
            raise PlaybookError(f"剧本存在循环: {' -> '.join(stack + (node_id,))}")
        if state == 2:
            return
        color[node_id] = 1
        for nxt in playbook.nodes[node_id].successors():
            visit(nxt, stack + (node_id,))
        color[node_id] = 2

    visit(playbook.start, ())


def parse_playbook(raw: Any) -> Playbook:
    """解析并校验剧本 JSON（dict 或 JSON 字符串）。不含循环。"""
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise PlaybookError(f"剧本不是合法 JSON: {exc}") from exc
    if not isinstance(raw, dict):
        raise PlaybookError("剧本必须是 JSON 对象")
    playbook_id = raw.get("id")
    if not isinstance(playbook_id, str) or not playbook_id:
        raise PlaybookError("剧本缺少 id")
    start = raw.get("start")
    raw_nodes = raw.get("nodes")
    if not isinstance(raw_nodes, dict) or not raw_nodes:
        raise PlaybookError("剧本缺少 nodes")
    nodes = {nid: _parse_node(nid, nraw) for nid, nraw in raw_nodes.items()}
    if start not in nodes:
        raise PlaybookError(f"start 指向不存在的节点 {start!r}")
    playbook = Playbook(playbook_id=playbook_id, start=start, nodes=nodes)
    _check_targets_and_cycles(playbook)
    return playbook
