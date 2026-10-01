"""剧本 JSON 的解析与校验。"""

from __future__ import annotations

from typing import Any

from .models import (
    ACTION_ASSIGN_LEAD,
    ACTION_SET_CONTAINMENT,
    CHECK_IOC_CONFIDENCE_GT_80,
    CHECK_SEVERITY_EQ_CRITICAL,
    END,
)


class PlaybookError(ValueError):
    """剧本描述非法。"""


_ACTIONS = {ACTION_ASSIGN_LEAD, ACTION_SET_CONTAINMENT}
_CHECKS = {CHECK_IOC_CONFIDENCE_GT_80, CHECK_SEVERITY_EQ_CRITICAL}


def validate_playbook(playbook: dict[str, Any]) -> None:
    """校验剧本结构；非法时抛出 PlaybookError。"""
    if not isinstance(playbook, dict):
        raise PlaybookError("playbook must be a dict")
    nodes = playbook.get("nodes")
    start = playbook.get("start")
    if not isinstance(nodes, dict) or not nodes:
        raise PlaybookError("playbook.nodes must be a non-empty dict")
    if not isinstance(start, str) or start not in nodes:
        raise PlaybookError("playbook.start must reference an existing node")

    for node_id, node in nodes.items():
        if not isinstance(node, dict):
            raise PlaybookError(f"node {node_id!r} must be a dict")
        node_type = node.get("type")
        if node_type == "condition":
            if node.get("check") not in _CHECKS:
                raise PlaybookError(f"node {node_id!r}: unknown check {node.get('check')!r}")
            for key in ("on_true", "on_false"):
                _validate_target(node_id, node.get(key), nodes, key)
        elif node_type == "action":
            if node.get("action") not in _ACTIONS:
                raise PlaybookError(f"node {node_id!r}: unknown action {node.get('action')!r}")
            _validate_target(node_id, node.get("next"), nodes, "next")
        else:
            raise PlaybookError(f"node {node_id!r}: unknown type {node_type!r}")

    _assert_acyclic(nodes)


def _validate_target(node_id: str, target: Any, nodes: dict[str, Any], key: str) -> None:
    if target != END and target not in nodes:
        raise PlaybookError(
            f"node {node_id!r}: {key} target {target!r} is neither a node nor {END!r}"
        )


def _assert_acyclic(nodes: dict[str, Any]) -> None:
    def targets(node: dict[str, Any]) -> list[str]:
        if node["type"] == "condition":
            raw = [node["on_true"], node["on_false"]]
        else:
            raw = [node["next"]]
        return [t for t in raw if t != END]

    WHITE, GRAY, BLACK = 0, 1, 2
    color = {nid: WHITE for nid in nodes}

    def visit(nid: str) -> None:
        color[nid] = GRAY
        for nxt in targets(nodes[nid]):
            if color[nxt] == GRAY:
                raise PlaybookError(f"playbook contains a cycle involving node {nxt!r}")
            if color[nxt] == WHITE:
                visit(nxt)
        color[nid] = BLACK

    for nid in nodes:
        if color[nid] == WHITE:
            visit(nid)
