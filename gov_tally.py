"""Gov Tally — DAO 治理投票计票引擎。

仅使用 Python 3.12 标准库，实现：

* 快照权重（snapshot）与逐跳委托（delegations）解析；
* ``tally_proposal``：合并最终受托人权重并计票，支持提案级委托覆盖
  （``delegation_override``：已委托者亲自投出本人权重，且不重复计入受托人）；
* 可选权重来源追踪（``include_provenance``）：结果增加 ``weight_provenance``，
  逐票列出正权重来源账户及其 snapshot 权重；
* 可选门槛判定（``decision_rules``）：按原规则计票后追加
  ``quorum_met`` / ``approval_met`` / ``decision``；
* ``verify_tally``：独立复核字段、归属、守恒与汇总；
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
    "tally_proposal",
    "verify_tally",
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

_DECISION_VALUES = ("no_quorum", "approved", "rejected")


class TallyError(Exception):
    """所有计票异常的基类。"""


class InvalidInputError(TallyError):
    """输入缺少字段、类型错误、取值非法（如负权重、choices 空或重复、
    decision_rules 结构或取值非法）。"""


class InvalidDelegationError(TallyError):
    """delegations 引用了 snapshot 之外的账户。"""


class DelegationCycleError(TallyError):
    """委托链成环且无法到达最终受托人。"""


class InvalidVoteError(TallyError):
    """选票非法：重复 voter、普通票来自已委托他人者、覆盖票来自未委托他人者、
    voter 不在 snapshot、choice 不存在。"""


class TallyVerificationError(TallyError):
    """计票结果未通过独立复核。"""


def _is_int(value: Any) -> bool:
    """bool 在 Python 中是 int 的子类，计票场景一律拒绝。"""
    return isinstance(value, int) and not isinstance(value, bool)


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

    decision_rules = _validate_decision_rules(data, choices)

    return proposal_id, choices, votes, snapshot, delegations, decision_rules


def _validate_decision_rules(data: dict, choices: list) -> dict | None:
    """校验可选的 decision_rules；缺省返回 None。

    配置必须恰好含 approval_choices（choices 的非空无重复子集）、
    min_counted_weight（非负整数）、approval_basis_points（1..10000 整数）；
    布尔值不算整数。任何非法均抛 InvalidInputError。
    """
    if "decision_rules" not in data:
        return None
    rules = data["decision_rules"]
    if not isinstance(rules, dict):
        raise InvalidInputError("field 'decision_rules' must be an object")

    required = ("approval_choices", "min_counted_weight", "approval_basis_points")
    for field in required:
        if field not in rules:
            raise InvalidInputError(f"decision_rules missing field: {field!r}")
    for key in rules:
        if key not in required:
            raise InvalidInputError(f"decision_rules unexpected field: {key!r}")

    approval_choices = rules["approval_choices"]
    if not isinstance(approval_choices, list):
        raise InvalidInputError("decision_rules.approval_choices must be a list")
    if len(approval_choices) == 0:
        raise InvalidInputError("decision_rules.approval_choices must not be empty")
    choice_set = set(choices)
    seen_approval: set[str] = set()
    for choice in approval_choices:
        if not isinstance(choice, str):
            raise InvalidInputError("every approval choice must be a string")
        if choice in seen_approval:
            raise InvalidInputError(f"duplicate approval choice: {choice!r}")
        if choice not in choice_set:
            raise InvalidInputError(f"approval choice not in choices: {choice!r}")
        seen_approval.add(choice)

    min_counted_weight = rules["min_counted_weight"]
    if not _is_int(min_counted_weight) or min_counted_weight < 0:
        raise InvalidInputError(
            "decision_rules.min_counted_weight must be a non-negative integer"
        )

    approval_basis_points = rules["approval_basis_points"]
    if not _is_int(approval_basis_points) or not 1 <= approval_basis_points <= 10000:
        raise InvalidInputError(
            "decision_rules.approval_basis_points must be an integer in [1, 10000]"
        )

    return {
        "approval_choices": approval_choices,
        "min_counted_weight": min_counted_weight,
        "approval_basis_points": approval_basis_points,
    }


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
    proposal_id, choices, votes, snapshot, delegations, decision_rules = (
        _validate_input(data)
    )
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
        result.update(_decide(decision_rules, per_choice, counted_weight))
    return result


def _decide(decision_rules: dict, per_choice: dict, counted_weight: int) -> dict:
    """门槛判定：先按原规则计票，再据 decision_rules 追加判定字段。

    * quorum_met：counted_weight 大于零且不少于 min_counted_weight；
    * approval_met：approval_choices 的 per_choice 权重和乘 10000
      不少于 counted_weight 乘 approval_basis_points；
    * decision：quorum_met 为假 -> no_quorum；两个判定皆真 -> approved；
      其余 -> rejected。
    """
    approval_weight = sum(
        per_choice[choice] for choice in decision_rules["approval_choices"]
    )
    quorum_met = (
        counted_weight > 0
        and counted_weight >= decision_rules["min_counted_weight"]
    )
    approval_met = (
        approval_weight * 10000
        >= counted_weight * decision_rules["approval_basis_points"]
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
    输入含 ``decision_rules`` 时结果在既有字段后追加
    ``quorum_met`` / ``approval_met`` / ``decision``；不含时输出形状不变。
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
    输入含 ``decision_rules`` 时另复核 ``quorum_met`` / ``approval_met`` /
    ``decision``：必须依次位于既有字段之后，类型、取值与决策均与独立重算一致。
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

    if "decision" in expected:
        _verify_decision(result, expected)

    return True


def _verify_decision(result: dict, expected: dict) -> None:
    """复核 decision_rules 衍生字段：依次位于既有字段之后，类型与取值一致。"""
    if list(result.keys())[-len(_DECISION_FIELDS):] != list(_DECISION_FIELDS):
        raise TallyVerificationError(
            "result must end with quorum_met, approval_met, decision in that order"
        )
    for field in ("quorum_met", "approval_met"):
        value = result[field]
        if not isinstance(value, bool):
            raise TallyVerificationError(f"{field} must be a boolean")
        if value != expected[field]:
            raise TallyVerificationError(f"{field} mismatch")
    decision = result["decision"]
    if not isinstance(decision, str) or decision not in _DECISION_VALUES:
        raise TallyVerificationError(
            "decision must be one of: no_quorum, approved, rejected"
        )
    if decision != expected["decision"]:
        raise TallyVerificationError("decision mismatch")


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
