"""Gov Tally — DAO 治理投票计票引擎。

仅使用 Python 3.12 标准库，实现：

* 快照权重（snapshot）与逐跳委托（delegations）解析；
* ``tally_proposal``：合并最终受托人权重并计票；
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


class TallyError(Exception):
    """所有计票异常的基类。"""


class InvalidInputError(TallyError):
    """输入缺少字段、类型错误、取值非法（如负权重、choices 空或重复）。"""


class InvalidDelegationError(TallyError):
    """delegations 引用了 snapshot 之外的账户。"""


class DelegationCycleError(TallyError):
    """委托链成环且无法到达最终受托人。"""


class InvalidVoteError(TallyError):
    """选票非法：重复 voter、已委托他人者投票、voter 不在 snapshot、choice 不存在。"""


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

    return proposal_id, choices, votes, snapshot, delegations


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


def _compute(data: Any) -> dict:
    proposal_id, choices, votes, snapshot, delegations = _validate_input(data)
    _check_delegation_accounts(snapshot, delegations)

    # 为每个快照账户解析最终受托人；环在此处即被发现，即使该账户未投票。
    trustee_of = {
        account: _resolve_trustee(account, delegations) for account in snapshot
    }

    # 每个账户的快照权重只并入其最终受托人一次，不重复计数。
    bucket = {account: 0 for account in snapshot}
    for account, weight in snapshot.items():
        bucket[trustee_of[account]] += weight

    choice_set = set(choices)
    per_choice = {choice: 0 for choice in choices}
    effective_weights: dict[str, int] = {}
    seen_voters: set[str] = set()

    for index, vote in enumerate(votes):
        voter = vote["voter"]
        choice = vote["choice"]

        if voter not in snapshot:
            raise InvalidVoteError(f"voter not in snapshot: {voter!r}")
        if choice not in choice_set:
            raise InvalidVoteError(f"choice not in choices: {choice!r}")
        if trustee_of[voter] != voter:
            raise InvalidVoteError(
                f"voter has delegated to another account: {voter!r}"
            )
        if voter in seen_voters:
            raise InvalidVoteError(f"duplicate voter: {voter!r}")
        seen_voters.add(voter)

        weight = bucket[voter]
        per_choice[choice] += weight
        effective_weights[voter] = weight

    snapshot_total_weight = sum(snapshot.values())
    counted_weight = sum(per_choice.values())
    uncounted_weight = snapshot_total_weight - counted_weight
    winners, is_tie = _winners(choices, per_choice)

    return {
        "proposal_id": proposal_id,
        "per_choice": per_choice,
        "effective_weights": effective_weights,
        "uncounted_weight": uncounted_weight,
        "counted_weight": counted_weight,
        "snapshot_total_weight": snapshot_total_weight,
        "winners": winners,
        "is_tie": is_tie,
    }


def tally_proposal(input_data: dict) -> dict:
    """对一次提案计票，返回结果字典；非法输入抛出对应异常。"""
    return _compute(input_data)


def verify_tally(input_data: dict, result: Any) -> bool:
    """独立复核计票结果。

    检查字段完整性、choice 顺序、权重归属（受托人合并与选票归属）、
    权重守恒（counted + uncounted == snapshot_total）与汇总一致性。
    全部一致返回 True，否则抛 TallyVerificationError。
    """
    expected = _compute(input_data)

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

    return True


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
