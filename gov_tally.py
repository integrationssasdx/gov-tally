"""Gov Tally: DAO 治理投票计票引擎。

提供：
- tally_proposal(input_data) -> dict：快照权重 + 委托链解析的计票。
- verify_tally(input_data, result) -> bool：复核计票结果，不一致抛 TallyVerificationError。
- 命令行：标准输入 JSON -> 标准输出 JSON；成功退出 0，异常退出 2。

仅使用 Python 标准库。
"""

from __future__ import annotations

import json
import sys


class TallyError(Exception):
    """所有计票相关异常的基类。"""


class InvalidInputError(TallyError):
    """输入字段缺失、类型错误、非整数、负权重、choices 为空或重复。"""


class InvalidDelegationError(TallyError):
    """delegations 中出现 snapshot 之外的账户。"""


class DelegationCycleError(TallyError):
    """委托链成环且无法到达最终受托人。"""


class InvalidVoteError(TallyError):
    """重复投票、委托他人者投票、voter 不在 snapshot 或 choice 不在 choices。"""


class TallyVerificationError(TallyError):
    """verify_tally 复核不一致。"""


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


def _is_int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _validate_input(data):
    """校验输入结构与类型，返回 (proposal_id, choices, votes, snapshot, delegations)。"""
    if not isinstance(data, dict):
        raise InvalidInputError("input must be a JSON object")
    for field in ("proposal_id", "choices", "votes", "snapshot", "delegations"):
        if field not in data:
            raise InvalidInputError("missing required field: %s" % field)

    proposal_id = data["proposal_id"]
    if not isinstance(proposal_id, (str, int)) or isinstance(proposal_id, bool):
        raise InvalidInputError("proposal_id must be a string or an integer")

    choices = data["choices"]
    if not isinstance(choices, list) or not choices:
        raise InvalidInputError("choices must be a non-empty list")
    for choice in choices:
        if not isinstance(choice, str) or not choice:
            raise InvalidInputError("each choice must be a non-empty string")
    if len(set(choices)) != len(choices):
        raise InvalidInputError("choices must not contain duplicates")

    snapshot = data["snapshot"]
    if not isinstance(snapshot, dict):
        raise InvalidInputError("snapshot must be an object mapping account to weight")
    for account, weight in snapshot.items():
        if not isinstance(account, str) or not account:
            raise InvalidInputError("snapshot accounts must be non-empty strings")
        if not _is_int(weight):
            raise InvalidInputError(
                "snapshot weight for account %s must be an integer" % account
            )
        if weight < 0:
            raise InvalidInputError(
                "snapshot weight for account %s must be non-negative" % account
            )

    delegations = data["delegations"]
    if not isinstance(delegations, dict):
        raise InvalidInputError("delegations must be an object mapping account to account")
    for source, target in delegations.items():
        if not isinstance(source, str) or not source:
            raise InvalidInputError("delegation source must be a non-empty string")
        if not isinstance(target, str) or not target:
            raise InvalidInputError("delegation target must be a non-empty string")

    votes = data["votes"]
    if not isinstance(votes, list):
        raise InvalidInputError("votes must be a list")
    for vote in votes:
        if not isinstance(vote, dict):
            raise InvalidInputError("each vote must be an object")
        if "voter" not in vote or "choice" not in vote:
            raise InvalidInputError("each vote must contain voter and choice")
        if not isinstance(vote["voter"], str) or not vote["voter"]:
            raise InvalidInputError("vote voter must be a non-empty string")
        if not isinstance(vote["choice"], str) or not vote["choice"]:
            raise InvalidInputError("vote choice must be a non-empty string")

    return proposal_id, choices, votes, snapshot, delegations


def _resolve_final_delegates(snapshot, delegations):
    """逐跳解析每个账户的最终受托人，返回 {account: final_delegate}。"""
    accounts = set(snapshot)
    for source, target in delegations.items():
        if source not in accounts:
            raise InvalidDelegationError(
                "delegation source not in snapshot: %s" % source
            )
        if target not in accounts:
            raise InvalidDelegationError(
                "delegation target not in snapshot: %s" % target
            )

    final = {}
    for account in accounts:
        seen = set()
        current = account
        while True:
            target = delegations.get(current)
            if target is None or target == current:
                final[account] = current
                break
            if current in seen:
                raise DelegationCycleError(
                    "delegation cycle detected at account: %s" % current
                )
            seen.add(current)
            current = target
    return final


def _validate_votes(votes, snapshot, choices, delegations):
    choice_set = set(choices)
    seen_voters = set()
    for vote in votes:
        voter = vote["voter"]
        choice = vote["choice"]
        if voter in seen_voters:
            raise InvalidVoteError("duplicate voter: %s" % voter)
        seen_voters.add(voter)
        if voter not in snapshot:
            raise InvalidVoteError("voter not in snapshot: %s" % voter)
        target = delegations.get(voter)
        if target is not None and target != voter:
            raise InvalidVoteError(
                "voter delegates to another account and cannot vote: %s" % voter
            )
        if choice not in choice_set:
            raise InvalidVoteError("vote choice not in choices: %s" % choice)


def tally_proposal(input_data):
    """计算提案计票结果。

    返回字典，字段：
    - proposal_id：原样透传。
    - per_choice：与 choices 同序的各选项计入权重（整数列表）。
    - effective_weights：最终受托人 -> 合并后的计入权重（本人 + 传递权重）。
    - uncounted_weight / counted_weight / snapshot_total_weight：整数汇总。
    - winners：唯一最大为 [choice]，并列最大为全部并列项（按 choices 顺序），
      无有效票为 []。
    - is_tie：并列最大为 true，其余为 false。
    """
    proposal_id, choices, votes, snapshot, delegations = _validate_input(input_data)
    final_delegate = _resolve_final_delegates(snapshot, delegations)
    _validate_votes(votes, snapshot, choices, delegations)

    # 最终受托人合并本人与传递权重；snapshot 权重只计入一次。
    effective_weights = {}
    for account, weight in snapshot.items():
        delegate = final_delegate[account]
        effective_weights[delegate] = effective_weights.get(delegate, 0) + weight

    per_choice_map = {choice: 0 for choice in choices}
    counted_weight = 0
    for vote in votes:
        voter = vote["voter"]
        # 已校验投票者不自外委托，故其最终受托人即本人。
        weight = effective_weights[final_delegate[voter]]
        per_choice_map[vote["choice"]] += weight
        counted_weight += weight

    snapshot_total_weight = sum(snapshot.values())
    per_choice = [per_choice_map[choice] for choice in choices]

    max_weight = max(per_choice)
    if max_weight <= 0:
        winners = []
        is_tie = False
    else:
        winners = [
            choice for choice, weight in zip(choices, per_choice) if weight == max_weight
        ]
        is_tie = len(winners) > 1

    return {
        "proposal_id": proposal_id,
        "per_choice": per_choice,
        "effective_weights": effective_weights,
        "uncounted_weight": snapshot_total_weight - counted_weight,
        "counted_weight": counted_weight,
        "snapshot_total_weight": snapshot_total_weight,
        "winners": winners,
        "is_tie": is_tie,
    }


def verify_tally(input_data, result):
    """复核 tally_proposal 的结果：字段、归属、守恒与汇总。

    一致返回 True，否则抛 TallyVerificationError。
    输入本身非法时抛对应的输入异常（InvalidInputError 等）。
    """
    expected = tally_proposal(input_data)

    if not isinstance(result, dict):
        raise TallyVerificationError("result must be a JSON object")
    missing = [field for field in _RESULT_FIELDS if field not in result]
    if missing:
        raise TallyVerificationError(
            "result missing fields: %s" % ", ".join(sorted(missing))
        )
    extra = [field for field in result if field not in _RESULT_FIELDS]
    if extra:
        raise TallyVerificationError(
            "result has unexpected fields: %s" % ", ".join(sorted(extra))
        )

    per_choice = result["per_choice"]
    effective_weights = result["effective_weights"]
    uncounted_weight = result["uncounted_weight"]
    counted_weight = result["counted_weight"]
    snapshot_total_weight = result["snapshot_total_weight"]
    winners = result["winners"]
    is_tie = result["is_tie"]

    if not isinstance(per_choice, list) or any(not _is_int(w) for w in per_choice):
        raise TallyVerificationError("per_choice must be a list of integers")
    if not isinstance(effective_weights, dict) or any(
        not isinstance(account, str) or not _is_int(weight)
        for account, weight in effective_weights.items()
    ):
        raise TallyVerificationError(
            "effective_weights must map account strings to integer weights"
        )
    for name, value in (
        ("uncounted_weight", uncounted_weight),
        ("counted_weight", counted_weight),
        ("snapshot_total_weight", snapshot_total_weight),
    ):
        if not _is_int(value):
            raise TallyVerificationError("%s must be an integer" % name)
    if not isinstance(winners, list) or any(not isinstance(c, str) for c in winners):
        raise TallyVerificationError("winners must be a list of choice strings")
    if not isinstance(is_tie, bool):
        raise TallyVerificationError("is_tie must be a boolean")

    # 守恒：计入 + 未计票 == 快照总权重；归属：有效权重之和 == 快照总权重。
    if counted_weight + uncounted_weight != snapshot_total_weight:
        raise TallyVerificationError(
            "conservation violated: counted_weight + uncounted_weight "
            "!= snapshot_total_weight"
        )
    if sum(effective_weights.values()) != snapshot_total_weight:
        raise TallyVerificationError(
            "attribution violated: sum of effective_weights != snapshot_total_weight"
        )
    # 汇总：各选项权重之和 == 计入权重。
    if sum(per_choice) != counted_weight:
        raise TallyVerificationError(
            "aggregation violated: sum of per_choice != counted_weight"
        )

    # 与独立重算结果逐字段比对（不只比较 winners）。
    for field in _RESULT_FIELDS:
        if result[field] != expected[field]:
            raise TallyVerificationError(
                "field %s does not match recomputed tally" % field
            )
    return True


def main(argv=None):
    try:
        raw = sys.stdin.read()
        try:
            input_data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise InvalidInputError("invalid JSON input: %s" % exc.msg) from None
        result = tally_proposal(input_data)
    except Exception as exc:
        # 异常退出 2；error 为异常类名，message 稳定、不含内存地址。
        error_name = (
            type(exc).__name__ if isinstance(exc, TallyError) else "UnexpectedError"
        )
        json.dump(
            {"error": error_name, "message": str(exc)},
            sys.stdout,
            ensure_ascii=False,
        )
        sys.stdout.write("\n")
        return 2
    json.dump(result, sys.stdout, ensure_ascii=False)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
