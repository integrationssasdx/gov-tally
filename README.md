# Gov Tally

DAO 治理投票计票引擎：投票快照、委托计算与结果复核。

## 范围

本仓库从零开始实现上述方向的可用工具，不依赖外部同类实现。

- 仅依赖 Python 3.12 标准库，无第三方包。
- 核心模块：`gov_tally.py`（`tally_proposal` / `verify_tally` /
  `build_review_package` / 异常类）。
- 命令行：无参数从标准输入读取计票 JSON；`review` 子命令读取复核输入。
  标准输出均为 JSON。

## 状态

已实现：快照权重、逐跳委托解析、受托人权重合并、提案级委托覆盖投票、计票、独立复核、
可选权重来源追踪（`include_provenance`）、可选门槛判定（`decision_rules`）、
独立结果复核包（`build_review_package` / `review` 子命令）与 CLI。

## 安装与运行

无需安装。要求 Python 3.12+。

```bash
# 原计票：行为与字段保持不变
cat proposal.json | python3 gov_tally.py

# 独立结果复核
cat review.json | python3 gov_tally.py review
```

- 成功：退出码 `0`，标准输出为结果 JSON（复核包见下文）。
- 失败：退出码 `2`，标准输出为 `{"error": 异常类名, "message": 稳定描述}`，不输出 traceback。
- 不接受除 `review` 之外的参数；多余参数按 `InvalidInputError` 处理。
- 两种模式都只读写标准输入/标准输出，不落盘。

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

## 结果复核（review）

`python3 gov_tally.py review` 从标准输入读取**恰好**含以下六个字段的 JSON 对象，
输出独立复核包；字段缺失或多余（含 `delegation_override`、`decision_rules`、
`choices`）、JSON 非法或根不是对象均报 `InvalidInputError`。

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `proposal_id` | string | 提案字符串 |
| `snapshot_block` | int | 非负整数区块 |
| `snapshot` | object | account -> 非负整数权重 |
| `votes` | object[] | 选票列表，每项含字符串 `voter`、`choice` |
| `delegations` | object | account -> 受托账户，逐跳解析 |
| `claimed_result` | object | 待核对结果，只允许公开结果字段 |

复核沿用公开计票语义：逐跳解析委托，把权重归到投票受托人并按选项累计，
再把快照总权重汇总为直接参与、委托参与、未参与三类；review 不设覆盖票，
`delegation_override` 与 `decision_rules` 不进入复核。

输出字段固定顺序：

| 字段 | 说明 |
| --- | --- |
| `proposal_id` / `snapshot_block` | 与输入一致 |
| `results_by_choice` | 各选项计入权重，选项集合只由选票派生，按 choice 排序 |
| `direct_participated_weight` | 已投票受托人本人权重之和 |
| `delegated_weight` | 经有效委托汇入已投票受托人的权重之和 |
| `non_participated_weight` | 受托人未投票（含未委托未投票）账户的权重之和 |
| `effective_delegations` | 实际把票权送达已投票受托人的委托明细，按 delegator 排序，每项含 `delegator`、`trustee`、`weight` |
| `weight_conservation_holds` | 三类权重之和是否等于快照权重总和 |
| `review_status` | 全部一致为 `"matched"`，否则 `"mismatched"` |
| `field_differences` | 逐项差异列表，每项含 `field`、`computed`、`claimed`；字段缺失时 `claimed` 为 `null` |

待核对结果允许的字段（逐项比对，顺序固定）：`proposal_id`、`snapshot_block`、
`results_by_choice`、`direct_participated_weight`、`delegated_weight`、
`non_participated_weight`。叶子值缺失、`null`、类型或取值错误不抛异常，逐项列为差异；
`results_by_choice` 的选项集合或任一权重不同算该字段的一项差异（比对前按选项名排序，
键序不同不算差异）。

Python 也可直接调用：

```python
from gov_tally import review_package_from_input, build_review_package

pkg = review_package_from_input(review_input_dict)  # 含顶层六字段契约校验
pkg = build_review_package(proposal_id, snapshot_block, snapshot,
                           votes, delegations, claimed_result)
```

## 异常

| 异常类 | 触发条件 |
| --- | --- |
| `InvalidInputError` | 字段缺失、类型错误（含 `delegation_override`、`include_provenance` 非布尔）、权重非整数或为负、`choices` 为空或重复、输入不是合法 JSON；`decision_rules` 非对象、字段缺失或多余、`approval_choices` 类型/成员非法或空/重复，或 `min_counted_weight`、`approval_basis_points` 类型或范围非法（布尔值不算整数）；review 输入顶层字段缺失或多余、根不是对象、命令行参数未知或多余 |
| `InvalidDelegationError` | `delegations` 的委托方或受托方不在 `snapshot` 中 |
| `DelegationCycleError` | 委托链成环且无法到达最终受托人 |
| `InvalidVoteError` | voter 重复、普通票来自已委托他人者、覆盖票来自未委托他人者、voter 不在 snapshot、choice 不在 choices |
| `TallyVerificationError` | `verify_tally` 复核不一致 |
| `SnapshotIntegrityError` | review 快照输入不合法：`proposal_id` 非字符串、`snapshot_block` 非非负整数（布尔不算整数）、`snapshot` 非对象、账户键非字符串或权重非非负整数 |
| `BallotValidationError` | review 选票不合法：`votes` 非列表、选票非对象或缺 `voter`/`choice`、二者非字符串、投票者不在快照、同一账户多票，或已委托账户直接投票 |
| `DelegationConflictError` | review 委托不合法：非对象（如可表达一账户多受托人的列表）、账户非字符串、引用快照外账户、自委托或委托链成环 |
| `ClaimedResultValidationError` | `claimed_result` 非对象、含公开结果字段之外的未知字段，或 `results_by_choice` 非字符串键的对象 |

错误输出中的 `message` 为确定性文本，不含内存地址。

## 测试

```bash
python3 -m unittest test_gov_tally -v
```

## 约定

- 公开行为以 README 与源码为准。
- 后续需求在此基线上增量实现。
