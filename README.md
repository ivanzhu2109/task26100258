# 安全事件响应剧本执行库（soar_playbook）

一个纯 Python + SQLite 的安全事件响应（SOAR）剧本执行库，由上层代码直接调用。
运行状态、事件与处置时间线全部持久化到 SQLite；已启动但未完成的运行，
在进程中断后重新打开同一数据库文件即可继续推进，已生效的处置不会重复。

## 能力

- **启动运行** `start_run`：每次成功启动产生独立运行 ID 并关联一个事件；
  同一事件可先后启动多个运行。启动时固定剧本快照、IOC 置信度与严重级别，
  之后修改原始数据或剧本不影响已启动运行的路径。
- **推进运行** `advance`：每调用一次执行当前节点并移动到下一节点；
  对已完成或已结束的运行是空操作，不会产生新处置。
- **查询运行** `get_run`：返回运行状态（`running` / `completed`）、
  当前节点与启动时固定的快照数据。

## 剧本格式（JSON，无循环）

```json
{
  "start": "c1",
  "nodes": {
    "c1": {
      "type": "condition",
      "check": "ioc_confidence_gt_80",
      "on_true": "dispatch",
      "on_false": "join"
    },
    "dispatch": {"type": "action", "action": "assign_lead", "next": "join"},
    "join": {"type": "action", "action": "set_containment", "next": "end"}
  }
}
```

- 条件节点 `check` 支持两种判断：
  - `ioc_confidence_gt_80`：IOC 置信度**严格大于 80**；
  - `severity_eq_critical`：严重级别等于 `"critical"`。
  分别用 `on_true` / `on_false` 指定去向。
- 动作节点 `action` 支持两种动作：
  - `assign_lead`：把事件分派给 `Lead`；
  - `set_containment`：把事件状态转为 `containment`。
  动作用 `next` 指定下一节点。
- 去向可以是后续节点 ID 或 `"end"`；多个分支可以汇合到同一节点。
- 每次运行只执行实际选中的路径；被跳过的分支不会留下任何记录。

## 使用示例

```python
import soar_playbook as sp

engine = sp.PlaybookEngine("soc.db")          # SQLite 文件；":memory:" 亦可

incident_id = engine.create_incident(ioc_confidence=85, severity="high")
run_id = engine.start_run(incident_id, playbook)  # playbook 为上面的 dict

# 逐步推进；进程中断后用新的 PlaybookEngine("soc.db") 继续即可
while True:
    info = engine.advance(run_id)
    if info.status == sp.RUN_STATUS_COMPLETED:
        break

incident = engine.get_incident(incident_id)   # status / assignee 已被动作修改
timeline = engine.get_timeline(incident_id)   # 每个动作节点一条持久处置记录
engine.close()
```

## 语义保证

- **幂等**：同一运行中同一动作节点无论重试/恢复多少次，只产生一次业务效果
  和一条时间线记录（`node_executions` 表 `(run_id, node_id)` 唯一约束 +
  单事务提交）。不同节点、不同运行之间互不相抵。
- **恢复**：每一步推进都在单个事务中落盘；中断后重启，承认此前已生效的
  处置，接着执行剩余节点，不重复、不遗漏。
- **完成判定**：只有选中路径的全部动作生效并落盘，运行才显示 `completed`；
  再次推进已完成运行不产生任何新处置。
- **记录必留**：即使事件已处于目标状态（已是 containment / 已分派 Lead），
  动作仍会追加自己的处置记录。

## 运行测试

```bash
python3 -m pytest tests/ -v
```

## 代码结构

- `soar_playbook/models.py`：数据类型与常量
- `soar_playbook/playbook.py`：剧本 JSON 校验（结构、去向、无环）
- `soar_playbook/storage.py`：SQLite 持久化层
- `soar_playbook/engine.py`：启动 / 推进 / 查询引擎
- `tests/test_engine.py`：自动化测试
