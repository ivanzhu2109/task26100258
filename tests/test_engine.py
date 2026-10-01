"""剧本执行库的自动化测试。"""
import os
import tempfile
import unittest

from playbook_lib import (
    PlaybookEngine,
    PlaybookError,
    RUN_STATUS_COMPLETED,
    RUN_STATUS_RUNNING,
)


def merge_playbook():
    """条件 -> 两个分支（各一个动作）-> 汇合动作 -> 结束。"""
    return {
        "id": "pb-merge",
        "start": "cond",
        "nodes": {
            "cond": {
                "type": "condition",
                "check": "ioc_confidence_gt_80",
                "on_true": "assign",
                "on_false": "contain",
            },
            "assign": {"type": "action", "action": "assign_lead", "next": "join"},
            "contain": {"type": "action", "action": "containment", "next": "join"},
            "join": {"type": "action", "action": "assign_lead", "next": None},
        },
    }


def two_step_playbook():
    """两个内容相同的动作节点串联。"""
    return {
        "id": "pb-two",
        "start": "a1",
        "nodes": {
            "a1": {"type": "action", "action": "assign_lead", "next": "a2"},
            "a2": {"type": "action", "action": "assign_lead", "next": None},
        },
    }


class EngineTestBase(unittest.TestCase):
    def setUp(self):
        fd, self.db_path = tempfile.mkstemp(suffix=".sqlite3")
        os.close(fd)
        os.unlink(self.db_path)
        self.engine = PlaybookEngine(self.db_path)

    def tearDown(self):
        self.engine.close()
        if os.path.exists(self.db_path):
            os.unlink(self.db_path)

    def reopen(self):
        """模拟进程中断后重启：关闭并用同一数据库文件新建引擎。"""
        self.engine.close()
        self.engine = PlaybookEngine(self.db_path)

    def run_to_completion(self, run_id):
        info = self.engine.advance_run(run_id)
        while info.status == RUN_STATUS_RUNNING:
            info = self.engine.advance_run(run_id)
        return info


class TestCrashResume(EngineTestBase):
    def test_resume_after_interruption(self):
        pb = two_step_playbook()
        run_id = self.engine.start_run("evt-1", pb, 90, "high")

        # 推进一步：a1 生效，随后“进程中断”
        info = self.engine.advance_run(run_id)
        self.assertEqual(info.status, RUN_STATUS_RUNNING)
        self.assertEqual(len(self.engine.get_timeline("evt-1")), 1)

        self.reopen()

        # 重启后继续推进：a1 不重复，a2 生效，运行完成
        info = self.run_to_completion(run_id)
        self.assertEqual(info.status, RUN_STATUS_COMPLETED)

        timeline = self.engine.get_timeline("evt-1")
        self.assertEqual([t.node_id for t in timeline], ["a1", "a2"])
        self.assertTrue(all(t.run_id == run_id for t in timeline))
        self.assertEqual(self.engine.get_event("evt-1")["assignee"], "Lead")

        # 再次推进已完成运行，不产生新处置
        info = self.engine.advance_run(run_id)
        self.assertEqual(info.status, RUN_STATUS_COMPLETED)
        self.assertEqual(len(self.engine.get_timeline("evt-1")), 2)

    def test_two_runs_same_event_each_leave_records(self):
        pb = two_step_playbook()
        run1 = self.engine.start_run("evt-2", pb, 50, "low")
        run2 = self.engine.start_run("evt-2", pb, 50, "low")
        self.assertNotEqual(run1, run2)

        self.run_to_completion(run1)
        self.run_to_completion(run2)

        timeline = self.engine.get_timeline("evt-2")
        self.assertEqual(len(timeline), 4)
        by_run = {}
        for t in timeline:
            by_run.setdefault(t.run_id, []).append(t.node_id)
        self.assertEqual(by_run[run1], ["a1", "a2"])
        self.assertEqual(by_run[run2], ["a1", "a2"])


class TestSnapshotImmutability(EngineTestBase):
    def test_playbook_and_ioc_frozen_at_start(self):
        pb = merge_playbook()
        run_id = self.engine.start_run("evt-3", pb, 81, "high")

        # 启动后修改原始剧本与 IOC 数据，不得影响已启动运行
        pb["nodes"]["cond"]["on_true"] = "contain"
        pb["nodes"]["cond"]["on_false"] = "assign"
        mutated_ioc_confidence = 10  # noqa: F841 调用方本地变量变化无关运行

        self.run_to_completion(run_id)
        timeline = self.engine.get_timeline("evt-3")
        # 仍走原剧本的 true 分支（assign），跳过的 contain 无记录，汇合 join 执行
        self.assertEqual([t.node_id for t in timeline], ["assign", "join"])

    def test_confidence_80_not_greater(self):
        # 严格大于 80：80 走 false 分支
        run_id = self.engine.start_run("evt-4", merge_playbook(), 80, "high")
        self.run_to_completion(run_id)
        timeline = self.engine.get_timeline("evt-4")
        self.assertEqual([t.node_id for t in timeline], ["contain", "join"])
        self.assertEqual(self.engine.get_event("evt-4")["status"], "containment")

    def test_confidence_81_greater(self):
        run_id = self.engine.start_run("evt-5", merge_playbook(), 81, "high")
        self.run_to_completion(run_id)
        timeline = self.engine.get_timeline("evt-5")
        self.assertEqual([t.node_id for t in timeline], ["assign", "join"])

    def test_snapshot_survives_restart(self):
        pb = merge_playbook()
        run_id = self.engine.start_run("evt-6", pb, 81, "high")
        pb["nodes"]["cond"]["check"] = "severity_eq_critical"
        self.reopen()
        self.run_to_completion(run_id)
        timeline = self.engine.get_timeline("evt-6")
        self.assertEqual([t.node_id for t in timeline], ["assign", "join"])


class TestSeverityCondition(EngineTestBase):
    def playbook(self):
        return {
            "id": "pb-sev",
            "start": "cond",
            "nodes": {
                "cond": {
                    "type": "condition",
                    "check": "severity_eq_critical",
                    "on_true": "contain",
                    "on_false": "end",
                },
                "contain": {
                    "type": "action",
                    "action": "containment",
                    "next": None,
                },
            },
        }

    def test_critical_takes_true_branch(self):
        run_id = self.engine.start_run("evt-7", self.playbook(), 10, "critical")
        self.run_to_completion(run_id)
        timeline = self.engine.get_timeline("evt-7")
        self.assertEqual([t.node_id for t in timeline], ["contain"])
        self.assertEqual(self.engine.get_event("evt-7")["status"], "containment")

    def test_non_critical_skips_to_end(self):
        run_id = self.engine.start_run("evt-8", self.playbook(), 10, "low")
        info = self.run_to_completion(run_id)
        self.assertEqual(info.status, RUN_STATUS_COMPLETED)
        self.assertEqual(self.engine.get_timeline("evt-8"), [])


class TestActionEffects(EngineTestBase):
    def test_action_records_even_when_state_already_matches(self):
        # 事件已处于 containment，再次执行仍留下自己的记录
        pb = {
            "id": "pb-dup",
            "start": "c1",
            "nodes": {
                "c1": {"type": "action", "action": "containment", "next": "c2"},
                "c2": {"type": "action", "action": "containment", "next": None},
            },
        }
        run_id = self.engine.start_run("evt-9", pb, 0, "low")
        self.run_to_completion(run_id)
        timeline = self.engine.get_timeline("evt-9")
        self.assertEqual([t.node_id for t in timeline], ["c1", "c2"])
        self.assertEqual(self.engine.get_event("evt-9")["status"], "containment")


class TestValidation(EngineTestBase):
    def test_cycle_rejected(self):
        pb = {
            "id": "pb-cycle",
            "start": "a",
            "nodes": {
                "a": {"type": "action", "action": "assign_lead", "next": "a"},
            },
        }
        with self.assertRaises(PlaybookError):
            self.engine.start_run("evt-10", pb, 0, "low")

    def test_unknown_run_raises(self):
        with self.assertRaises(KeyError):
            self.engine.get_run("no-such-run")


if __name__ == "__main__":
    unittest.main()
