# Gov Tally

DAO 治理投票计票引擎：投票快照、委托计算与结果复核。

## 范围

本仓库从零开始实现上述方向的可用工具，不依赖外部同类实现。

- 仅依赖 Python 3.12 标准库，无第三方包。
- 核心模块：`gov_tally.py`（`tally_proposal` / `verify_tally` / 异常类）。
- 命令行：标准输入读取 JSON，标准输出 JSON。

## 状态

已实现：快照权重、逐跳委托解析、受托人权重合并、提案级委托覆盖投票、计票、独立复核、
可选权重来源追踪（`include_provenance`）、可选门槛判定（`decision_rules`）与 CLI；
结果复核（review）同样独立核对覆盖票归属、完整选项（`choices`）与门槛判定。

## 安装与运行

无需安装。要求 Python 3.12+。

```bash
cat proposal.json | python3 gov_tally.py
cat review_input.json | python3 gov_tally.py review
```

- 成功：退出码 `0`，标准输出为结果 JSON。
- 失败：退出码 `2`，标准输出为 `{"error": 异常类名, "message": 稳定描述}`，不输出 traceback。
- 无参数：按提案计票输入处理；`review` 子命令：按复核包输入处理（见下文「结果复核」），
  只新增输入输出路径，计票语义、异常与退出码不变。

## 输入格式

单个 JSON 对象，字段如下：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `proposal_id` | string | 提案标识 |
| `choices` | string[] | 非空、不重复的选项列表（输出顺序以此为准） |
| `votes` | object[] | 选票列表，每项含 `voter`、`choice`（均为 string）与可选布尔 `delegation_override` |
| `snapshot` | object | account -> 非负整数权重 |
| `delegations` | object | account -> account，逐跳指向受托人 |
| `include_provenance` | bool（可选） | 为 `true` 时结果增加 `weight_provenance`；省略或 `false` 时输出形状不变 |
| `decision_rules` | object（可选） | 门槛判定配置，详见下文；省略时输出形状不变 |

委托规则：

- 委托逐跳解析到最终受托人；自委托（`A -> A`）与无委托者视为委托终止于本人，可投票。
- 委托链上的中间委托人（最终受托人不是本人）不可投普通票，其权重传递给最终受托人。
- 最终受托人合并本人权重与全部传递权重；每个账户的快照权重只并入一次，不重复计数。
- 最终受托人未投票时，其所持全部权重归入未计票。
- 委托链成环且无法到达最终受托人时报错（自环不算环）。

委托覆盖（`delegation_override`）：

- 省略或为 `false`：保持普通票语义，普通票只由最终受托人发出。
- 为 `true`：已委托账户可亲自投票，其**本人权重**按所选 `choice` 计入，且不再传给最终受托人；
  其上游经其转发的他人权重仍逐跳归入最终受托人，受托人投票时只记扣除后的剩余合并权重。
- 覆盖票仅可由委托链指向他人（最终受托人不是本人）的账户投出，且同一 voter 只能出现一次。
- 无论受托人票与覆盖票在 `votes` 中的先后顺序如何，扣除都生效，本人权重不会重复计入。
- `delegation_override` 取值非布尔（如字符串 `"true"`、数字 `1`）报 `InvalidInputError`；
  覆盖投票者不在 snapshot 或未委托他人、普通投票者已委托他人、voter 重复、choice 越界，报 `InvalidVoteError`。

权重来源追踪（`include_provenance`）：

- 省略或为 `false`：Python API 与命令行输出形状不变，结果不含 `weight_provenance`。
- 为 `true`：结果增加 `weight_provenance`，外层按 `votes` 中计票 voter 顺序（与
  `effective_weights` 键序一致），每个 voter 映射到其正权重来源；内层按 snapshot 顺序，
  只列正权重来源，来源权重即该账户的 snapshot 权重，且来源权重和等于该票的
  `effective_weights`。
- 普通票的来源含受托人本人与全部汇入上游账户，但被 `delegation_override` 抽走本人权重者
  不进入该票；覆盖票只含投票者本人及其 snapshot 权重，多个覆盖者各归自己的票；
  受托人普通票只含剩余来源。同一 snapshot 来源不会同时归入受托人票与覆盖票。
- 零权重计票仍在外层出现，其来源映射为空；零权重账户不作为来源列出。
- `include_provenance` 取值非布尔（如字符串 `"true"`、数字 `1`、`null`）报 `InvalidInputError`。

门槛判定（`decision_rules`）：

- 省略时 Python API 与命令行输出形状、异常、退出码与票权语义均不变。
- 配置存在时必须**恰好**含以下三个字段，缺一或有多余字段均报 `InvalidInputError`：
  - `approval_choices`：`choices` 的非空、无重复子集（成员必须为 string）；
  - `min_counted_weight`：非负整数（布尔值不算整数）；
  - `approval_basis_points`：`1` 到 `10000` 的整数（含端点；布尔值不算整数）。
- 配置非对象、字段类型或取值非法（含子项含不在 `choices` 中的成员、重复成员）均报 `InvalidInputError`。
- 先按原规则完成计票，再计算（整数比较，不使用浮点）：
  - `quorum_met`：`counted_weight > 0` 且 `counted_weight >= min_counted_weight`；
  - `approval_met`：`approval_choices` 中各选项的 `per_choice` 权重之和乘 `10000`
    不少于 `counted_weight * approval_basis_points`（边界相等算通过）；
  - `decision`：`quorum_met` 为假 -> `"no_quorum"`；两个判定都真 -> `"approved"`；
    其余情况 -> `"rejected"`。
- 零计票权重时即便 `min_counted_weight` 为 0 也不构成法定人数（`counted_weight > 0` 为必要条件）。

## 输出格式

`per_choice` 的键顺序与输入 `choices` 完全一致；所有权重与汇总均为整数。

| 字段 | 说明 |
| --- | --- |
| `proposal_id` | 与输入一致 |
| `per_choice` | 各选项按 choices 同序排列的计入权重 |
| `effective_weights` | 实际计票 voter -> 权重，按 `votes` 顺序；覆盖票记本人权重，受托人票记扣除覆盖权重后的剩余合并权重 |
| `weight_provenance` | 仅 `include_provenance` 为 `true` 时出现：计票 voter -> 来源账户 -> snapshot 权重；外层按 `votes` 顺序，内层按 snapshot 顺序且只列正权重来源 |
| `uncounted_weight` | 未计票权重（弃权/未投票受托人所持权重） |
| `counted_weight` | 已计票权重 |
| `snapshot_total_weight` | 快照权重总和 |
| `winners` | 唯一最大时为该选项；并列最大时为全部并列项（按 choices 顺序）；无有效票时为空列表 |
| `is_tie` | 并列最大为 `true`；唯一最大或无有效票为 `false` |
| `quorum_met` | 仅配置 `decision_rules` 时出现：是否达到法定人数（布尔值） |
| `approval_met` | 仅配置 `decision_rules` 时出现：赞成率是否达标（布尔值） |
| `decision` | 仅配置 `decision_rules` 时出现：`"no_quorum"` / `"approved"` / `"rejected"` |

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

配置 `decision_rules` 时在上述字段之后追加三个字段，例如：

```json
{
  "decision_rules": {
    "approval_choices": ["yes", "abstain"],
    "min_counted_weight": 8,
    "approval_basis_points": 5001
  }
}
```

对应结果尾部增加：

```json
{
  "quorum_met": true,
  "approval_met": true,
  "decision": "approved"
}
```

## 结果复核（review）

`review` 子命令对一次提案的计票结果做独立复核，不落盘：

```bash
cat review_input.json | python3 gov_tally.py review
```

输入为含以下六个必填字段的 JSON 对象（另可含可选字段 `choices` 与
`decision_rules`；缺必填字段、多未知字段、非法 JSON 或输入非对象均报
`InvalidInputError`）：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `proposal_id` | string | 提案标识 |
| `snapshot_block` | int | 非负整数快照区块（布尔值不算整数） |
| `snapshot` | object | account -> 非负整数权重 |
| `votes` | object[] | 选票列表，每项含 `voter`、`choice`（均为 string）与可选布尔 `delegation_override` |
| `delegations` | object | account -> 受托 account |
| `claimed_result` | object | 待核对结果 |
| `choices` | string[]（可选） | 非空、不重复的完整选项列表；省略时选项从投票派生 |
| `decision_rules` | object（可选） | 门槛判定配置，与计票入口同构；存在时必须同时提供 `choices` |

校验后逐跳解析委托，把权重归到投票受托人并累计选项。选票可带
`delegation_override`：缺失或 `false` 沿用普通票语义（只由未委托他人者
投出）；`true` 仅允许已委托他人者投出——其本人权重直接计入所选选项且
不再交给受托人，经其转发的他人权重仍逐跳归最终受托人，扣除与选票顺序
无关，每账户至多一票。`delegation_override` 非布尔、覆盖票来自未委托
他人者报 `BallotValidationError`。

`choices` 省略时选项集合从投票记录派生；给出时 `results_by_choice` 含
全部选项（未被投出者为 0），一律按选项名排序。投票选项不在 `choices`
内报 `BallotValidationError`；`choices` 为空、重复或成员非字符串报
`InvalidInputError`。

`decision_rules` 的校验与计算沿用 `tally_proposal`（字段、类型、范围、
判定均一致；非法配置报 `InvalidInputError`）。有配置时在既有输出字段
之后按 `quorum_met`、`approval_met`、`decision` 顺序追加，并与
`claimed_result` 逐项比较；无配置时 `claimed_result` 出现任一门槛字段
报 `ClaimedResultValidationError`。

输出字段固定顺序：
`proposal_id` / `snapshot_block` / `results_by_choice`（按 choice 排序）/
`direct_participated_weight`（投票者本人权重：投普通票的受托人本人 +
覆盖票投票者本人）/ `delegated_weight`（经有效委托送达已投票受托人的
他人权重）/ `non_participated_weight`（其余权重）/
`effective_delegations`（按 delegator 排序；不列覆盖者本人权重，实际
转发的上游权重按来源账户列入）/ `weight_conservation_holds`
（三类权重之和是否等于快照权重总和）/ `review_status` / `field_differences`，
有 `decision_rules` 时尾部追加三个门槛字段。三类权重守恒。

`claimed_result` 缺字段或值不同进入 `field_differences`，每项含 `field`、
`computed`、`claimed`；无差异时 `review_status` 为 `"matched"`，有差异为
`"mismatched"`。
字段取值校验的异常分类：`SnapshotIntegrityError`（提案标识、快照区块、快照账户或权重
不合法）、`BallotValidationError`（投票者不在快照、同一账户多票、选项非字符串或越界、
普通票来自已委托他人者、覆盖票来自未委托他人者、`delegation_override` 非布尔）、
`DelegationConflictError`（委托引用快照外账户、自委托、成环或一账户多受托人）、
`InvalidInputError`（顶层结构、`choices`、`decision_rules` 非法，或配置缺少 `choices`）、
`ClaimedResultValidationError`
（待核对结果非对象、含未知字段——无配置时含门槛字段——或 `results_by_choice` 键非字符串）。
Python API 对应
`build_review_package(proposal_id, snapshot_block, snapshot, votes, delegations,
claimed_result, choices=None, decision_rules=None)`。

## Python API

```python
from gov_tally import tally_proposal, verify_tally

result = tally_proposal(input_data)
verify_tally(input_data, result)  # 一致返回 True，否则抛 TallyVerificationError

# 可选权重来源追踪：参数与输入字段二选一（参数优先）
result = tally_proposal(input_data, include_provenance=True)
verify_tally(input_data, result, include_provenance=True)
```

`verify_tally` 按覆盖语义独立重算并逐字段复核：字段完整性、`per_choice` 选项与顺序、
权重归属（覆盖票本人权重、受托人剩余合并权重、选票归属）、守恒关系与汇总，以及
`winners` / `is_tie`；不会只比较 winners。
输入含 `decision_rules` 时另复核 `quorum_met` / `approval_met` / `decision`：
三字段必须恰好按该顺序追加在既有字段之后（集合、位置、顺序不符均失败），
两个判定必须为布尔值且与重算结果一致，`decision` 必须取值合法且与两个判定逻辑一致；
无配置时结果中出现任一门槛字段也会失败。
`include_provenance` 为 `true` 时另复核 `weight_provenance`：字段存在性、内外层键及顺序、
来源归属、来源值等于 snapshot 权重、映射求和等于 `effective_weights`，以及同一 snapshot
来源至多归属一张计票；缺失、额外、乱序、错配、重复归属、非整数或非正来源权重均抛
`TallyVerificationError`。为 `false` 时结果中的额外字段不受约束。

## 异常

| 异常类 | 触发条件 |
| --- | --- |
| `InvalidInputError` | 字段缺失、类型错误（含 `delegation_override`、`include_provenance` 非布尔）、权重非整数或为负、`choices` 为空或重复、输入不是合法 JSON；`decision_rules` 非对象、字段缺失或多余、`approval_choices` 类型/成员非法或空/重复，或 `min_counted_weight`、`approval_basis_points` 类型或范围非法（布尔值不算整数） |
| `InvalidDelegationError` | `delegations` 的委托方或受托方不在 `snapshot` 中 |
| `DelegationCycleError` | 委托链成环且无法到达最终受托人 |
| `InvalidVoteError` | voter 重复、普通票来自已委托他人者、覆盖票来自未委托他人者、voter 不在 snapshot、choice 不在 choices |
| `TallyVerificationError` | `verify_tally` 复核不一致 |

错误输出中的 `message` 为确定性文本，不含内存地址。

## 测试

```bash
python3 -m unittest test_gov_tally -v
```

## 约定

- 公开行为以 README 与源码为准。
- 后续需求在此基线上增量实现。
