"""剧本执行引擎的自动化测试。"""

from __future__ import annotations

import copy

import pytest

import soar_playbook as sp


def make_two_identical_actions_playbook():
    """条件 -> 两个内容相同的动作节点 -> 结束。"""
    return {
        "start": "c1",
        "nodes": {
            "c1": {
                "type": "condition",
                "check": "ioc_confidence_gt_80",
                "on_true": "a1",
                "on_false": "a2",
            },
            "a1": {"type": "action", "action": "assign_lead", "next": "a2"},
            "a2": {"type": "action", "action": "assign_lead", "next": "end"},
        },
    }


def make_join_playbook():
    """条件 -> 高置信分支多一个动作 -> 汇合到共同动作 -> 结束。"""
    return {
        "start": "c1",
        "nodes": {
            "c1": {
                "type": "condition",
                "check": "ioc_confidence_gt_80",
                "on_true": "dispatch",
                "on_false": "join",
            },
            "dispatch": {"type": "action", "action": "assign_lead", "next": "join"},
            "join": {"type": "action", "action": "set_containment", "next": "end"},
        },
    }


def run_to_completion(engine: sp.PlaybookEngine, run_id: int) -> sp.RunInfo:
    for _ in range(100):
        info = engine.advance(run_id)
        if info.status == sp.RUN_STATUS_COMPLETED:
            return info
    raise AssertionError("run did not complete within 100 steps")


def timeline_actions(engine, incident_id):
    return [(e.run_id, e.node_id, e.action) for e in engine.get_timeline(incident_id)]


# ---------- 场景一：中断恢复 + 幂等 ----------


def test_resume_after_interruption_no_duplicate_no_missing(tmp_path):
    db = str(tmp_path / "soc.db")
    playbook = make_two_identical_actions_playbook()

    engine = sp.PlaybookEngine(db)
    incident_id = engine.create_incident(ioc_confidence=90, severity="high")
    run_id = engine.start_run(incident_id, playbook)

    # 推进两步：条件节点 + 第一个动作节点生效，随后“进程中断”
    engine.advance(run_id)
    engine.advance(run_id)
    assert timeline_actions(engine, incident_id) == [(run_id, "a1", "assign_lead")]
    engine.close()

    # 重启进程（新引擎实例，同一数据库文件），继续推进
    engine2 = sp.PlaybookEngine(db)
    info = run_to_completion(engine2, run_id)
    assert info.status == sp.RUN_STATUS_COMPLETED

    # 已生效的 a1 不重复，剩余的 a2 最终生效；两个相同动作各留一条记录
    assert timeline_actions(engine2, incident_id) == [
        (run_id, "a1", "assign_lead"),
        (run_id, "a2", "assign_lead"),
    ]
    assert engine2.get_incident(incident_id).assignee == "Lead"
    engine2.close()


def test_two_runs_same_incident_each_leave_own_records(tmp_path):
    db = str(tmp_path / "soc.db")
    engine = sp.PlaybookEngine(db)
    incident_id = engine.create_incident(ioc_confidence=90, severity="high")
    playbook = make_two_identical_actions_playbook()

    run1 = engine.start_run(incident_id, playbook)
    run_to_completion(engine, run1)
    run2 = engine.start_run(incident_id, playbook)
    run_to_completion(engine, run2)

    assert timeline_actions(engine, incident_id) == [
        (run1, "a1", "assign_lead"),
        (run1, "a2", "assign_lead"),
        (run2, "a1", "assign_lead"),
        (run2, "a2", "assign_lead"),
    ]
    assert engine.get_run(run1).status == sp.RUN_STATUS_COMPLETED
    assert engine.get_run(run2).status == sp.RUN_STATUS_COMPLETED
    engine.close()


def test_advance_completed_run_is_noop(tmp_path):
    db = str(tmp_path / "soc.db")
    engine = sp.PlaybookEngine(db)
    incident_id = engine.create_incident(ioc_confidence=90, severity="high")
    run_id = engine.start_run(incident_id, make_two_identical_actions_playbook())
    run_to_completion(engine, run_id)

    before = engine.get_timeline(incident_id)
    engine.advance(run_id)
    engine.advance(run_id)
    after = engine.get_timeline(incident_id)
    assert len(before) == len(after) == 2
    assert engine.get_run(run_id).status == sp.RUN_STATUS_COMPLETED
    engine.close()


# ---------- 场景二：快照固定 ----------


def test_run_uses_snapshot_after_ioc_and_playbook_mutation(tmp_path):
    db = str(tmp_path / "soc.db")
    engine = sp.PlaybookEngine(db)
    incident_id = engine.create_incident(ioc_confidence=81, severity="high")
    playbook = make_join_playbook()
    original = copy.deepcopy(playbook)
    run_id = engine.start_run(incident_id, playbook)

    # 启动后调用方修改原始数据与剧本
    engine.update_incident(incident_id, ioc_confidence=10)
    playbook["nodes"]["c1"]["on_true"] = "join"
    playbook["nodes"]["c1"]["check"] = "severity_eq_critical"
    del playbook["nodes"]["dispatch"]

    info = run_to_completion(engine, run_id)
    assert info.status == sp.RUN_STATUS_COMPLETED

    # 仍按启动时的快照走 on_true 分支：dispatch + join 都执行
    assert timeline_actions(engine, incident_id) == [
        (run_id, "dispatch", "assign_lead"),
        (run_id, "join", "set_containment"),
    ]
    # 固化在运行里的剧本未被外部修改影响
    assert engine.get_run(run_id).playbook == original
    engine.close()


@pytest.mark.parametrize(
    "confidence,expected_nodes",
    [
        (80, ["join"]),            # 严格大于 80，80 不成立
        (81, ["dispatch", "join"]),  # 81 成立
    ],
)
def test_confidence_threshold_strictly_greater_than_80(
    tmp_path, confidence, expected_nodes
):
    db = str(tmp_path / "soc.db")
    engine = sp.PlaybookEngine(db)
    incident_id = engine.create_incident(ioc_confidence=confidence, severity="low")
    run_id = engine.start_run(incident_id, make_join_playbook())

    info = run_to_completion(engine, run_id)
    assert info.status == sp.RUN_STATUS_COMPLETED
    assert [n for (_, n, _) in timeline_actions(engine, incident_id)] == expected_nodes
    engine.close()


def test_severity_critical_condition_and_snapshot(tmp_path):
    db = str(tmp_path / "soc.db")
    playbook = {
        "start": "c1",
        "nodes": {
            "c1": {
                "type": "condition",
                "check": "severity_eq_critical",
                "on_true": "contain",
                "on_false": "end",
            },
            "contain": {"type": "action", "action": "set_containment", "next": "end"},
        },
    }
    engine = sp.PlaybookEngine(db)
    incident_id = engine.create_incident(ioc_confidence=10, severity="critical")
    run_id = engine.start_run(incident_id, playbook)

    # 启动后把严重级别改掉，运行仍按 critical 走
    engine.update_incident(incident_id, severity="low")
    info = run_to_completion(engine, run_id)

    assert info.status == sp.RUN_STATUS_COMPLETED
    assert timeline_actions(engine, incident_id) == [
        (run_id, "contain", "set_containment")
    ]
    assert engine.get_incident(incident_id).status == "containment"
    engine.close()


# ---------- 其他行为 ----------


def test_action_records_even_when_already_in_target_state(tmp_path):
    db = str(tmp_path / "soc.db")
    engine = sp.PlaybookEngine(db)
    # 事件已处于 containment 且已分派给 Lead
    incident_id = engine.create_incident(
        ioc_confidence=90, severity="high", status="containment", assignee="Lead"
    )
    playbook = {
        "start": "a1",
        "nodes": {
            "a1": {"type": "action", "action": "set_containment", "next": "a2"},
            "a2": {"type": "action", "action": "assign_lead", "next": "end"},
        },
    }
    run_id = engine.start_run(incident_id, playbook)
    run_to_completion(engine, run_id)

    assert timeline_actions(engine, incident_id) == [
        (run_id, "a1", "set_containment"),
        (run_id, "a2", "assign_lead"),
    ]
    engine.close()


def test_query_distinguishes_running_and_completed(tmp_path):
    db = str(tmp_path / "soc.db")
    engine = sp.PlaybookEngine(db)
    incident_id = engine.create_incident(ioc_confidence=90, severity="high")
    run_id = engine.start_run(incident_id, make_two_identical_actions_playbook())

    assert engine.get_run(run_id).status == sp.RUN_STATUS_RUNNING
    engine.advance(run_id)
    assert engine.get_run(run_id).status == sp.RUN_STATUS_RUNNING
    run_to_completion(engine, run_id)
    assert engine.get_run(run_id).status == sp.RUN_STATUS_COMPLETED
    engine.close()


def test_invalid_playbook_rejected(tmp_path):
    db = str(tmp_path / "soc.db")
    engine = sp.PlaybookEngine(db)
    incident_id = engine.create_incident(ioc_confidence=90, severity="high")
    cyclic = {
        "start": "a",
        "nodes": {
            "a": {"type": "action", "action": "assign_lead", "next": "b"},
            "b": {"type": "action", "action": "assign_lead", "next": "a"},
        },
    }
    with pytest.raises(sp.PlaybookError):
        engine.start_run(incident_id, cyclic)
    engine.close()
