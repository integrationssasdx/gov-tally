"""Gov Tally — DAO 治理投票计票引擎。

仅使用 Python 3.12 标准库，实现：

* 快照权重（snapshot）与逐跳委托（delegations）解析；
* ``tally_proposal``：合并最终受托人权重并计票，支持提案级委托覆盖
  （``delegation_override``：已委托者亲自投出本人权重，且不重复计入受托人）；
* 可选权重来源追踪（``include_provenance``）：结果增加 ``weight_provenance``，
  逐票列出正权重来源账户及其 snapshot 权重；
* 可选门槛判定（``decision_rules``）：结果在既有字段后增加
  ``quorum_met`` / ``approval_met`` / ``decision``；
* ``verify_tally``：独立复核字段、归属、守恒、汇总与门槛判定；
* ``build_review_package``：独立的结果复核包装配，把一次提案的计票输入、
  有效委托、实际票权与待核对结果整理成确定性、可重复验证的返回对象
  （``matched`` / ``mismatched`` 逐项差异，不隐式写入文件、数据库或日志）；
  可选 ``choices``（给出完整选项，含未投出的零票选项）、选票上的
  ``delegation_override``（已委托者亲自投出本人权重）与 ``decision_rules``
  （追加门槛判定字段并独立复核）；
* 委托事件流：``tally_proposal_from_events`` / ``verify_tally_from_events`` /
  ``build_review_package_from_events`` 以非负整数 ``snapshot_block`` 和
  ``delegation_events``（恰好含 ``block`` / ``delegator`` / ``trustee``，
  ``trustee`` 为 null 表示清除委托）取代静态 ``delegations``，按序重放
  不大于 ``snapshot_block`` 的事件重建委托状态后再计票、复核与装配；
  事件计票结果末尾追加 ``snapshot_block`` 与 ``resolved_delegations``
  （按 snapshot 顺序列各账户最终受托人），复核包末尾追加
  ``resolved_delegations``；事件非法抛 ``DelegationEventError``；
* 命令行：标准输入读取 JSON，标准输出 JSON，异常退出码 2；
  无参数按提案计票输入处理，``review`` 子命令按复核包输入处理，
  ``events`` / ``review-events`` 子命令按事件流输入处理。
"""

from __future__ import annotations

import json
import sys
from typing import Any

__all__ = [
    "TallyError",
    "InvalidInputError",
    "InvalidDelegationError",
    "DelegationCycleError",
    "InvalidVoteError",
    "TallyVerificationError",
    "SnapshotIntegrityError",
    "BallotValidationError",
    "DelegationConflictError",
    "ClaimedResultValidationError",
    "DelegationEventError",
    "tally_proposal",
    "verify_tally",
    "build_review_package",
    "tally_proposal_from_events",
    "verify_tally_from_events",
    "build_review_package_from_events",
]

_RESULT_FIELDS = (
    "proposal_id",
    "per_choice",
    "effective_weights",
    "uncounted_weight",
    "counted_weight",
    "snapshot_total_weight",
    "winners",
    "is_tie",
)

_DECISION_FIELDS = ("quorum_met", "approval_met", "decision")
_DECISION_RULE_FIELDS = (
    "approval_choices",
    "min_counted_weight",
    "approval_basis_points",
)
_DECISIONS = ("no_quorum", "approved", "rejected")

# 复核包输出字段的稳定顺序；门槛字段仅在配置 decision_rules 时追加。
_REVIEW_PACKAGE_FIELDS = (
    "proposal_id",
    "snapshot_block",
    "results_by_choice",
    "direct_participated_weight",
    "delegated_weight",
    "non_participated_weight",
    "effective_delegations",
    "weight_conservation_holds",
    "review_status",
    "field_differences",
)
_REVIEW_PACKAGE_DECISION_FIELDS = (
    "quorum_met",
    "approval_met",
    "decision",
)

# 待核对结果允许的公开结果字段及其在逐项差异中的确定顺序；
# 有 decision_rules 时三个门槛字段追加其后。
_CLAIMED_RESULT_FIELDS = (
    "proposal_id",
    "snapshot_block",
    "results_by_choice",
    "direct_participated_weight",
    "delegated_weight",
    "non_participated_weight",
)
_CLAIMED_DECISION_FIELDS = ("quorum_met", "approval_met", "decision")

# review 子命令输入对象必须包含的字段；choices 与 decision_rules 可选，
# 出现与否均合法（decision_rules 必须与 choices 同时提供）。
_REVIEW_INPUT_FIELDS = (
    "proposal_id",
    "snapshot_block",
    "snapshot",
    "votes",
    "delegations",
    "claimed_result",
)
_REVIEW_OPTIONAL_INPUT_FIELDS = ("choices", "decision_rules")

# review-events 子命令输入对象必须包含的字段；以 delegation_events 取代
# delegations，可选字段与 review 相同。
_REVIEW_EVENTS_INPUT_FIELDS = (
    "proposal_id",
    "snapshot_block",
    "snapshot",
    "votes",
    "delegation_events",
    "claimed_result",
)


class TallyError(Exception):
    """所有计票异常的基类。"""


class InvalidInputError(TallyError):
    """输入缺少字段、类型错误、取值非法（如负权重、choices 空或重复）。"""


class InvalidDelegationError(TallyError):
    """delegations 引用了 snapshot 之外的账户。"""


class DelegationCycleError(TallyError):
    """委托链成环且无法到达最终受托人。"""


class DelegationEventError(TallyError):
    """委托事件流不合法：事件字段缺失或多余、block 类型或顺序非法、
    账户不在 snapshot、trustee 等于 delegator，或重放后委托链成环。"""


class InvalidVoteError(TallyError):
    """选票非法：重复 voter、普通票来自已委托他人者、覆盖票来自未委托他人者、
    voter 不在 snapshot、choice 不存在。"""


class TallyVerificationError(TallyError):
    """计票结果未通过独立复核。"""


class SnapshotIntegrityError(TallyError):
    """复核包快照输入不合法：账户标识缺失/重复/非字符串，或投票权非非负整数。"""


class BallotValidationError(TallyError):
    """复核包选票不合法：投票者不在快照、同一账户多票、选项非字符串
    （给出 choices 时还要求选项在 choices 内）、普通票来自已委托他人者
    （直接投票与委托不得同时使用票权）、覆盖票来自未委托他人者，
    或 delegation_override 非布尔。"""


class DelegationConflictError(TallyError):
    """复核包委托关系不合法：引用快照外账户、自委托、成环，
    或一个账户指向多个受托人。"""


class ClaimedResultValidationError(TallyError):
    """待核对结果不是对象、含公开结果字段之外的未知字段（无 decision_rules
    配置时含 quorum_met/approval_met/decision 三个门槛字段），
    或 results_by_choice 不是字符串键的对象。叶子值的类型或取值错误
    不作为异常，按逐项差异（mismatched）返回。"""


def _is_int(value: Any) -> bool:
    """bool 在 Python 中是 int 的子类，计票场景一律拒绝。"""
    return isinstance(value, int) and not isinstance(value, bool)


def _validate_decision_rules(rules: Any, choices: list[str]) -> dict:
    """校验 decision_rules：恰好三个字段，类型与取值合法。"""
    if not isinstance(rules, dict):
        raise InvalidInputError("field 'decision_rules' must be an object")

    keys = set(rules)
    required = set(_DECISION_RULE_FIELDS)
    for field in _DECISION_RULE_FIELDS:
        if field not in keys:
            raise InvalidInputError(f"decision_rules missing field: {field!r}")
    for field in rules:
        if field not in required:
            raise InvalidInputError(
                f"decision_rules has unexpected field: {field!r}"
            )

    approval_choices = rules["approval_choices"]
    if not isinstance(approval_choices, list):
        raise InvalidInputError(
            "decision_rules.approval_choices must be a list"
        )
    if len(approval_choices) == 0:
        raise InvalidInputError(
            "decision_rules.approval_choices must not be empty"
        )
    choice_set = set(choices)
    seen_approval: set[str] = set()
    for choice in approval_choices:
        if not isinstance(choice, str):
            raise InvalidInputError(
                "every decision_rules.approval_choices member must be a string"
            )
        if choice not in choice_set:
            raise InvalidInputError(
                f"decision_rules.approval_choices member not in choices: {choice!r}"
            )
        if choice in seen_approval:
            raise InvalidInputError(
                f"duplicate decision_rules.approval_choices member: {choice!r}"
            )
        seen_approval.add(choice)

    min_counted_weight = rules["min_counted_weight"]
    if not _is_int(min_counted_weight):
        raise InvalidInputError(
            "decision_rules.min_counted_weight must be an integer"
        )
    if min_counted_weight < 0:
        raise InvalidInputError(
            "decision_rules.min_counted_weight must be non-negative"
        )

    approval_basis_points = rules["approval_basis_points"]
    if not _is_int(approval_basis_points):
        raise InvalidInputError(
            "decision_rules.approval_basis_points must be an integer"
        )
    if not 1 <= approval_basis_points <= 10000:
        raise InvalidInputError(
            "decision_rules.approval_basis_points must be between 1 and 10000"
        )

    return rules


def _validate_choices_field(choices: Any) -> list:
    """校验 choices：非空、无重复的字符串列表。"""
    if not isinstance(choices, list):
        raise InvalidInputError("field 'choices' must be a list")
    if len(choices) == 0:
        raise InvalidInputError("field 'choices' must not be empty")
    seen_choices: set[str] = set()
    for choice in choices:
        if not isinstance(choice, str):
            raise InvalidInputError("every choice must be a string")
        if choice in seen_choices:
            raise InvalidInputError(f"duplicate choice: {choice!r}")
        seen_choices.add(choice)
    return choices


def _validate_votes_field(votes: Any) -> list:
    """校验 votes：对象列表，voter/choice 为字符串，覆盖标志为布尔。"""
    if not isinstance(votes, list):
        raise InvalidInputError("field 'votes' must be a list")
    for index, vote in enumerate(votes):
        if not isinstance(vote, dict):
            raise InvalidInputError(f"votes[{index}] must be an object")
        for key in ("voter", "choice"):
            if key not in vote:
                raise InvalidInputError(f"votes[{index}] missing field: {key!r}")
        if not isinstance(vote["voter"], str):
            raise InvalidInputError(f"votes[{index}].voter must be a string")
        if not isinstance(vote["choice"], str):
            raise InvalidInputError(f"votes[{index}].choice must be a string")
        if "delegation_override" in vote and not isinstance(
            vote["delegation_override"], bool
        ):
            raise InvalidInputError(
                f"votes[{index}].delegation_override must be a boolean"
            )
    return votes


def _validate_snapshot_field(snapshot: Any) -> dict:
    """校验 snapshot：字符串账户映射到非负整数权重。"""
    if not isinstance(snapshot, dict):
        raise InvalidInputError("field 'snapshot' must be an object")
    for account, weight in snapshot.items():
        if not isinstance(account, str):
            raise InvalidInputError("snapshot keys must be strings")
        if not _is_int(weight):
            raise InvalidInputError(f"snapshot weight for {account!r} must be an integer")
        if weight < 0:
            raise InvalidInputError(f"snapshot weight for {account!r} must be non-negative")
    return snapshot


def _validate_input(data: Any):
    """校验输入结构与类型，返回解包后的各字段。"""
    if not isinstance(data, dict):
        raise InvalidInputError("input must be a JSON object")

    for field in ("proposal_id", "choices", "votes", "snapshot", "delegations"):
        if field not in data:
            raise InvalidInputError(f"missing field: {field!r}")

    proposal_id = data["proposal_id"]
    if not isinstance(proposal_id, str):
        raise InvalidInputError("field 'proposal_id' must be a string")

    choices = _validate_choices_field(data["choices"])
    votes = _validate_votes_field(data["votes"])
    snapshot = _validate_snapshot_field(data["snapshot"])

    delegations = data["delegations"]
    if not isinstance(delegations, dict):
        raise InvalidInputError("field 'delegations' must be an object")
    for account, target in delegations.items():
        if not isinstance(account, str) or not isinstance(target, str):
            raise InvalidInputError("delegation accounts must be strings")

    decision_rules = None
    if "decision_rules" in data:
        decision_rules = _validate_decision_rules(
            data["decision_rules"], choices
        )

    return proposal_id, choices, votes, snapshot, delegations, decision_rules


def _validate_events_input(data: Any):
    """校验事件计票输入：以 snapshot_block 与 delegation_events 取代 delegations。"""
    if not isinstance(data, dict):
        raise InvalidInputError("input must be a JSON object")

    for field in (
        "proposal_id",
        "choices",
        "votes",
        "snapshot",
        "snapshot_block",
        "delegation_events",
    ):
        if field not in data:
            raise InvalidInputError(f"missing field: {field!r}")

    proposal_id = data["proposal_id"]
    if not isinstance(proposal_id, str):
        raise InvalidInputError("field 'proposal_id' must be a string")

    choices = _validate_choices_field(data["choices"])
    votes = _validate_votes_field(data["votes"])
    snapshot = _validate_snapshot_field(data["snapshot"])

    snapshot_block = data["snapshot_block"]
    if not _is_int(snapshot_block) or snapshot_block < 0:
        raise InvalidInputError(
            "field 'snapshot_block' must be a non-negative integer"
        )

    decision_rules = None
    if "decision_rules" in data:
        decision_rules = _validate_decision_rules(
            data["decision_rules"], choices
        )

    return (
        proposal_id,
        choices,
        votes,
        snapshot,
        snapshot_block,
        data["delegation_events"],
        decision_rules,
    )


# 委托事件恰好包含的字段；trustee 为 null 表示清除委托。
_DELEGATION_EVENT_FIELDS = ("block", "delegator", "trustee")


def _apply_delegation_events(events: Any, snapshot: dict, snapshot_block: int) -> dict:
    """校验委托事件流并按序重放到 snapshot_block，返回生效的委托映射。

    每个事件恰好含 ``block`` / ``delegator`` / ``trustee``；block 为非负整数
    且全程不得递减，同块后项覆盖前项；trustee 为 null 清除该账户的委托，
    非 null 时不得等于 delegator；delegator 与非 null trustee 都必须在
    snapshot 内。仅 block 不大于 snapshot_block 的事件生效，之后的事件
    只校验、不改变状态。事件本身的非法一律抛 DelegationEventError。
    """
    if not isinstance(events, list):
        raise DelegationEventError("field 'delegation_events' must be a list")
    delegations: dict[str, str] = {}
    last_block: int | None = None
    for index, event in enumerate(events):
        if not isinstance(event, dict):
            raise DelegationEventError(
                f"delegation_events[{index}] must be an object"
            )
        for field in _DELEGATION_EVENT_FIELDS:
            if field not in event:
                raise DelegationEventError(
                    f"delegation_events[{index}] missing field: {field!r}"
                )
        for field in event:
            if field not in _DELEGATION_EVENT_FIELDS:
                raise DelegationEventError(
                    f"delegation_events[{index}] has unexpected field: {field!r}"
                )
        block = event["block"]
        if not _is_int(block) or block < 0:
            raise DelegationEventError(
                f"delegation_events[{index}].block must be a non-negative integer"
            )
        if last_block is not None and block < last_block:
            raise DelegationEventError(
                f"delegation_events[{index}].block must not decrease"
            )
        last_block = block
        delegator = event["delegator"]
        trustee = event["trustee"]
        if not isinstance(delegator, str):
            raise DelegationEventError(
                f"delegation_events[{index}].delegator must be a string"
            )
        if trustee is not None and not isinstance(trustee, str):
            raise DelegationEventError(
                f"delegation_events[{index}].trustee must be a string or null"
            )
        if delegator not in snapshot:
            raise DelegationEventError(
                f"delegation event delegator not in snapshot: {delegator!r}"
            )
        if trustee is not None and trustee not in snapshot:
            raise DelegationEventError(
                f"delegation event trustee not in snapshot: {trustee!r}"
            )
        if trustee is not None and trustee == delegator:
            raise DelegationEventError(
                "delegation event trustee must differ from delegator: "
                f"{delegator!r}"
            )
        if block > snapshot_block:
            # 快照之后的事件无效：结构与顺序已在上方校验，不改变状态。
            continue
        if trustee is None:
            delegations.pop(delegator, None)
        else:
            delegations[delegator] = trustee
    return delegations


def _resolve_event_trustees(snapshot: dict, delegations: dict) -> dict:
    """按 snapshot 顺序解析各账户最终受托人；成环抛 DelegationEventError。"""
    try:
        return {
            account: _resolve_trustee(account, delegations) for account in snapshot
        }
    except DelegationCycleError as exc:
        raise DelegationEventError(str(exc)) from exc


def _check_delegation_accounts(snapshot: dict, delegations: dict) -> None:
    """委托方与受托方都必须在 snapshot 内。"""
    for account, target in delegations.items():
        if account not in snapshot:
            raise InvalidDelegationError(
                f"delegating account not in snapshot: {account!r}"
            )
        if target not in snapshot:
            raise InvalidDelegationError(
                f"delegation target not in snapshot: {target!r}"
            )


def _resolve_trustee(account: str, delegations: dict) -> str:
    """逐跳解析最终受托人。

    * 无委托：受托人即本人；
    * 自委托（A -> A）：委托在本人处终止，受托人即本人；
    * 链上进入已访问节点即成环且无法到达最终受托人，抛 DelegationCycleError。
    """
    visited: set[str] = set()
    current = account
    while current in delegations:
        target = delegations[current]
        if target == current:
            return current
        if current in visited:
            raise DelegationCycleError(
                f"delegation cycle detected at account: {current!r}"
            )
        visited.add(current)
        current = target
    return current


def _winners(choices: list[str], per_choice: dict) -> tuple[list[str], bool]:
    """唯一最大 -> [该 choice]；并列最大 -> 全部并列项；无有效票 -> ([], False)。"""
    max_weight = max(per_choice.values())
    if max_weight == 0:
        return [], False
    leaders = [choice for choice in choices if per_choice[choice] == max_weight]
    if len(leaders) == 1:
        return leaders, False
    return leaders, True


_UNSET = object()


def _resolve_provenance_flag(data: dict, include_provenance: Any) -> bool:
    """解析 include_provenance：显式参数优先，否则读输入字段，默认 False。"""
    if include_provenance is _UNSET:
        include_provenance = data.get("include_provenance", False)
    if not isinstance(include_provenance, bool):
        raise InvalidInputError("include_provenance must be a boolean")
    return include_provenance


def _compute(data: Any, include_provenance: Any = _UNSET) -> dict:
    (
        proposal_id,
        choices,
        votes,
        snapshot,
        delegations,
        decision_rules,
    ) = _validate_input(data)
    _check_delegation_accounts(snapshot, delegations)
    include_provenance = _resolve_provenance_flag(data, include_provenance)
    return _compute_core(
        proposal_id,
        choices,
        votes,
        snapshot,
        delegations,
        decision_rules,
        include_provenance,
    )


def _compute_from_events(data: Any, include_provenance: Any = _UNSET) -> dict:
    """按委托事件流重建 snapshot_block 时点的委托状态后计票。

    结果在既有字段之后追加 ``snapshot_block`` 与 ``resolved_delegations``
    （按 snapshot 顺序列各账户最终受托人）；有 decision_rules 时门槛字段随后。
    """
    (
        proposal_id,
        choices,
        votes,
        snapshot,
        snapshot_block,
        events,
        decision_rules,
    ) = _validate_events_input(data)
    include_provenance = _resolve_provenance_flag(data, include_provenance)
    delegations = _apply_delegation_events(events, snapshot, snapshot_block)
    trustee_of = _resolve_event_trustees(snapshot, delegations)
    resolved_delegations = {
        account: trustee_of[account] for account in snapshot
    }
    return _compute_core(
        proposal_id,
        choices,
        votes,
        snapshot,
        delegations,
        decision_rules,
        include_provenance,
        trustee_of=trustee_of,
        extra_fields={
            "snapshot_block": snapshot_block,
            "resolved_delegations": resolved_delegations,
        },
    )


def _compute_core(
    proposal_id: str,
    choices: list,
    votes: list,
    snapshot: dict,
    delegations: dict,
    decision_rules: dict | None,
    include_provenance: bool,
    trustee_of: dict | None = None,
    extra_fields: dict | None = None,
) -> dict:
    """计票核心：委托解析、权重合并、覆盖扣减、来源追踪与门槛判定。

    ``trustee_of`` 省略时按 delegations 逐跳解析；``extra_fields``（事件路径的
    snapshot_block / resolved_delegations）追加在既有字段之后、门槛字段之前。
    """
    # 为每个快照账户解析最终受托人；环在此处即被发现，即使该账户未投票。
    if trustee_of is None:
        trustee_of = {
            account: _resolve_trustee(account, delegations) for account in snapshot
        }

    # 每个账户的快照权重只并入其最终受托人一次，不重复计数。
    bucket = {account: 0 for account in snapshot}
    for account, weight in snapshot.items():
        bucket[trustee_of[account]] += weight

    # 覆盖票预先抽出本人权重：无论选票顺序如何，受托人计票时都只拿剩余合并权重，
    # 保证本人权重不会同时计入覆盖票与受托人票。
    withdrawn_total = {account: 0 for account in snapshot}
    override_voters: set[str] = set()
    for vote in votes:
        if not vote.get("delegation_override", False):
            continue
        voter = vote["voter"]
        if voter in snapshot and trustee_of[voter] != voter:
            override_voters.add(voter)
            withdrawn_total[trustee_of[voter]] += snapshot[voter]

    choice_set = set(choices)
    per_choice = {choice: 0 for choice in choices}
    effective_weights: dict[str, int] = {}
    weight_provenance: dict[str, dict[str, int]] = {}
    seen_voters: set[str] = set()

    for vote in votes:
        voter = vote["voter"]
        choice = vote["choice"]
        override = vote.get("delegation_override", False)

        if voter not in snapshot:
            raise InvalidVoteError(f"voter not in snapshot: {voter!r}")
        if choice not in choice_set:
            raise InvalidVoteError(f"choice not in choices: {choice!r}")
        if voter in seen_voters:
            raise InvalidVoteError(f"duplicate voter: {voter!r}")
        seen_voters.add(voter)

        trustee = trustee_of[voter]
        if override:
            # 覆盖票只允许委托链指向他人的账户投出；记本人权重，不传给受托人。
            if trustee == voter:
                raise InvalidVoteError(
                    f"override voter has not delegated to another account: {voter!r}"
                )
            weight = snapshot[voter]
            # 覆盖票的来源只有投票者本人；零权重时来源映射为空。
            sources = {voter: weight} if weight > 0 else {}
        else:
            # 普通票只由最终受托人发出；所持权重已扣除被覆盖票抽走的部分。
            if trustee != voter:
                raise InvalidVoteError(
                    f"voter has delegated to another account: {voter!r}"
                )
            weight = bucket[voter] - withdrawn_total[voter]
            # 普通票的来源为全部汇入账户（含受托人本人），按 snapshot 顺序，
            # 只列正权重；被覆盖票抽走本人权重者不进入该票。
            sources = {
                account: snapshot[account]
                for account in snapshot
                if trustee_of[account] == voter
                and account not in override_voters
                and snapshot[account] > 0
            }

        per_choice[choice] += weight
        effective_weights[voter] = weight
        if include_provenance:
            weight_provenance[voter] = sources

    snapshot_total_weight = sum(snapshot.values())
    counted_weight = sum(per_choice.values())
    uncounted_weight = snapshot_total_weight - counted_weight
    winners, is_tie = _winners(choices, per_choice)

    result = {
        "proposal_id": proposal_id,
        "per_choice": per_choice,
        "effective_weights": effective_weights,
    }
    if include_provenance:
        result["weight_provenance"] = weight_provenance
    result.update(
        {
            "uncounted_weight": uncounted_weight,
            "counted_weight": counted_weight,
            "snapshot_total_weight": snapshot_total_weight,
            "winners": winners,
            "is_tie": is_tie,
        }
    )
    if extra_fields:
        result.update(extra_fields)
    if decision_rules is not None:
        result.update(
            _decide(decision_rules, per_choice, counted_weight)
        )
    return result


def _decide(
    decision_rules: dict, per_choice: dict, counted_weight: int
) -> dict:
    """按门槛规则计算 quorum_met、approval_met 与 decision。"""
    quorum_met = (
        counted_weight > 0
        and counted_weight >= decision_rules["min_counted_weight"]
    )
    approval_weight = sum(
        per_choice[choice]
        for choice in decision_rules["approval_choices"]
    )
    basis_points = decision_rules["approval_basis_points"]
    approval_met = (
        approval_weight * 10000 >= counted_weight * basis_points
    )
    if not quorum_met:
        decision = "no_quorum"
    elif approval_met:
        decision = "approved"
    else:
        decision = "rejected"
    return {
        "quorum_met": quorum_met,
        "approval_met": approval_met,
        "decision": decision,
    }


def tally_proposal(input_data: dict, include_provenance: Any = _UNSET) -> dict:
    """对一次提案计票，返回结果字典；非法输入抛出对应异常。

    ``include_provenance`` 为 True 时结果增加 ``weight_provenance``；
    省略时回退到输入中的 ``include_provenance`` 字段，默认 False（输出形状不变）。

    输入含 ``decision_rules`` 时，结果在既有字段之后追加
    ``quorum_met`` / ``approval_met`` / ``decision``；省略时输出形状不变。
    """
    return _compute(input_data, include_provenance)


def tally_proposal_from_events(input_data: dict, include_provenance: Any = _UNSET) -> dict:
    """按委托事件流重建 snapshot_block 时点的委托状态并计票。

    输入沿用提案字段，以非负整数 ``snapshot_block`` 和 ``delegation_events``
    取代 ``delegations``；事件恰好含 ``block`` / ``delegator`` / ``trustee``
    （null 表示清除委托），按序生效、block 不得递减、同块后项覆盖前项，
    仅不大于 snapshot_block 的事件生效。委托语义（逐跳解析、覆盖扣减、
    权重合并、来源与门槛规则）与 ``tally_proposal`` 一致；结果在既有字段
    之后追加 ``snapshot_block`` 与 ``resolved_delegations``（按 snapshot
    顺序列各账户最终受托人），有 decision_rules 时门槛字段随后。
    事件非法抛 DelegationEventError，其余异常与 ``tally_proposal`` 相同。
    """
    return _compute_from_events(input_data, include_provenance)


def verify_tally(
    input_data: dict, result: Any, include_provenance: Any = _UNSET
) -> bool:
    """独立复核计票结果。

    按覆盖语义独立重算，检查字段完整性、choice 顺序、权重归属
    （覆盖票记本人权重、受托人记剩余合并权重、选票归属）、
    权重守恒（counted + uncounted == snapshot_total）与汇总一致性。
    ``include_provenance`` 为 True 时另复核 ``weight_provenance``
    （存在性、内外层键及顺序、来源归属、来源值等于 snapshot 权重、
    映射求和等于 effective_weights、同一来源至多归属一张计票）；
    为 False 时结果中的额外字段不受约束。
    输入含 ``decision_rules`` 时另复核 ``quorum_met`` /
    ``approval_met`` / ``decision`` 的集合、尾部顺序、类型、取值与
    决策一致性；无配置时结果不得含这些字段。
    全部一致返回 True，否则抛 TallyVerificationError。
    """
    expected = _compute(input_data, include_provenance)
    return _verify_result(expected, result)


def verify_tally_from_events(
    input_data: dict, result: Any, include_provenance: Any = _UNSET
) -> bool:
    """独立复核事件流计票结果。

    按事件流独立重算 ``tally_proposal_from_events`` 的全部结果，在
    ``verify_tally`` 的复核项（字段、归属、守恒、汇总、来源、赢家与门槛）
    之上另复核 ``snapshot_block`` 与 ``resolved_delegations`` 的存在性、
    类型、账户顺序与取值。缺失、乱序或数值不符抛 TallyVerificationError，
    全部一致返回 True。
    """
    expected = _compute_from_events(input_data, include_provenance)
    return _verify_result(expected, result, event_fields=True)


def _verify_result(expected: dict, result: Any, event_fields: bool = False) -> bool:
    """把计票结果与独立重算值逐项比对；全部一致返回 True。"""
    if not isinstance(result, dict):
        raise TallyVerificationError("result must be an object")
    required_fields = _RESULT_FIELDS
    if event_fields:
        required_fields = required_fields + (
            "snapshot_block",
            "resolved_delegations",
        )
    for field in required_fields:
        if field not in result:
            raise TallyVerificationError(f"result missing field: {field!r}")

    if result["proposal_id"] != expected["proposal_id"]:
        raise TallyVerificationError("proposal_id does not match input")

    per_choice = result["per_choice"]
    expected_per_choice = expected["per_choice"]
    if not isinstance(per_choice, dict):
        raise TallyVerificationError("per_choice must be an object")
    if list(per_choice.keys()) != list(expected_per_choice.keys()):
        raise TallyVerificationError("per_choice choices or their order mismatch")
    for choice, weight in per_choice.items():
        if not _is_int(weight) or weight < 0:
            raise TallyVerificationError(
                f"per_choice weight must be a non-negative integer: {choice!r}"
            )
        if weight != expected_per_choice[choice]:
            raise TallyVerificationError(f"per_choice weight mismatch: {choice!r}")

    effective_weights = result["effective_weights"]
    expected_effective = expected["effective_weights"]
    if not isinstance(effective_weights, dict):
        raise TallyVerificationError("effective_weights must be an object")
    if list(effective_weights.items()) != list(expected_effective.items()):
        raise TallyVerificationError("effective_weights mismatch")
    for account, weight in effective_weights.items():
        if not _is_int(weight) or weight < 0:
            raise TallyVerificationError(
                f"effective weight must be a non-negative integer: {account!r}"
            )

    for field in ("uncounted_weight", "counted_weight", "snapshot_total_weight"):
        value = result[field]
        if not _is_int(value) or value < 0:
            raise TallyVerificationError(f"{field} must be a non-negative integer")
        if value != expected[field]:
            raise TallyVerificationError(f"{field} mismatch")

    if result["counted_weight"] + result["uncounted_weight"] != result[
        "snapshot_total_weight"
    ]:
        raise TallyVerificationError(
            "counted_weight + uncounted_weight != snapshot_total_weight"
        )
    if sum(per_choice.values()) != result["counted_weight"]:
        raise TallyVerificationError("sum of per_choice weights != counted_weight")
    if sum(effective_weights.values()) != result["counted_weight"]:
        raise TallyVerificationError("sum of effective_weights != counted_weight")

    if event_fields:
        snapshot_block = result["snapshot_block"]
        if not _is_int(snapshot_block) or snapshot_block < 0:
            raise TallyVerificationError(
                "snapshot_block must be a non-negative integer"
            )
        if snapshot_block != expected["snapshot_block"]:
            raise TallyVerificationError("snapshot_block mismatch")
        resolved = result["resolved_delegations"]
        if not isinstance(resolved, dict):
            raise TallyVerificationError("resolved_delegations must be an object")
        expected_resolved = expected["resolved_delegations"]
        if list(resolved.keys()) != list(expected_resolved.keys()):
            raise TallyVerificationError(
                "resolved_delegations accounts or their order mismatch"
            )
        for account, trustee in resolved.items():
            if not isinstance(trustee, str):
                raise TallyVerificationError(
                    f"resolved trustee must be a string: {account!r}"
                )
            if trustee != expected_resolved[account]:
                raise TallyVerificationError(
                    f"resolved_delegations trustee mismatch: {account!r}"
                )

    winners = result["winners"]
    if not isinstance(winners, list) or winners != expected["winners"]:
        raise TallyVerificationError("winners mismatch")
    is_tie = result["is_tie"]
    if not isinstance(is_tie, bool) or is_tie != expected["is_tie"]:
        raise TallyVerificationError("is_tie mismatch")

    if "weight_provenance" in expected:
        _verify_provenance(result, expected, effective_weights)

    _verify_decisions(result, expected)

    return True


def _verify_decisions(result: dict, expected: dict) -> None:
    """复核门槛字段：集合、尾部顺序、类型、取值与决策一致性。

    有配置时结果键集合与顺序必须与重算结果完全一致（任何多余字段也算集合不一致）；
    无配置时沿用既有兼容行为，仅禁止出现三个门槛字段，其余额外字段不约束。
    """
    if "decision" not in expected:
        for field in _DECISION_FIELDS:
            if field in result:
                raise TallyVerificationError(
                    f"unexpected field without decision_rules: {field!r}"
                )
        return

    for field in _DECISION_FIELDS:
        if field not in result:
            raise TallyVerificationError(f"result missing field: {field!r}")

    # 集合与顺序必须与重算结果完全一致：三个字段恰好作为尾部追加，
    # 任何缺失、多余或乱序都算不一致。
    if list(result.keys()) != list(expected.keys()):
        raise TallyVerificationError(
            "result fields, their set or their order mismatch"
        )

    for field in ("quorum_met", "approval_met"):
        value = result[field]
        if not isinstance(value, bool):
            raise TallyVerificationError(f"{field} must be a boolean")
        if value != expected[field]:
            raise TallyVerificationError(f"{field} mismatch")

    decision = result["decision"]
    if not isinstance(decision, str) or decision not in _DECISIONS:
        raise TallyVerificationError("decision must be one of the decision labels")
    if decision != expected["decision"]:
        raise TallyVerificationError("decision mismatch")

    # 决策必须与两个判定一致，不能出现标志与结论互相矛盾的组合。
    if not result["quorum_met"]:
        consistent = decision == "no_quorum"
    elif result["approval_met"]:
        consistent = decision == "approved"
    else:
        consistent = decision == "rejected"
    if not consistent:
        raise TallyVerificationError("decision is inconsistent with the thresholds")


def _verify_provenance(result: dict, expected: dict, effective_weights: dict) -> None:
    """复核 weight_provenance：存在性、键序、归属、取值、求和与唯一归属。"""
    if "weight_provenance" not in result:
        raise TallyVerificationError("result missing field: 'weight_provenance'")
    provenance = result["weight_provenance"]
    if not isinstance(provenance, dict):
        raise TallyVerificationError("weight_provenance must be an object")
    expected_provenance = expected["weight_provenance"]
    if list(provenance.keys()) != list(expected_provenance.keys()):
        raise TallyVerificationError("weight_provenance voters or their order mismatch")

    seen_sources: set[str] = set()
    for voter, sources in provenance.items():
        if not isinstance(sources, dict):
            raise TallyVerificationError(
                f"weight_provenance entry must be an object: {voter!r}"
            )
        expected_sources = expected_provenance[voter]
        if list(sources.keys()) != list(expected_sources.keys()):
            raise TallyVerificationError(
                f"weight_provenance sources or their order mismatch: {voter!r}"
            )
        for source, source_weight in sources.items():
            if not _is_int(source_weight) or source_weight <= 0:
                raise TallyVerificationError(
                    "weight_provenance source weight must be a positive integer: "
                    f"{source!r}"
                )
            if source_weight != expected_sources[source]:
                raise TallyVerificationError(
                    f"weight_provenance source weight mismatch: {source!r}"
                )
            if source in seen_sources:
                raise TallyVerificationError(
                    f"weight_provenance source attributed to multiple votes: {source!r}"
                )
            seen_sources.add(source)
        if sum(sources.values()) != effective_weights[voter]:
            raise TallyVerificationError(
                f"weight_provenance sum does not equal effective weight: {voter!r}"
            )


def _validate_review_snapshot(
    proposal_id: Any, snapshot_block: Any, snapshot: Any
) -> dict[str, int]:
    """校验复核包的提案标识、快照区块与快照账户权重。"""
    if not isinstance(proposal_id, str):
        raise SnapshotIntegrityError("review package 'proposal_id' must be a string")
    if not _is_int(snapshot_block) or snapshot_block < 0:
        raise SnapshotIntegrityError(
            "review package 'snapshot_block' must be a non-negative integer"
        )
    if not isinstance(snapshot, dict):
        raise SnapshotIntegrityError(
            "review package 'snapshot' must be an object of account -> weight"
        )
    for account, weight in snapshot.items():
        if not isinstance(account, str):
            raise SnapshotIntegrityError("snapshot account identifiers must be strings")
        if not _is_int(weight):
            raise SnapshotIntegrityError(
                f"snapshot weight must be an integer: {account!r}"
            )
        if weight < 0:
            raise SnapshotIntegrityError(
                f"snapshot weight must be non-negative: {account!r}"
            )
    # dict 键天然唯一；返回副本，避免调用方在计算期间变更输入影响确定性。
    return dict(snapshot)


def _validate_review_delegations(
    delegations: Any, snapshot: dict[str, int]
) -> dict[str, str]:
    """校验委托关系：仅快照账户之间、无自委托、无环、每账户唯一受托。"""
    if not isinstance(delegations, dict):
        raise DelegationConflictError(
            "review package 'delegations' must be an object of account -> trustee"
        )
    resolved: dict[str, str] = {}
    for delegator, target in delegations.items():
        if not isinstance(delegator, str) or not isinstance(target, str):
            raise DelegationConflictError(
                "delegation accounts and trustees must be strings"
            )
        if delegator not in snapshot:
            raise DelegationConflictError(
                f"delegating account not in snapshot: {delegator!r}"
            )
        if target not in snapshot:
            raise DelegationConflictError(
                f"delegation target not in snapshot: {target!r}"
            )
        if target == delegator:
            raise DelegationConflictError(
                f"self-delegation is not allowed: {delegator!r}"
            )
    # 结构校验通过后，逐跳解析最终受托人并在此处发现环；
    # 继续沿用 _resolve_trustee 的既成环判定作为事实来源。
    for account in snapshot:
        try:
            resolved[account] = _resolve_trustee(account, delegations)
        except DelegationCycleError as exc:
            raise DelegationConflictError(str(exc)) from exc
    return resolved


def _validate_review_choices(choices: Any) -> list[str] | None:
    """校验复核包的可选 choices：非空字符串列表、无重复；省略返回 None。"""
    if choices is None:
        return None
    if not isinstance(choices, list):
        raise InvalidInputError("review package 'choices' must be a list")
    if len(choices) == 0:
        raise InvalidInputError("review package 'choices' must not be empty")
    validated: list[str] = []
    seen: set[str] = set()
    for choice in choices:
        if not isinstance(choice, str):
            raise InvalidInputError("every review package choice must be a string")
        if choice in seen:
            raise InvalidInputError(f"duplicate review package choice: {choice!r}")
        seen.add(choice)
        validated.append(choice)
    return validated


def _validate_review_votes(
    votes: Any,
    snapshot: dict[str, int],
    delegations: dict[str, str],
    choice_set: set[str] | None,
) -> list[dict]:
    """校验选票：对象列表、voter/choice 为字符串、voter 在快照内、
    同一账户至多一票；给出 choices 时 choice 必须在其中。

    普通票（delegation_override 省略或 false）只由未委托他人者投出；
    覆盖票（delegation_override 为 true）只允许已委托他人者投出，
    其本人权重直接计入所选选项，不再交给受托人。
    delegation_override 取值非布尔视为选票不合法。
    """
    if not isinstance(votes, list):
        raise BallotValidationError("review package 'votes' must be a list")
    validated: list[dict] = []
    seen_voters: set[str] = set()
    for index, vote in enumerate(votes):
        if not isinstance(vote, dict):
            raise BallotValidationError(f"votes[{index}] must be an object")
        if "voter" not in vote or "choice" not in vote:
            raise BallotValidationError(
                f"votes[{index}] missing field: "
                + ("'voter'" if "voter" not in vote else "'choice'")
            )
        voter = vote["voter"]
        choice = vote["choice"]
        if not isinstance(voter, str):
            raise BallotValidationError(f"votes[{index}].voter must be a string")
        if not isinstance(choice, str):
            raise BallotValidationError(f"votes[{index}].choice must be a string")
        if "delegation_override" in vote and not isinstance(
            vote["delegation_override"], bool
        ):
            raise BallotValidationError(
                f"votes[{index}].delegation_override must be a boolean"
            )
        override = vote.get("delegation_override", False)
        if voter not in snapshot:
            raise BallotValidationError(
                f"voter not in snapshot: {voter!r}"
            )
        if choice_set is not None and choice not in choice_set:
            raise BallotValidationError(
                f"choice not in choices: {choice!r}"
            )
        if voter in seen_voters:
            raise BallotValidationError(f"duplicate voter: {voter!r}")
        if override:
            # 覆盖票只允许已把票权委托给他人的账户投出（复核包禁止自委托，
            # 故进入 delegations 即表示最终受托人是他人）。
            if voter not in delegations:
                raise BallotValidationError(
                    f"override voter has not delegated to another account: {voter!r}"
                )
        elif voter in delegations:
            raise BallotValidationError(
                f"voter has delegated voting power to another account: {voter!r}"
            )
        seen_voters.add(voter)
        validated_vote = {"voter": voter, "choice": choice}
        if override:
            validated_vote["delegation_override"] = True
        validated.append(validated_vote)
    return validated


def _validate_claimed_result(claimed_result: Any, known_fields: tuple[str, ...]) -> dict:
    """校验待核对结果的结构契约：必须为对象，只含公开字段，
    且 results_by_choice（若给出）必须是字符串键的对象。

    叶子值的类型与取值不在此处约束——任何与计算值不一致的取值
    （含缺失、null、错误类型、负数）都按逐项差异处理，不抛异常；
    计算结果本身也不读取待核对结果的任何字段。
    """
    if not isinstance(claimed_result, dict):
        raise ClaimedResultValidationError("claimed result must be an object")
    allowed = set(known_fields)
    for field in claimed_result:
        if field not in allowed:
            raise ClaimedResultValidationError(
                f"claimed result has unknown field: {field!r}"
            )

    claimed_by_choice = claimed_result.get("results_by_choice")
    if claimed_by_choice is not None:
        if not isinstance(claimed_by_choice, dict):
            raise ClaimedResultValidationError(
                "claimed 'results_by_choice' must be an object of choice -> weight"
            )
        for choice in claimed_by_choice:
            if not isinstance(choice, str):
                raise ClaimedResultValidationError(
                    "claimed 'results_by_choice' keys must be choice strings"
                )
    return claimed_result


def _strict_equal(left: Any, right: Any) -> bool:
    """类型敏感的叶子相等比较：布尔不与整数等同（bool 是 int 子类），
    整数不与浮点等同，与既有计票引擎拒绝 bool/float 权重的严格性一致。"""
    if isinstance(left, bool) or isinstance(right, bool):
        return isinstance(left, bool) and isinstance(right, bool) and left == right
    if _is_int(left) or _is_int(right):
        return _is_int(left) and _is_int(right) and left == right
    return left == right


def _review_field_differences(
    computed: dict, claimed_result: dict, known_fields: tuple[str, ...]
) -> list[dict]:
    """按公开字段的固定顺序逐项比对，返回字段名、计算值与待核对值。

    缺失字段以待核对值 ``None`` 记为差异；叶子值不做类型约束，任何
    取值差异（含错误类型，如 ``9.0``、``True`` 或字符串）都逐项返回；
    results_by_choice 的选项集合或任一权重不同都算该字段的一项差异，
    比对前按选项名归一化排序。差异不是异常。
    """
    differences: list[dict] = []
    for field in known_fields:
        if field not in claimed_result:
            differences.append(
                {"field": field, "computed": computed[field], "claimed": None}
            )
            continue
        claimed_value = claimed_result[field]
        if field == "results_by_choice" and isinstance(claimed_value, dict):
            # 结构校验已保证键为字符串；排序归一化使比对只依赖语义内容。
            claimed_normalized = dict(sorted(claimed_value.items()))
            computed_normalized = computed[field]
            matches = (
                set(claimed_normalized) == set(computed_normalized)
                and all(
                    _strict_equal(claimed_normalized[choice], computed_normalized[choice])
                    for choice in computed_normalized
                )
            )
            claimed_value = claimed_normalized
            is_equal = matches
        else:
            is_equal = _strict_equal(claimed_value, computed[field])
        if not is_equal:
            differences.append(
                {"field": field, "computed": computed[field], "claimed": claimed_value}
            )
    return differences


def build_review_package(
    proposal_id: str,
    snapshot_block: int,
    snapshot: dict,
    votes: list,
    delegations: dict,
    claimed_result: Any,
    choices: list[str] | None = None,
    decision_rules: dict | None = None,
) -> dict:
    """生成一次提案的独立结果复核包（纯函数，只通过返回值交付）。

    输入为提案标识、快照区块、快照账户及其投票权、投票记录、委托关系与
    待核对结果。沿用公开计票语义：按委托链把未直接投票账户的票权聚合到
    唯一受托人，再按选项累计票权；普通票只由未委托他人者投出，覆盖票
    （``delegation_override`` 为 true）只允许已委托他人者投出，其本人权重
    直接计入所选选项、不再交给受托人，经其转发的上游权重仍归最终受托人。

    ``choices`` 省略时选项集合从投票记录派生；给出时必须是非空、无重复的
    字符串列表，``results_by_choice`` 含全部选项（未投出者为 0）且一律按
    选项名排序。``decision_rules`` 存在时必须同时提供 ``choices``，校验与
    计算沿用 ``tally_proposal``，结果在既有字段之后追加
    ``quorum_met`` / ``approval_met`` / ``decision`` 并与 claimed_result 逐项
    比较；无配置时 claimed_result 出现任一门槛字段报
    ``ClaimedResultValidationError``。

    输出字段固定顺序：``proposal_id`` / ``snapshot_block`` /
    ``results_by_choice`` / ``direct_participated_weight`` /
    ``delegated_weight`` / ``non_participated_weight`` /
    ``effective_delegations`` / ``weight_conservation_holds`` /
    ``review_status`` / ``field_differences``，配置 ``decision_rules`` 时
    在尾部追加三个门槛字段。

    * 顶层结构、``choices``、``decision_rules`` 非法（含配置缺少 choices）->
      ``InvalidInputError``；
    * 快照账户标识非字符串或不唯一、投票权非非负整数 ->
      ``SnapshotIntegrityError``；
    * 投票者不在快照、同一账户多票、选项非字符串（给出 choices 时越界）、
      普通票来自已委托他人者、覆盖票来自未委托他人者或
      delegation_override 非布尔 -> ``BallotValidationError``；
    * 委托引用快照外账户、自委托、成环或一账户多受托人 ->
      ``DelegationConflictError``；
    * 待核对结果不是对象、含未知字段（含无配置时的门槛字段），或
      results_by_choice 不是字符串键对象 -> ``ClaimedResultValidationError``；
      叶子值缺失、null、错误类型或取值错误不作为异常，按逐项差异返回；
    * 字段值不一致不抛异常，``review_status`` 为 ``"mismatched"`` 并逐项
      给出字段名、计算值与待核对值；全部一致为 ``"matched"``。

    空投票集合生成有效的零票复核包；未委托且未投票账户保留为
    ``non_participated_weight``，不丢弃、不分配给任何选项。
    """
    snapshot_weights = _validate_review_snapshot(
        proposal_id, snapshot_block, snapshot
    )
    delegations = dict(delegations) if isinstance(delegations, dict) else delegations
    trustee_of = _validate_review_delegations(delegations, snapshot_weights)
    return _review_package_impl(
        proposal_id,
        snapshot_block,
        snapshot_weights,
        votes,
        delegations,
        trustee_of,
        claimed_result,
        choices,
        decision_rules,
    )


def build_review_package_from_events(
    proposal_id: str,
    snapshot_block: int,
    snapshot: dict,
    votes: list,
    delegation_events: list,
    claimed_result: Any,
    choices: list[str] | None = None,
    decision_rules: dict | None = None,
) -> dict:
    """按委托事件流重建 snapshot_block 时点的委托状态并装配复核包。

    输入沿用 ``build_review_package`` 的 review 字段与 ``claimed_result``，
    以 ``delegation_events`` 取代 ``delegations``；事件校验与重放语义同
    ``tally_proposal_from_events``（非法事件抛 DelegationEventError，投票、
    快照、待核对结果与门槛非法仍用既有异常）。输出保持旧字段顺序，在
    ``field_differences`` 之后追加 ``resolved_delegations``（按 snapshot
    顺序列各账户最终受托人），有 decision_rules 时门槛字段仍在末尾；
    三类权重守恒核对与逐项差异比对不变。
    """
    snapshot_weights = _validate_review_snapshot(
        proposal_id, snapshot_block, snapshot
    )
    delegations = _apply_delegation_events(
        delegation_events, snapshot_weights, snapshot_block
    )
    trustee_of = _resolve_event_trustees(snapshot_weights, delegations)
    resolved_delegations = {
        account: trustee_of[account] for account in snapshot_weights
    }
    return _review_package_impl(
        proposal_id,
        snapshot_block,
        snapshot_weights,
        votes,
        delegations,
        trustee_of,
        claimed_result,
        choices,
        decision_rules,
        resolved_delegations=resolved_delegations,
    )


def _review_package_impl(
    proposal_id: str,
    snapshot_block: int,
    snapshot_weights: dict,
    votes: list,
    delegations: dict,
    trustee_of: dict,
    claimed_result: Any,
    choices: list[str] | None,
    decision_rules: dict | None,
    resolved_delegations: dict | None = None,
) -> dict:
    """复核包装配主体：委托已校验解析，此处完成选票、门槛与逐项差异。"""

    validated_choices = _validate_review_choices(choices)
    if decision_rules is not None and validated_choices is None:
        raise InvalidInputError(
            "review package 'decision_rules' requires 'choices' to be provided"
        )
    rules = None
    if decision_rules is not None:
        # 校验规则与 tally_proposal 完全一致（InvalidInputError 语义不变）。
        rules = _validate_decision_rules(decision_rules, validated_choices)
    choice_set = (
        set(validated_choices) if validated_choices is not None else None
    )

    validated_votes = _validate_review_votes(
        votes, snapshot_weights, delegations, choice_set
    )

    known_fields = _CLAIMED_RESULT_FIELDS
    if rules is not None:
        known_fields = known_fields + _CLAIMED_DECISION_FIELDS
    claimed = _validate_claimed_result(claimed_result, known_fields)

    # 给出完整 choices 时含未投出的零票选项；否则从投票派生。一律按名称排序。
    if validated_choices is not None:
        tally_choices = sorted(validated_choices)
    else:
        tally_choices = sorted({vote["choice"] for vote in validated_votes})
    override_voters = {
        vote["voter"]
        for vote in validated_votes
        if vote.get("delegation_override", False)
    }
    # 普通票投票者即投出票权的最终受托人；覆盖票投票者不是受托人。
    voting_trustees = {
        vote["voter"]
        for vote in validated_votes
        if not vote.get("delegation_override", False)
    }

    results_by_choice: dict[str, int] = {
        choice: 0 for choice in tally_choices
    }
    decision_values: dict[str, Any] = {}
    if tally_choices:
        # 复用公开计票入口的委托传播、覆盖扣除与门槛规则作为事实来源；
        # 入口前的校验已更严格，这里不应再产生既有异常，防御性翻译一次。
        context = {
            "proposal_id": proposal_id,
            "choices": tally_choices,
            "votes": validated_votes,
            "snapshot": snapshot_weights,
            "delegations": delegations,
        }
        if rules is not None:
            context["decision_rules"] = rules
        try:
            tally = _compute(context)
        except InvalidDelegationError as exc:
            raise DelegationConflictError(str(exc)) from exc
        except DelegationCycleError as exc:
            raise DelegationConflictError(str(exc)) from exc
        except InvalidVoteError as exc:
            raise BallotValidationError(str(exc)) from exc
        results_by_choice = dict(tally["per_choice"])
        if rules is not None:
            decision_values = {field: tally[field] for field in _DECISION_FIELDS}

    # 票权三分类（按快照账户逐个归属，恰好覆盖总票权一次）：
    # 直接参与＝投普通票的受托人本人权重 + 覆盖票投票者本人权重；
    # 受托＝本人未覆盖、经有效委托汇入已投票受托人的他人权重；
    # 未参与＝票权既未由本人覆盖投出、最终受托人也未投票的账户权重。
    direct_weight = 0
    delegated_weight = 0
    non_participated_weight = 0
    for account, weight in snapshot_weights.items():
        if account in override_voters:
            # 覆盖票：本人权重直接参与，与其受托人是否投票无关。
            direct_weight += weight
            continue
        trustee = trustee_of[account]
        if trustee == account:
            if account in voting_trustees:
                direct_weight += weight
            else:
                non_participated_weight += weight
        elif trustee in voting_trustees:
            delegated_weight += weight
        else:
            non_participated_weight += weight

    # 有效委托明细：本人未覆盖、票权实际送达已投票受托人的委托，按委托人
    # 排序，受托人取委托链解析后的唯一最终受托人；不列覆盖者本人权重，
    # 实际转发的上游权重仍按来源账户列入；权重求和等于 delegated_weight。
    effective_delegations = [
        {
            "delegator": account,
            "trustee": trustee_of[account],
            "weight": snapshot_weights[account],
        }
        for account in sorted(snapshot_weights)
        if account not in override_voters
        and trustee_of[account] != account
        and trustee_of[account] in voting_trustees
    ]

    snapshot_total_weight = sum(snapshot_weights.values())
    weight_conservation_holds = (
        direct_weight + delegated_weight + non_participated_weight
        == snapshot_total_weight
    )

    computed = {
        "proposal_id": proposal_id,
        "snapshot_block": snapshot_block,
        "results_by_choice": results_by_choice,
        "direct_participated_weight": direct_weight,
        "delegated_weight": delegated_weight,
        "non_participated_weight": non_participated_weight,
    }
    computed.update(decision_values)
    field_differences = _review_field_differences(
        computed, claimed, known_fields
    )
    review_status = "matched" if not field_differences else "mismatched"

    package = {
        "proposal_id": proposal_id,
        "snapshot_block": snapshot_block,
        "results_by_choice": results_by_choice,
        "direct_participated_weight": direct_weight,
        "delegated_weight": delegated_weight,
        "non_participated_weight": non_participated_weight,
        "effective_delegations": effective_delegations,
        "weight_conservation_holds": weight_conservation_holds,
        "review_status": review_status,
        "field_differences": field_differences,
    }
    if resolved_delegations is not None:
        package["resolved_delegations"] = resolved_delegations
    if rules is not None:
        package.update(decision_values)
    return package


def _read_stdin_json() -> Any:
    """从标准输入读取并解析 JSON；非法 JSON 报 InvalidInputError。"""
    raw = sys.stdin.read()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        raise InvalidInputError("input is not valid JSON")


def _read_review_input(required_fields: tuple[str, ...]) -> dict:
    """stdin 读取复核类输入并校验骨架：必填字段齐全、无未知字段、
    可选字段 choices / decision_rules 类型合法（显式 null 按类型非法处理）。"""
    data = _read_stdin_json()
    if not isinstance(data, dict):
        raise InvalidInputError("input must be a JSON object")
    for field in required_fields:
        if field not in data:
            raise InvalidInputError(f"missing field: {field!r}")
    allowed = set(required_fields) | set(_REVIEW_OPTIONAL_INPUT_FIELDS)
    for field in data:
        if field not in allowed:
            raise InvalidInputError(f"unexpected field: {field!r}")
    # 显式 null 与计票入口一致按类型非法处理（API 参数 None 才表示省略）。
    if "choices" in data and not isinstance(data["choices"], list):
        raise InvalidInputError("review package 'choices' must be a list")
    if "decision_rules" in data and not isinstance(
        data["decision_rules"], dict
    ):
        raise InvalidInputError("field 'decision_rules' must be an object")
    return data


def _run_review_cli() -> dict:
    """review 子命令：stdin 读取复核包输入，校验后装配复核包。

    输入必须是含 ``_REVIEW_INPUT_FIELDS`` 六个必填字段的 JSON 对象，
    可另含可选字段 ``choices`` / ``decision_rules``；
    缺必填字段、多未知字段、非法 JSON 或输入非对象均报 InvalidInputError，
    各字段的取值校验由 build_review_package 按既有规则完成。
    """
    data = _read_review_input(_REVIEW_INPUT_FIELDS)
    return build_review_package(
        data["proposal_id"],
        data["snapshot_block"],
        data["snapshot"],
        data["votes"],
        data["delegations"],
        data["claimed_result"],
        data.get("choices"),
        data.get("decision_rules"),
    )


def _run_review_events_cli() -> dict:
    """review-events 子命令：stdin 读取事件复核输入，重放事件后装配复核包。

    输入骨架与 review 相同，仅以 ``delegation_events`` 取代 ``delegations``；
    骨架非法报 InvalidInputError，事件与取值校验由
    build_review_package_from_events 完成。
    """
    data = _read_review_input(_REVIEW_EVENTS_INPUT_FIELDS)
    return build_review_package_from_events(
        data["proposal_id"],
        data["snapshot_block"],
        data["snapshot"],
        data["votes"],
        data["delegation_events"],
        data["claimed_result"],
        data.get("choices"),
        data.get("decision_rules"),
    )


def main(argv: list[str] | None = None) -> int:
    """命令行入口：stdin JSON -> stdout JSON；成功退出 0，异常退出 2。

    无参数时按提案计票输入处理；``review`` 子命令按复核包输入处理；
    ``events`` / ``review-events`` 子命令按委托事件流输入处理，
    只新增输入输出路径，计票与复核语义不变。
    """
    if argv is None:
        argv = sys.argv[1:]
    try:
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8")
        if not argv:
            result = tally_proposal(_read_stdin_json())
        elif argv == ["events"]:
            result = tally_proposal_from_events(_read_stdin_json())
        elif argv == ["review"]:
            result = _run_review_cli()
        elif argv == ["review-events"]:
            result = _run_review_events_cli()
        else:
            raise InvalidInputError(f"unknown command: {argv[0]!r}")
    except TallyError as exc:
        json.dump(
            {"error": type(exc).__name__, "message": str(exc)},
            sys.stdout,
            ensure_ascii=False,
        )
        sys.stdout.write("\n")
        return 2

    json.dump(result, sys.stdout, ensure_ascii=False)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
