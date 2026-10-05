"""Review Pack — 结果复核包生成。

在 gov_tally 既有快照、委托与复核能力之外，提供独立的结果复核包装配。
本模块只做统一装配、边界校验、确定性汇总与逐项差异输出：

* 边界校验：快照账户标识唯一且票权为非负数值；选票只引用快照账户且
  同一账户至多一票；委托只在快照账户之间建立，不得出现环、自委托或
  一个账户指向多个受托人；直接投票与委托不得同时给同一账户使用票权。
  违反时按具体条件抛出 SnapshotIntegrityError、BallotValidationError
  或 DelegationConflictError，不继续计算。
* 确定性汇总：先按委托链把每个未直接投票账户的票权聚合到其唯一最终
  受托人，再按选项累计票权；所有求和按账户名排序遍历（浮点经
  math.fsum），重复输入产生完全相同的结果。
* 逐项复核：与调用方提供的待核对结果逐字段比对。一致时复核状态为
  ``matched``；任一公开结果字段不一致时为 ``mismatched``，并逐项返回
  字段名、计算值与待核对值，不把不一致当作异常。待核对结果含未知
  字段时抛 ClaimedResultValidationError；计算结果不依赖任何未知字段。

复核包只通过返回值交付，不隐式写入文件、数据库或日志。
"""

from __future__ import annotations

import math
from typing import Any

__all__ = [
    "ReviewPackError",
    "SnapshotIntegrityError",
    "BallotValidationError",
    "DelegationConflictError",
    "ClaimedResultValidationError",
    "build_review_pack",
]

# 待核对结果允许引用的公开结果字段，也是逐项比对的稳定顺序。
_CLAIMABLE_FIELDS = (
    "proposal_id",
    "snapshot_block",
    "results",
    "direct_weight",
    "delegated_weight",
    "non_participating_weight",
    "delegations",
    "weight_conserved",
)


class ReviewPackError(Exception):
    """复核包生成相关异常的基类。"""


class SnapshotIntegrityError(ReviewPackError):
    """快照标识或快照数据非法：proposal_id 非字符串、snapshot_block 非
    非负整数、snapshot 非对象、账户标识非字符串、票权非数值或为负。"""


class BallotValidationError(ReviewPackError):
    """选票非法：结构或类型错误、voter 不在快照中、同一账户重复投票。"""


class DelegationConflictError(ReviewPackError):
    """委托关系非法：结构或类型错误、委托方或受托方不在快照中、自委托、
    一个账户指向多个受托人、委托链成环，或同一账户既直接投票又委托他人。"""


class ClaimedResultValidationError(ReviewPackError):
    """待核对结果非法：不是对象，或含有公开结果字段之外的未知字段。"""


def _is_weight(value: Any) -> bool:
    """票权必须是非负数值；bool 是 int 的子类，一律拒绝。"""
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _sum_weights(values) -> Any:
    """确定性求和：全整数时用精确整数和，含浮点时用 math.fsum。"""
    values = list(values)
    if any(isinstance(value, float) for value in values):
        return math.fsum(values)
    return sum(values)


def _validate_identity(proposal_id: Any, snapshot_block: Any) -> None:
    if not isinstance(proposal_id, str):
        raise SnapshotIntegrityError("proposal_id must be a string")
    if isinstance(snapshot_block, bool) or not isinstance(snapshot_block, int):
        raise SnapshotIntegrityError("snapshot_block must be an integer")
    if snapshot_block < 0:
        raise SnapshotIntegrityError("snapshot_block must be non-negative")


def _validate_snapshot(snapshot: Any) -> dict:
    if not isinstance(snapshot, dict):
        raise SnapshotIntegrityError("snapshot must be an object")
    for account, weight in snapshot.items():
        if not isinstance(account, str):
            raise SnapshotIntegrityError("snapshot account identifiers must be strings")
        if not _is_weight(weight):
            raise SnapshotIntegrityError(
                f"snapshot weight for {account!r} must be a number"
            )
        if isinstance(weight, float) and not math.isfinite(weight):
            raise SnapshotIntegrityError(
                f"snapshot weight for {account!r} must be finite"
            )
        if weight < 0:
            raise SnapshotIntegrityError(
                f"snapshot weight for {account!r} must be non-negative"
            )
    return snapshot


def _validate_votes(snapshot: dict, votes: Any) -> set:
    """校验选票结构、引用与唯一性，返回投票账户集合。"""
    if not isinstance(votes, list):
        raise BallotValidationError("votes must be a list")
    voters: set[str] = set()
    for index, vote in enumerate(votes):
        if not isinstance(vote, dict):
            raise BallotValidationError(f"votes[{index}] must be an object")
        for key in ("voter", "choice"):
            if key not in vote:
                raise BallotValidationError(f"votes[{index}] missing field: {key!r}")
        voter = vote["voter"]
        choice = vote["choice"]
        if not isinstance(voter, str):
            raise BallotValidationError(f"votes[{index}].voter must be a string")
        if not isinstance(choice, str):
            raise BallotValidationError(f"votes[{index}].choice must be a string")
        if voter not in snapshot:
            raise BallotValidationError(f"voter not in snapshot: {voter!r}")
        if voter in voters:
            raise BallotValidationError(f"duplicate voter: {voter!r}")
        voters.add(voter)
    return voters


def _validate_delegations(snapshot: dict, delegations: Any) -> dict:
    """校验委托关系，返回 delegator -> 直接受托人 的映射。"""
    if not isinstance(delegations, list):
        raise DelegationConflictError("delegations must be a list")
    delegation_map: dict[str, str] = {}
    for index, entry in enumerate(delegations):
        if not isinstance(entry, dict):
            raise DelegationConflictError(f"delegations[{index}] must be an object")
        for key in ("delegator", "trustee"):
            if key not in entry:
                raise DelegationConflictError(
                    f"delegations[{index}] missing field: {key!r}"
                )
        delegator = entry["delegator"]
        trustee = entry["trustee"]
        if not isinstance(delegator, str) or not isinstance(trustee, str):
            raise DelegationConflictError(
                f"delegations[{index}] accounts must be strings"
            )
        if delegator not in snapshot:
            raise DelegationConflictError(
                f"delegator not in snapshot: {delegator!r}"
            )
        if trustee not in snapshot:
            raise DelegationConflictError(
                f"trustee not in snapshot: {trustee!r}"
            )
        if delegator == trustee:
            raise DelegationConflictError(
                f"self-delegation is not allowed: {delegator!r}"
            )
        if delegator in delegation_map:
            raise DelegationConflictError(
                f"account delegates to multiple trustees: {delegator!r}"
            )
        delegation_map[delegator] = trustee
    return delegation_map


def _resolve_trustee(account: str, delegation_map: dict) -> str:
    """逐跳解析最终受托人；链上进入已访问节点即成环。"""
    visited: set[str] = set()
    current = account
    while current in delegation_map:
        if current in visited:
            raise DelegationConflictError(
                f"delegation cycle detected at account: {current!r}"
            )
        visited.add(current)
        current = delegation_map[current]
    return current


def _validate_claimed(claimed_result: Any) -> dict:
    if not isinstance(claimed_result, dict):
        raise ClaimedResultValidationError("claimed_result must be an object")
    for field in claimed_result:
        if field not in _CLAIMABLE_FIELDS:
            raise ClaimedResultValidationError(
                f"claimed_result has unknown field: {field!r}"
            )
    return claimed_result


def build_review_pack(
    proposal_id: Any,
    snapshot_block: Any,
    snapshot: Any,
    votes: Any,
    delegations: Any,
    claimed_result: Any,
) -> dict:
    """装配一次提案的结果复核包，返回可重复验证的字典。

    参数：

    * ``proposal_id``：提案标识（字符串）；
    * ``snapshot_block``：快照区块（非负整数，布尔值不算整数）；
    * ``snapshot``：账户标识 -> 非负数值票权；
    * ``votes``：选票列表，每项含 ``voter`` 与 ``choice``（均为字符串）；
    * ``delegations``：委托列表，每项含 ``delegator`` 与 ``trustee``；
    * ``claimed_result``：待核对结果，只允许引用公开结果字段。

    返回字典字段顺序稳定：``proposal_id``、``snapshot_block``、
    ``results``（选项 -> 票权，按选项名排序）、``direct_weight``（直接
    参与票权）、``delegated_weight``（经受托计入的票权）、
    ``non_participating_weight``（未参与票权）、``delegations``（有效
    委托明细，按 delegator 排序）、``weight_conserved``（参与票权与
    未参与票权之和等于快照总票权）、``review``（复核状态与逐项差异）。
    """
    _validate_identity(proposal_id, snapshot_block)
    snapshot = _validate_snapshot(snapshot)
    voters = _validate_votes(snapshot, votes)
    delegation_map = _validate_delegations(snapshot, delegations)

    # 同一账户不得既直接投票又委托他人，否则票权被使用两次。
    for account in sorted(voters & delegation_map.keys()):
        raise DelegationConflictError(
            f"account both votes and delegates: {account!r}"
        )

    # 为每个快照账户解析最终受托人；环在此处即被发现，即使该账户未投票。
    trustee_of = {
        account: _resolve_trustee(account, delegation_map) for account in snapshot
    }

    # 每个未直接投票账户的票权按链聚合到其唯一最终受托人；
    # 按账户名排序遍历，保证浮点求和顺序确定。
    basin_weights: dict[str, list] = {}
    for account in sorted(snapshot):
        if account in voters:
            continue
        basin_weights.setdefault(trustee_of[account], []).append(snapshot[account])

    # 受托人投票时，其票权为本人票权加上全部汇入的受托票权；
    # 受托人未投票时，汇入票权留在未参与票权中，不分配给任何选项。
    choice_weights: dict[str, list] = {}
    direct_parts: list = []
    delegated_parts: list = []
    for vote in votes:
        voter = vote["voter"]
        incoming = basin_weights.get(voter, [])
        choice_weights.setdefault(vote["choice"], []).append(
            _sum_weights([snapshot[voter], *incoming])
        )
        direct_parts.append(snapshot[voter])
        delegated_parts.extend(incoming)

    results = {
        choice: _sum_weights(weights)
        for choice, weights in sorted(choice_weights.items())
    }
    direct_weight = _sum_weights(direct_parts)
    delegated_weight = _sum_weights(delegated_parts)

    # 未委托且未投票的账户、以及委托给未投票受托人的账户，均保留为未参与票权。
    counted_accounts = set(voters)
    for account in snapshot:
        if account not in voters and trustee_of[account] in voters:
            counted_accounts.add(account)
    non_participating_weight = _sum_weights(
        snapshot[account]
        for account in sorted(snapshot)
        if account not in counted_accounts
    )
    total_weight = _sum_weights(snapshot[account] for account in sorted(snapshot))

    # 有效委托明细：委托链终点受托人实际投票的委托，按 delegator 排序。
    effective_delegations = [
        {
            "delegator": account,
            "trustee": trustee_of[account],
            "weight": snapshot[account],
        }
        for account in sorted(snapshot)
        if trustee_of[account] != account and trustee_of[account] in voters
    ]

    weight_conserved = (
        _sum_weights([direct_weight, delegated_weight, non_participating_weight])
        == total_weight
    )

    computed = {
        "proposal_id": proposal_id,
        "snapshot_block": snapshot_block,
        "results": results,
        "direct_weight": direct_weight,
        "delegated_weight": delegated_weight,
        "non_participating_weight": non_participating_weight,
        "delegations": effective_delegations,
        "weight_conserved": weight_conserved,
    }

    # 待核对结果只影响复核状态与差异，不参与计算；未知字段直接报错。
    claimed_result = _validate_claimed(claimed_result)
    mismatches = [
        {"field": field, "computed": computed[field], "claimed": claimed_result[field]}
        for field in _CLAIMABLE_FIELDS
        if field in claimed_result and claimed_result[field] != computed[field]
    ]
    status = "matched" if not mismatches else "mismatched"

    return {
        "proposal_id": proposal_id,
        "snapshot_block": snapshot_block,
        "results": results,
        "direct_weight": direct_weight,
        "delegated_weight": delegated_weight,
        "non_participating_weight": non_participating_weight,
        "delegations": effective_delegations,
        "weight_conserved": weight_conserved,
        "review": {"status": status, "mismatches": mismatches},
    }
