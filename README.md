# 安全事件响应剧本执行库

纯 Python + SQLite 实现的剧本（Playbook）执行库，由上层代码直接调用。
运行状态、事件状态与处置时间线全部持久化到 SQLite，进程中断后可直接继续推进。

## 能力

- **启动运行** `start_run(...)`：产生独立 `run_id`，关联一个事件；同一事件可先后启动多个运行。
  启动时固定剧本快照、IOC 置信度与严重级别，之后修改原始数据不影响已启动的运行（重启后同样成立）。
- **推进运行** `advance_run(run_id)`：每次推进一个节点；已完成运行再次推进不产生任何新处置。
- **查询运行** `get_run(run_id)`：返回 `RunInfo`，`status` 为 `running` 或 `completed`。

## 剧本 JSON 格式

```json
{
  "id": "pb-merge",
  "start": "cond",
  "nodes": {
    "cond": {
      "type": "condition",
      "check": "ioc_confidence_gt_80",
      "on_true": "assign",
      "on_false": "contain"
    },
    "assign":  {"type": "action", "action": "assign_lead", "next": "join"},
    "contain": {"type": "action", "action": "containment", "next": "join"},
    "join":    {"type": "action", "action": "assign_lead", "next": null}
  }
}
```

- 条件节点 `check` 支持两种判断：
  - `ioc_confidence_gt_80`：IOC 置信度**严格大于 80**；
  - `severity_eq_critical`：严重级别等于 `"critical"`。
  `on_true` / `on_false` 分别指定成立与不成立时的去向。
- 动作节点 `action` 支持两种动作：
  - `assign_lead`：将事件分派给 `"Lead"`（`events.assignee = 'Lead'`）；
  - `containment`：将事件转为遏制（`events.status = 'containment'`）。
  `next` 指定下一节点。
- 去向为 `null`、`""` 或 `"end"` 表示结束；多个分支可以汇合到同一节点。
- 剧本不允许循环，启动时校验，含环则抛出 `PlaybookError`。
- 每次运行只执行实际选中的路径；被跳过的分支不留任何记录。

## 幂等与恢复语义

- 同一运行中同一动作节点无论重试多少次，只产生一次业务效果和一条处置记录
  （`timeline` 表以 `(run_id, node_id)` 唯一约束保证）。
- 不同节点即使动作内容完全相同，也各留一条记录；不同运行之间互不影响。
- 每个节点的“标记已执行 + 修改事件 + 追加处置记录”在单个 SQLite 事务内完成：
  进程中断要么整体生效、要么整体回滚，重启后从下一个未执行节点继续。
- 即使事件已处于目标状态，动作仍会追加自己的处置记录。

## 使用示例

```python
from playbook_lib import PlaybookEngine, RUN_STATUS_RUNNING

engine = PlaybookEngine("soc.sqlite3")   # 持久化到本地 SQLite 文件

run_id = engine.start_run(
    event_id="evt-001",
    playbook=playbook_dict,              # dict 或 JSON 字符串
    ioc_confidence=81,
    severity="critical",
)

info = engine.advance_run(run_id)        # 每次推进一个节点
while info.status == RUN_STATUS_RUNNING:
    info = engine.advance_run(run_id)

print(engine.get_run(run_id))            # RunInfo(status='completed', ...)
print(engine.get_event("evt-001"))       # {'assignee': 'Lead', 'status': 'containment', ...}
for rec in engine.get_timeline("evt-001"):
    print(rec.node_id, rec.action, rec.created_at)

engine.close()                           # 进程重启后用同一 db 路径重新构造即可继续
```

## 运行测试

```bash
python3 -m unittest discover -s tests -v
```

## 代码结构

- `playbook_lib/models.py`：类型定义、剧本 JSON 解析与校验（含循环检测）。
- `playbook_lib/store.py`：SQLite 持久化（runs / events / timeline / node_executions 四张表）。
- `playbook_lib/engine.py`：`PlaybookEngine`，启动 / 推进 / 查询三项能力。
- `tests/test_engine.py`：覆盖中断恢复、快照固定、条件边界（80/81）、分支汇合、
  幂等、同事件多运行等场景。
