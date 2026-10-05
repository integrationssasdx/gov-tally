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
* 命令行：标准输入读取 JSON，标准输出 JSON，异常退出码 2。
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
    "tally_proposal",
    "verify_tally",
    "build_review_package",
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

# 复核包输出字段的稳定顺序。
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

# 待核对结果允许的公开结果字段及其在逐项差异中的确定顺序。
_CLAIMED_RESULT_FIELDS = (
    "proposal_id",
    "snapshot_block",
    "results_by_choice",
    "direct_participated_weight",
    "delegated_weight",
    "non_participated_weight",
)


class TallyError(Exception):
    """所有计票异常的基类。"""


class InvalidInputError(TallyError):
    """输入缺少字段、类型错误、取值非法（如负权重、choices 空或重复）。"""


class InvalidDelegationError(TallyError):
    """delegations 引用了 snapshot 之外的账户。"""


class DelegationCycleError(TallyError):
    """委托链成环且无法到达最终受托人。"""


class InvalidVoteError(TallyError):
    """选票非法：重复 voter、普通票来自已委托他人者、覆盖票来自未委托他人者、
    voter 不在 snapshot、choice 不存在。"""


class TallyVerificationError(TallyError):
    """计票结果未通过独立复核。"""


class SnapshotIntegrityError(TallyError):
    """复核包快照输入不合法：账户标识缺失/重复/非字符串，或投票权非非负整数。"""


class BallotValidationError(TallyError):
    """复核包选票不合法：投票者不在快照、同一账户多票、选项非字符串，
    或直接投票者已把票权委托给他人（直接投票与委托不得同时使用票权）。"""


class DelegationConflictError(TallyError):
    """复核包委托关系不合法：引用快照外账户、自委托、成环，
    或一个账户指向多个受托人。"""


class ClaimedResultValidationError(TallyError):
    """待核对结果不是对象、含公开结果字段之外的未知字段，
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

    choices = data["choices"]
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

    votes = data["votes"]
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

    snapshot = data["snapshot"]
    if not isinstance(snapshot, dict):
        raise InvalidInputError("field 'snapshot' must be an object")
    for account, weight in snapshot.items():
        if not isinstance(account, str):
            raise InvalidInputError("snapshot keys must be strings")
        if not _is_int(weight):
            raise InvalidInputError(f"snapshot weight for {account!r} must be an integer")
        if weight < 0:
            raise InvalidInputError(f"snapshot weight for {account!r} must be non-negative")

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

    # 为每个快照账户解析最终受托人；环在此处即被发现，即使该账户未投票。
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

    if not isinstance(result, dict):
        raise TallyVerificationError("result must be an object")
    for field in _RESULT_FIELDS:
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


def _validate_review_votes(
    votes: Any, snapshot: dict[str, int], delegations: dict[str, str]
) -> list[dict[str, str]]:
    """校验选票：对象列表、voter/choice 为字符串、voter 在快照内、
    同一账户至多一票，且直接投票与委托不得同时给同一账户使用票权。"""
    if not isinstance(votes, list):
        raise BallotValidationError("review package 'votes' must be a list")
    validated: list[dict[str, str]] = []
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
        if voter not in snapshot:
            raise BallotValidationError(
                f"voter not in snapshot: {voter!r}"
            )
        if voter in seen_voters:
            raise BallotValidationError(f"duplicate voter: {voter!r}")
        if voter in delegations:
            raise BallotValidationError(
                f"voter has delegated voting power to another account: {voter!r}"
            )
        seen_voters.add(voter)
        validated.append({"voter": voter, "choice": choice})
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
) -> dict:
    """生成一次提案的独立结果复核包（纯函数，只通过返回值交付）。

    输入为提案标识、快照区块、快照账户及其投票权、投票记录、委托关系与
    待核对结果。沿用公开计票语义：按委托链把未直接投票账户的票权聚合到
    唯一受托人，再按选项累计票权；直接投票与委托相互排斥，不设覆盖票。

    输出字段固定顺序：``proposal_id`` / ``snapshot_block`` /
    ``results_by_choice`` / ``direct_participated_weight`` /
    ``delegated_weight`` / ``non_participated_weight`` /
    ``effective_delegations`` / ``weight_conservation_holds`` /
    ``review_status`` / ``field_differences``。

    * 快照账户标识非字符串或不唯一、投票权非非负整数 ->
      ``SnapshotIntegrityError``；
    * 投票者不在快照、同一账户多票、已委托账户直接投票 ->
      ``BallotValidationError``；
    * 委托引用快照外账户、自委托、成环或一账户多受托人 ->
      ``DelegationConflictError``；
    * 待核对结果不是对象、含未知字段，或 results_by_choice 不是字符串键
      对象 -> ``ClaimedResultValidationError``；叶子值缺失、null、错误
      类型或取值错误不作为异常，按逐项差异返回；
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
    validated_votes = _validate_review_votes(
        votes, snapshot_weights, delegations
    )
    claimed = _validate_claimed_result(claimed_result, _CLAIMED_RESULT_FIELDS)

    # 选项只从投票记录派生并确定排序：计算结果不依赖待核对结果的任何字段。
    choices = sorted({vote["choice"] for vote in validated_votes})
    voting_trustees = {vote["voter"] for vote in validated_votes}

    results_by_choice: dict[str, int] = {choice: 0 for choice in choices}
    if choices:
        # 复用公开计票入口的委托传播与累计规则作为事实来源；
        # 入口前的校验已更严格，这里不应再产生既有异常，防御性翻译一次。
        context = {
            "proposal_id": proposal_id,
            "choices": choices,
            "votes": validated_votes,
            "snapshot": snapshot_weights,
            "delegations": delegations,
        }
        try:
            tally = _compute(context)
        except InvalidDelegationError as exc:
            raise DelegationConflictError(str(exc)) from exc
        except DelegationCycleError as exc:
            raise DelegationConflictError(str(exc)) from exc
        except InvalidVoteError as exc:
            raise BallotValidationError(str(exc)) from exc
        results_by_choice = dict(tally["per_choice"])

    # 票权三分类（按快照账户逐个归属，恰好覆盖总票权一次）：
    # 直接参与＝已投票受托人本人权重；受托＝经有效委托汇入已投票受托人的权重；
    # 未参与＝受托人未投票（含未委托未投票）账户的权重。
    direct_weight = 0
    delegated_weight = 0
    non_participated_weight = 0
    for account, weight in snapshot_weights.items():
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

    # 有效委托明细：实际把票权送达已投票受托人的委托，按委托人排序，
    # 受托人取委托链解析后的唯一最终受托人；权重求和等于 delegated_weight。
    effective_delegations = [
        {
            "delegator": account,
            "trustee": trustee_of[account],
            "weight": snapshot_weights[account],
        }
        for account in sorted(snapshot_weights)
        if trustee_of[account] != account
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
    field_differences = _review_field_differences(
        computed, claimed, _CLAIMED_RESULT_FIELDS
    )
    review_status = "matched" if not field_differences else "mismatched"

    return {
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


def main(argv: list[str] | None = None) -> int:
    """命令行入口：stdin JSON -> stdout JSON；成功退出 0，异常退出 2。"""
    try:
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8")
        raw = sys.stdin.read()
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            raise InvalidInputError("input is not valid JSON")
        result = tally_proposal(data)
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
