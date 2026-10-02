# Gov Tally

DAO 治理投票计票引擎：投票快照、委托计算与结果复核。

## 范围

本仓库从零开始实现上述方向的可用工具，不依赖外部同类实现。

- 仅依赖 Python 3.12 标准库，无第三方包。
- 核心模块：`gov_tally.py`（`tally_proposal` / `verify_tally` / 异常类）。
- 命令行：标准输入读取 JSON，标准输出 JSON。

## 状态

已实现：快照权重、逐跳委托解析、受托人权重合并、计票、独立复核与 CLI。

## 安装与运行

无需安装。要求 Python 3.12+。

```bash
cat proposal.json | python3 gov_tally.py
```

- 成功：退出码 `0`，标准输出为结果 JSON。
- 失败：退出码 `2`，标准输出为 `{"error": 异常类名, "message": 稳定描述}`，不输出 traceback。

## 输入格式

单个 JSON 对象，字段如下：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `proposal_id` | string | 提案标识 |
| `choices` | string[] | 非空、不重复的选项列表（输出顺序以此为准） |
| `votes` | object[] | 选票列表，每项含 `voter`、`choice`（均为 string） |
| `snapshot` | object | account -> 非负整数权重 |
| `delegations` | object | account -> account，逐跳指向受托人 |

委托规则：

- 委托逐跳解析到最终受托人；自委托（`A -> A`）与无委托者视为委托终止于本人，可投票。
- 委托链上的中间委托人（最终受托人不是本人）不可投票，其权重传递给最终受托人。
- 最终受托人合并本人权重与全部传递权重；每个账户的快照权重只并入一次，不重复计数。
- 最终受托人未投票时，其所持全部权重归入未计票。
- 委托链成环且无法到达最终受托人时报错（自环不算环）。

## 输出格式

`per_choice` 的键顺序与输入 `choices` 完全一致；所有权重与汇总均为整数。

| 字段 | 说明 |
| --- | --- |
| `proposal_id` | 与输入一致 |
| `per_choice` | 各选项按 choices 同序排列的计入权重 |
| `effective_weights` | 实际计入的投票受托人 -> 合并后权重（按投票顺序） |
| `uncounted_weight` | 未计票权重（弃权/未投票受托人所持权重） |
| `counted_weight` | 已计票权重 |
| `snapshot_total_weight` | 快照权重总和 |
| `winners` | 唯一最大时为该选项；并列最大时为全部并列项（按 choices 顺序）；无有效票时为空列表 |
| `is_tie` | 并列最大为 `true`；唯一最大或无有效票为 `false` |

守恒关系：`counted_weight + uncounted_weight == snapshot_total_weight`。

示例：

```json
{
  "proposal_id": "P-42",
  "per_choice": {"yes": 9, "no": 0, "abstain": 0},
  "effective_weights": {"c": 9},
  "uncounted_weight": 7,
  "counted_weight": 9,
  "snapshot_total_weight": 16,
  "winners": ["yes"],
  "is_tie": false
}
```

## Python API

```python
from gov_tally import tally_proposal, verify_tally

result = tally_proposal(input_data)
verify_tally(input_data, result)  # 一致返回 True，否则抛 TallyVerificationError
```

`verify_tally` 独立重算并逐字段复核：字段完整性、`per_choice` 选项与顺序、
权重归属（受托人合并、选票归属）、守恒关系与汇总，以及 `winners` / `is_tie`；
不会只比较 winners。

## 异常

| 异常类 | 触发条件 |
| --- | --- |
| `InvalidInputError` | 字段缺失、类型错误、权重非整数或为负、`choices` 为空或重复、输入不是合法 JSON |
| `InvalidDelegationError` | `delegations` 的委托方或受托方不在 `snapshot` 中 |
| `DelegationCycleError` | 委托链成环且无法到达最终受托人 |
| `InvalidVoteError` | voter 重复、已委托他人者投票、voter 不在 snapshot、choice 不在 choices |
| `TallyVerificationError` | `verify_tally` 复核不一致 |

错误输出中的 `message` 为确定性文本，不含内存地址。

## 测试

```bash
python3 -m unittest test_gov_tally -v
```

## 约定

- 公开行为以 README 与源码为准。
- 后续需求在此基线上增量实现。
