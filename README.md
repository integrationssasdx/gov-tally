# Gov Tally

DAO 治理投票计票引擎：投票快照、委托计算与结果复核。

## 范围

本仓库从零开始实现上述方向的可用工具，不依赖外部同类实现。仅使用 Python 3.12 标准库。

## 用法

### 命令行

标准输入读取 JSON，标准输出写出 JSON：

```sh
python3 gov_tally.py < input.json
```

成功时退出码 0，输出计票结果；异常时退出码 2，输出
`{"error": "<异常类名>", "message": "<稳定描述>"}`。

### 输入

```json
{
  "proposal_id": "p1",
  "choices": ["yes", "no"],
  "votes": [{"voter": "alice", "choice": "yes"}],
  "snapshot": {"alice": 10, "bob": 5},
  "delegations": {"bob": "alice"}
}
```

- `choices`：非空且不重复的字符串列表。
- `snapshot`：账户 -> 非负整数权重。
- `delegations`：账户 -> 账户，逐跳解析至最终受托人；自委托或不委托者可投票，中间委托人不可投票；最终受托人合并本人与传递权重，snapshot 权重不重复计数。
- `votes`：每票含 `voter` 与 `choice`，`choice` 必须在 `choices` 中。

### 输出

```json
{
  "proposal_id": "p1",
  "per_choice": [15, 0],
  "effective_weights": {"alice": 15},
  "uncounted_weight": 0,
  "counted_weight": 15,
  "snapshot_total_weight": 15,
  "winners": ["yes"],
  "is_tie": false
}
```

- `per_choice`：与 `choices` 同序的各选项计入权重（整数）。
- `effective_weights`：最终受托人 -> 合并后的计入权重。
- `winners`：唯一最大为该选项，并列最大为全部并列项（按 `choices` 顺序），无有效票为 `[]`；`is_tie` 对应为 `false` / `true` / `false`。

### API

- `tally_proposal(input_data) -> dict`：计算上述结果。
- `verify_tally(input_data, result) -> bool`：复核字段、归属、守恒与汇总（不只比较 winners），一致返回 `True`，否则抛 `TallyVerificationError`。

### 异常

- `InvalidInputError`：字段缺失、类型错误、非整数、负权重、choices 空或重复。
- `InvalidDelegationError`：delegations 含 snapshot 外账户。
- `DelegationCycleError`：委托链成环且不能达最终受托人。
- `InvalidVoteError`：重复 voter、委托他人者投票、voter 不在 snapshot、choice 不在 choices。
- `TallyVerificationError`：复核不一致。

## 测试

```sh
python3 -m unittest test_gov_tally -v
```

## 约定

- 公开行为以 README 与源码为准。
- 后续需求在此基线上增量实现。
