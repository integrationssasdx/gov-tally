"""Gov Tally 测试 —— 标准库 unittest。"""

import json
import subprocess
import sys
import unittest
from pathlib import Path

import gov_tally as gt

MODULE = Path(__file__).resolve().parent / "gov_tally.py"


def base_input(**overrides):
    data = {
        "proposal_id": "p1",
        "choices": ["yes", "no"],
        "votes": [],
        "snapshot": {"a": 10, "b": 5},
        "delegations": {},
    }
    data.update(overrides)
    return data


class TallyTests(unittest.TestCase):
    def test_basic_direct_votes(self):
        data = base_input(votes=[{"voter": "a", "choice": "yes"}, {"voter": "b", "choice": "no"}])
        result = gt.tally_proposal(data)
        self.assertEqual(result["proposal_id"], "p1")
        self.assertEqual(list(result["per_choice"].keys()), ["yes", "no"])
        self.assertEqual(result["per_choice"], {"yes": 10, "no": 5})
        self.assertEqual(result["effective_weights"], {"a": 10, "b": 5})
        self.assertEqual(result["counted_weight"], 15)
        self.assertEqual(result["uncounted_weight"], 0)
        self.assertEqual(result["snapshot_total_weight"], 15)
        self.assertEqual(result["winners"], ["yes"])
        self.assertFalse(result["is_tie"])

    def test_delegation_chain_merges_weights(self):
        data = base_input(
            snapshot={"a": 2, "b": 3, "c": 4},
            delegations={"a": "b", "b": "c"},
            votes=[{"voter": "c", "choice": "yes"}],
        )
        result = gt.tally_proposal(data)
        # a -> b -> c：c 合并本人 4 + b 3 + a 2，只计一次。
        self.assertEqual(result["effective_weights"], {"c": 9})
        self.assertEqual(result["per_choice"], {"yes": 9, "no": 0})
        self.assertEqual(result["counted_weight"], 9)
        self.assertEqual(result["uncounted_weight"], 0)

    def test_intermediate_delegator_cannot_vote(self):
        data = base_input(
            snapshot={"a": 2, "b": 3, "c": 4},
            delegations={"a": "b", "b": "c"},
            votes=[{"voter": "b", "choice": "yes"}],
        )
        with self.assertRaises(gt.InvalidVoteError):
            gt.tally_proposal(data)
        data["votes"] = [{"voter": "a", "choice": "yes"}]
        with self.assertRaises(gt.InvalidVoteError):
            gt.tally_proposal(data)

    def test_self_delegation_allows_voting(self):
        data = base_input(delegations={"a": "a"}, votes=[{"voter": "a", "choice": "yes"}])
        result = gt.tally_proposal(data)
        self.assertEqual(result["per_choice"], {"yes": 10, "no": 0})
        self.assertEqual(result["winners"], ["yes"])

    def test_abstention_goes_uncounted(self):
        data = base_input(
            snapshot={"a": 2, "b": 3, "c": 4},
            delegations={"a": "b", "b": "c"},
        )
        result = gt.tally_proposal(data)
        self.assertEqual(result["counted_weight"], 0)
        self.assertEqual(result["uncounted_weight"], 9)
        self.assertEqual(result["snapshot_total_weight"], 9)
        self.assertEqual(result["winners"], [])
        self.assertFalse(result["is_tie"])

    def test_tie_lists_all_leaders_in_choice_order(self):
        data = base_input(votes=[{"voter": "a", "choice": "yes"}, {"voter": "b", "choice": "no"}])
        result = gt.tally_proposal(data)
        self.assertEqual(result["per_choice"], {"yes": 10, "no": 5})
        data2 = base_input(
            snapshot={"a": 5, "b": 5},
            votes=[{"voter": "a", "choice": "yes"}, {"voter": "b", "choice": "no"}],
        )
        result2 = gt.tally_proposal(data2)
        self.assertTrue(result2["is_tie"])
        self.assertEqual(result2["winners"], ["yes", "no"])

    def test_three_way_tie_preserves_choice_order(self):
        data = base_input(
            choices=["c", "a", "b"],
            snapshot={"x": 1, "y": 1, "z": 1, "q": 7},
            votes=[
                {"voter": "x", "choice": "c"},
                {"voter": "y", "choice": "a"},
                {"voter": "z", "choice": "b"},
            ],
        )
        result = gt.tally_proposal(data)
        self.assertEqual(list(result["per_choice"].keys()), ["c", "a", "b"])
        self.assertTrue(result["is_tie"])
        self.assertEqual(result["winners"], ["c", "a", "b"])
        # q 未投票，权重 7 归入未计票。
        self.assertEqual(result["uncounted_weight"], 7)
        self.assertEqual(result["counted_weight"], 3)

    def test_zero_weight_vote_is_not_effective(self):
        data = base_input(snapshot={"a": 0, "b": 5}, votes=[{"voter": "a", "choice": "yes"}])
        result = gt.tally_proposal(data)
        self.assertEqual(result["counted_weight"], 0)
        self.assertEqual(result["uncounted_weight"], 5)
        self.assertEqual(result["winners"], [])
        self.assertFalse(result["is_tie"])

    def test_zero_snapshot_total(self):
        data = base_input(snapshot={"a": 0, "b": 0})
        result = gt.tally_proposal(data)
        self.assertEqual(result["snapshot_total_weight"], 0)
        self.assertEqual(result["winners"], [])
        self.assertFalse(result["is_tie"])

    def test_delegated_weight_not_double_counted(self):
        data = base_input(
            snapshot={"a": 3, "b": 2},
            delegations={"a": "b"},
            votes=[{"voter": "b", "choice": "no"}],
        )
        result = gt.tally_proposal(data)
        self.assertEqual(result["effective_weights"], {"b": 5})
        self.assertEqual(result["per_choice"], {"yes": 0, "no": 5})
        self.assertEqual(result["counted_weight"], 5)
        self.assertEqual(result["uncounted_weight"], 0)

    def test_nonvoting_trustee_weight_all_uncounted(self):
        data = base_input(snapshot={"a": 3, "b": 2}, delegations={"a": "b"})
        result = gt.tally_proposal(data)
        self.assertEqual(result["uncounted_weight"], 5)
        self.assertEqual(result["counted_weight"], 0)

    def test_delegation_chain_terminating_at_voter(self):
        data = base_input(
            snapshot={"a": 1, "b": 2, "c": 4},
            delegations={"a": "b"},
            votes=[{"voter": "b", "choice": "yes"}, {"voter": "c", "choice": "no"}],
        )
        result = gt.tally_proposal(data)
        self.assertEqual(result["effective_weights"], {"b": 3, "c": 4})
        self.assertEqual(result["per_choice"], {"yes": 3, "no": 4})
        self.assertEqual(result["winners"], ["no"])


class DelegationOverrideTests(unittest.TestCase):
    def test_override_withdraws_own_weight_from_trustee(self):
        # a -> b -> c：a 覆盖票只计本人 2；c 合并到剩余 7（c 4 + b 3）。
        data = base_input(
            snapshot={"a": 2, "b": 3, "c": 4},
            delegations={"a": "b", "b": "c"},
            votes=[
                {"voter": "a", "choice": "no", "delegation_override": True},
                {"voter": "c", "choice": "yes"},
            ],
        )
        result = gt.tally_proposal(data)
        self.assertEqual(result["effective_weights"], {"a": 2, "c": 7})
        self.assertEqual(result["per_choice"], {"yes": 7, "no": 2})
        self.assertEqual(result["counted_weight"], 9)
        self.assertEqual(result["uncounted_weight"], 0)
        self.assertEqual(result["snapshot_total_weight"], 9)
        self.assertEqual(result["winners"], ["yes"])

    def test_override_effective_weights_follow_votes_order(self):
        # 受托人票在前、覆盖票在后：effective_weights 严格按 votes 顺序。
        data = base_input(
            snapshot={"a": 2, "b": 3, "c": 4},
            delegations={"a": "b", "b": "c"},
            votes=[
                {"voter": "c", "choice": "yes"},
                {"voter": "a", "choice": "no", "delegation_override": True},
            ],
        )
        result = gt.tally_proposal(data)
        self.assertEqual(list(result["effective_weights"].items()), [("c", 7), ("a", 2)])
        self.assertEqual(result["per_choice"], {"yes": 7, "no": 2})

    def test_intermediate_override_keeps_upstream_weight_with_trustee(self):
        # a -> b -> c：b 覆盖投本人 3；a 的 2 仍逐跳归入 c，c 得 4 + 2。
        data = base_input(
            snapshot={"a": 2, "b": 3, "c": 4},
            delegations={"a": "b", "b": "c"},
            votes=[
                {"voter": "b", "choice": "no", "delegation_override": True},
                {"voter": "c", "choice": "yes"},
            ],
        )
        result = gt.tally_proposal(data)
        self.assertEqual(result["effective_weights"], {"b": 3, "c": 6})
        self.assertEqual(result["per_choice"], {"yes": 6, "no": 3})
        self.assertEqual(result["counted_weight"], 9)
        self.assertEqual(result["uncounted_weight"], 0)

    def test_override_without_trustee_vote_leaves_remainder_uncounted(self):
        # a 覆盖投出本人 2；受托人 b 未投票，b 本人 3 归入未计票。
        data = base_input(
            snapshot={"a": 2, "b": 3},
            delegations={"a": "b"},
            votes=[{"voter": "a", "choice": "yes", "delegation_override": True}],
        )
        result = gt.tally_proposal(data)
        self.assertEqual(result["effective_weights"], {"a": 2})
        self.assertEqual(result["per_choice"], {"yes": 2, "no": 0})
        self.assertEqual(result["counted_weight"], 2)
        self.assertEqual(result["uncounted_weight"], 3)

    def test_multiple_overrides_same_trustee(self):
        data = base_input(
            snapshot={"a": 2, "c": 4, "d": 5},
            delegations={"a": "d", "c": "d"},
            votes=[
                {"voter": "a", "choice": "yes", "delegation_override": True},
                {"voter": "c", "choice": "no", "delegation_override": True},
                {"voter": "d", "choice": "yes"},
            ],
        )
        result = gt.tally_proposal(data)
        self.assertEqual(list(result["effective_weights"].items()), [("a", 2), ("c", 4), ("d", 5)])
        self.assertEqual(result["per_choice"], {"yes": 7, "no": 4})
        self.assertEqual(result["counted_weight"], 11)
        self.assertEqual(result["uncounted_weight"], 0)

    def test_override_zero_weight(self):
        data = base_input(
            snapshot={"a": 0, "b": 5},
            delegations={"a": "b"},
            votes=[
                {"voter": "a", "choice": "yes", "delegation_override": True},
                {"voter": "b", "choice": "no"},
            ],
        )
        result = gt.tally_proposal(data)
        self.assertEqual(result["effective_weights"], {"a": 0, "b": 5})
        self.assertEqual(result["per_choice"], {"yes": 0, "no": 5})
        self.assertEqual(result["winners"], ["no"])

    def test_both_chain_nodes_override_trustee_keeps_own(self):
        # a -> b -> c：a 与 b 都覆盖，c 只剩本人 4；2 和 3 分别归 a、b。
        data = base_input(
            snapshot={"a": 2, "b": 3, "c": 4},
            delegations={"a": "b", "b": "c"},
            votes=[
                {"voter": "a", "choice": "yes", "delegation_override": True},
                {"voter": "b", "choice": "yes", "delegation_override": True},
                {"voter": "c", "choice": "no"},
            ],
        )
        result = gt.tally_proposal(data)
        self.assertEqual(result["effective_weights"], {"a": 2, "b": 3, "c": 4})
        self.assertEqual(result["per_choice"], {"yes": 5, "no": 4})
        self.assertEqual(result["counted_weight"], 9)
        self.assertEqual(result["uncounted_weight"], 0)

    def test_override_same_choice_as_trustee(self):
        data = base_input(
            snapshot={"a": 2, "b": 3},
            delegations={"a": "b"},
            votes=[
                {"voter": "a", "choice": "yes", "delegation_override": True},
                {"voter": "b", "choice": "yes"},
            ],
        )
        result = gt.tally_proposal(data)
        self.assertEqual(result["effective_weights"], {"a": 2, "b": 3})
        self.assertEqual(result["per_choice"], {"yes": 5, "no": 0})
        self.assertFalse(result["is_tie"])

    def test_override_false_behaves_like_omitted(self):
        # 显式 false 不改变普通票规则：已委托他人者仍不能投普通票。
        data = base_input(
            delegations={"a": "b"},
            votes=[{"voter": "a", "choice": "yes", "delegation_override": False}],
        )
        with self.assertRaises(gt.InvalidVoteError):
            gt.tally_proposal(data)
        # 受托人带 false 正常投票，合并权重不变。
        data2 = base_input(
            snapshot={"a": 2, "b": 3},
            delegations={"a": "b"},
            votes=[{"voter": "b", "choice": "no", "delegation_override": False}],
        )
        result = gt.tally_proposal(data2)
        self.assertEqual(result["effective_weights"], {"b": 5})

    def test_override_non_boolean_is_invalid_input(self):
        for value in ("true", 1, 0, None, "yes", [], {}):
            with self.subTest(value=value):
                data = base_input(
                    votes=[{"voter": "a", "choice": "yes", "delegation_override": value}]
                )
                with self.assertRaises(gt.InvalidInputError):
                    gt.tally_proposal(data)


class InvalidOverrideVoteTests(unittest.TestCase):
    def test_override_voter_not_in_snapshot(self):
        data = base_input(votes=[
            {"voter": "z", "choice": "yes", "delegation_override": True}
        ])
        with self.assertRaises(gt.InvalidVoteError):
            gt.tally_proposal(data)

    def test_override_without_delegation(self):
        data = base_input(votes=[
            {"voter": "a", "choice": "yes", "delegation_override": True}
        ])
        with self.assertRaises(gt.InvalidVoteError):
            gt.tally_proposal(data)

    def test_override_with_self_delegation(self):
        data = base_input(
            delegations={"a": "a"},
            votes=[{"voter": "a", "choice": "yes", "delegation_override": True}],
        )
        with self.assertRaises(gt.InvalidVoteError):
            gt.tally_proposal(data)

    def test_override_duplicate_voter(self):
        data = base_input(
            delegations={"a": "b"},
            votes=[
                {"voter": "a", "choice": "yes", "delegation_override": True},
                {"voter": "a", "choice": "no", "delegation_override": True},
            ],
        )
        with self.assertRaises(gt.InvalidVoteError):
            gt.tally_proposal(data)

    def test_override_choice_out_of_range(self):
        data = base_input(
            delegations={"a": "b"},
            votes=[{"voter": "a", "choice": "maybe", "delegation_override": True}],
        )
        with self.assertRaises(gt.InvalidVoteError):
            gt.tally_proposal(data)

class CycleTests(unittest.TestCase):
    def test_direct_cycle(self):
        data = base_input(snapshot={"a": 1, "b": 2}, delegations={"a": "b", "b": "a"})
        with self.assertRaises(gt.DelegationCycleError):
            gt.tally_proposal(data)

    def test_three_node_cycle(self):
        data = base_input(
            snapshot={"a": 1, "b": 2, "c": 3},
            delegations={"a": "b", "b": "c", "c": "a"},
        )
        with self.assertRaises(gt.DelegationCycleError):
            gt.tally_proposal(data)

    def test_cycle_with_tail_is_still_cycle(self):
        # d -> a -> b -> c -> a：d 能走到环上但环无最终受托人。
        data = base_input(
            snapshot={"a": 1, "b": 2, "c": 3, "d": 4},
            delegations={"d": "a", "a": "b", "b": "c", "c": "a"},
        )
        with self.assertRaises(gt.DelegationCycleError):
            gt.tally_proposal(data)

    def test_self_loop_is_terminal_not_cycle(self):
        data = base_input(
            snapshot={"a": 1, "b": 2},
            delegations={"a": "a"},
            votes=[{"voter": "a", "choice": "yes"}, {"voter": "b", "choice": "no"}],
        )
        result = gt.tally_proposal(data)
        self.assertEqual(result["per_choice"], {"yes": 1, "no": 2})


class InvalidInputTests(unittest.TestCase):
    def assert_invalid(self, data):
        with self.assertRaises(gt.InvalidInputError):
            gt.tally_proposal(data)

    def test_missing_fields(self):
        for field in ("proposal_id", "choices", "votes", "snapshot", "delegations"):
            data = base_input()
            del data[field]
            self.assert_invalid(data)

    def test_bad_types(self):
        self.assert_invalid(base_input(proposal_id=7))
        self.assert_invalid(base_input(choices="yes"))
        self.assert_invalid(base_input(votes={}))
        self.assert_invalid(base_input(snapshot=[]))
        self.assert_invalid(base_input(delegations=[]))
        self.assert_invalid(base_input(choices=["yes", 3]))
        self.assert_invalid(base_input(votes=[{"voter": "a"}]))
        self.assert_invalid(base_input(votes=[{"choice": "yes"}]))
        self.assert_invalid(base_input(votes=[{"voter": 9, "choice": "yes"}]))

    def test_empty_or_duplicate_choices(self):
        self.assert_invalid(base_input(choices=[]))
        self.assert_invalid(base_input(choices=["yes", "yes"]))

    def test_non_integer_weight(self):
        self.assert_invalid(base_input(snapshot={"a": 1.5}))
        self.assert_invalid(base_input(snapshot={"a": "10"}))
        self.assert_invalid(base_input(snapshot={"a": None}))

    def test_bool_weight_rejected(self):
        self.assert_invalid(base_input(snapshot={"a": True}))

    def test_negative_weight(self):
        self.assert_invalid(base_input(snapshot={"a": -1}))

    def test_input_not_object(self):
        self.assert_invalid([1, 2, 3])
        self.assert_invalid("nope")


class InvalidDelegationTests(unittest.TestCase):
    def test_delegator_outside_snapshot(self):
        data = base_input(delegations={"x": "a"})
        with self.assertRaises(gt.InvalidDelegationError):
            gt.tally_proposal(data)

    def test_target_outside_snapshot(self):
        data = base_input(delegations={"a": "x"})
        with self.assertRaises(gt.InvalidDelegationError):
            gt.tally_proposal(data)


class InvalidVoteTests(unittest.TestCase):
    def test_duplicate_voter(self):
        data = base_input(votes=[
            {"voter": "a", "choice": "yes"},
            {"voter": "a", "choice": "no"},
        ])
        with self.assertRaises(gt.InvalidVoteError):
            gt.tally_proposal(data)

    def test_delegated_voter_votes(self):
        data = base_input(delegations={"a": "b"}, votes=[{"voter": "a", "choice": "yes"}])
        with self.assertRaises(gt.InvalidVoteError):
            gt.tally_proposal(data)

    def test_voter_outside_snapshot(self):
        data = base_input(votes=[{"voter": "z", "choice": "yes"}])
        with self.assertRaises(gt.InvalidVoteError):
            gt.tally_proposal(data)

    def test_choice_not_in_choices(self):
        data = base_input(votes=[{"voter": "a", "choice": "maybe"}])
        with self.assertRaises(gt.InvalidVoteError):
            gt.tally_proposal(data)


class VerifyTests(unittest.TestCase):
    def setUp(self):
        self.data = base_input(
            snapshot={"a": 2, "b": 3, "c": 4},
            delegations={"a": "b", "b": "c"},
            votes=[{"voter": "c", "choice": "yes"}],
        )
        self.result = gt.tally_proposal(self.data)

    def test_verify_passes_for_fresh_computation(self):
        self.assertTrue(gt.verify_tally(self.data, self.result))

    def test_verify_recomputes_independently(self):
        # 传入一个手工构造但数值正确的结果，也应通过。
        handcrafted = {
            "proposal_id": "p1",
            "per_choice": {"yes": 9, "no": 0},
            "effective_weights": {"c": 9},
            "uncounted_weight": 0,
            "counted_weight": 9,
            "snapshot_total_weight": 9,
            "winners": ["yes"],
            "is_tie": False,
        }
        self.assertTrue(gt.verify_tally(self.data, handcrafted))

    def _assert_rejected(self, result):
        with self.assertRaises(gt.TallyVerificationError):
            gt.verify_tally(self.data, result)

    def test_tampered_per_choice(self):
        bad = json.loads(json.dumps(self.result))
        bad["per_choice"]["yes"] = 8
        self._assert_rejected(bad)

    def test_per_choice_wrong_order(self):
        bad = json.loads(json.dumps(self.result))
        bad["per_choice"] = {"no": 0, "yes": 9}
        self._assert_rejected(bad)

    def test_tampered_effective_weights(self):
        bad = json.loads(json.dumps(self.result))
        bad["effective_weights"] = {"c": 8}
        self._assert_rejected(bad)

    def test_broken_conservation(self):
        bad = json.loads(json.dumps(self.result))
        bad["uncounted_weight"] = 1
        self._assert_rejected(bad)

    def test_wrong_totals(self):
        bad = json.loads(json.dumps(self.result))
        bad["snapshot_total_weight"] = 10
        self._assert_rejected(bad)

    def test_winners_only_match_is_not_enough(self):
        # 只让 winners 保持正确但权重被篡改，仍须复核失败。
        bad = json.loads(json.dumps(self.result))
        bad["per_choice"] = {"yes": 900, "no": 0}
        bad["counted_weight"] = 900
        self._assert_rejected(bad)

    def test_wrong_winners_and_tie(self):
        bad = json.loads(json.dumps(self.result))
        bad["winners"] = ["no"]
        self._assert_rejected(bad)
        bad2 = json.loads(json.dumps(self.result))
        bad2["is_tie"] = True
        self._assert_rejected(bad2)

    def test_missing_result_field(self):
        for field in (
            "proposal_id", "per_choice", "effective_weights", "uncounted_weight",
            "counted_weight", "snapshot_total_weight", "winners", "is_tie",
        ):
            bad = json.loads(json.dumps(self.result))
            del bad[field]
            self._assert_rejected(bad)

    def test_non_integer_result_values(self):
        bad = json.loads(json.dumps(self.result))
        bad["counted_weight"] = 9.0
        self._assert_rejected(bad)

    def test_verify_invalid_input_raises_input_error(self):
        with self.assertRaises(gt.InvalidInputError):
            gt.verify_tally(base_input(choices=[]), self.result)


class VerifyOverrideTests(unittest.TestCase):
    def setUp(self):
        self.data = base_input(
            snapshot={"a": 2, "b": 3, "c": 4},
            delegations={"a": "b", "b": "c"},
            votes=[
                {"voter": "a", "choice": "no", "delegation_override": True},
                {"voter": "c", "choice": "yes"},
            ],
        )
        self.result = gt.tally_proposal(self.data)

    def test_verify_passes(self):
        self.assertTrue(gt.verify_tally(self.data, self.result))
        self.assertEqual(self.result["effective_weights"], {"a": 2, "c": 7})
        self.assertEqual(self.result["per_choice"], {"yes": 7, "no": 2})

    def test_verify_handcrafted_override_result(self):
        handcrafted = {
            "proposal_id": "p1",
            "per_choice": {"yes": 7, "no": 2},
            "effective_weights": {"a": 2, "c": 7},
            "uncounted_weight": 0,
            "counted_weight": 9,
            "snapshot_total_weight": 9,
            "winners": ["yes"],
            "is_tie": False,
        }
        self.assertTrue(gt.verify_tally(self.data, handcrafted))

    def _assert_rejected(self, result):
        with self.assertRaises(gt.TallyVerificationError):
            gt.verify_tally(self.data, result)

    def test_override_own_weight_misattributed_to_trustee(self):
        # 受托人被错记为完整合并权重 9（应扣除覆盖票抽出的 2）。
        bad = json.loads(json.dumps(self.result))
        bad["effective_weights"] = {"a": 2, "c": 9}
        bad["per_choice"] = {"yes": 9, "no": 2}
        bad["counted_weight"] = 11
        self._assert_rejected(bad)

    def test_override_missing_own_weight(self):
        # 覆盖票权重被抹掉：守恒仍可能成立（塞进 uncounted），但归属不符。
        bad = json.loads(json.dumps(self.result))
        bad["effective_weights"] = {"c": 7}
        bad["per_choice"] = {"yes": 7, "no": 0}
        bad["counted_weight"] = 7
        bad["uncounted_weight"] = 2
        self._assert_rejected(bad)

    def test_override_wrong_effective_weight_order(self):
        bad = json.loads(json.dumps(self.result))
        bad["effective_weights"] = {"c": 7, "a": 2}
        self._assert_rejected(bad)

    def test_override_per_choice_wrong_order(self):
        bad = json.loads(json.dumps(self.result))
        bad["per_choice"] = {"no": 2, "yes": 7}
        self._assert_rejected(bad)

    def test_override_wrong_tie_flag(self):
        data = base_input(
            snapshot={"a": 5, "b": 5},
            delegations={"a": "b"},
            votes=[
                {"voter": "a", "choice": "no", "delegation_override": True},
                {"voter": "b", "choice": "yes"},
            ],
        )
        result = gt.tally_proposal(data)
        self.assertTrue(result["is_tie"])
        bad = json.loads(json.dumps(result))
        bad["is_tie"] = False
        with self.assertRaises(gt.TallyVerificationError):
            gt.verify_tally(data, bad)

    def test_override_bad_weight_type(self):
        bad = json.loads(json.dumps(self.result))
        bad["effective_weights"]["a"] = 2.0
        self._assert_rejected(bad)

    def test_verify_override_invalid_input_raises_input_error(self):
        bad_data = json.loads(json.dumps(self.data))
        bad_data["votes"][0]["delegation_override"] = "true"
        with self.assertRaises(gt.InvalidInputError):
            gt.verify_tally(bad_data, self.result)


class CliTests(unittest.TestCase):
    def run_cli(self, payload):
        proc = subprocess.run(
            [sys.executable, str(MODULE)],
            input=json.dumps(payload),
            capture_output=True,
            text=True,
        )
        return proc.returncode, json.loads(proc.stdout), proc.stderr

    def test_success_exit_zero(self):
        code, out, err = self.run_cli(base_input(
            votes=[{"voter": "a", "choice": "yes"}, {"voter": "b", "choice": "no"}]
        ))
        self.assertEqual(code, 0)
        self.assertEqual(out["per_choice"], {"yes": 10, "no": 5})
        self.assertNotIn("error", out)

    def test_error_exit_two_with_class_name(self):
        data = base_input(choices=[])
        code, out, err = self.run_cli(data)
        self.assertEqual(code, 2)
        self.assertEqual(out["error"], "InvalidInputError")
        self.assertIsInstance(out["message"], str)
        self.assertNotIn("0x", out["message"])  # 无内存地址

    def test_each_error_class_on_stdout(self):
        cases = [
            (base_input(choices=[]), "InvalidInputError"),
            (base_input(delegations={"a": "z"}), "InvalidDelegationError"),
            (base_input(delegations={"a": "b", "b": "a"}), "DelegationCycleError"),
            (base_input(votes=[{"voter": "z", "choice": "yes"}]), "InvalidVoteError"),
        ]
        for payload, name in cases:
            with self.subTest(name=name):
                code, out, _ = self.run_cli(payload)
                self.assertEqual(code, 2)
                self.assertEqual(out["error"], name)

    def test_invalid_json(self):
        proc = subprocess.run(
            [sys.executable, str(MODULE)],
            input="{not json",
            capture_output=True,
            text=True,
        )
        self.assertEqual(proc.returncode, 2)
        out = json.loads(proc.stdout)
        self.assertEqual(out["error"], "InvalidInputError")

    def test_override_success_and_failure(self):
        payload = base_input(
            snapshot={"a": 2, "b": 3, "c": 4},
            delegations={"a": "b", "b": "c"},
            votes=[
                {"voter": "a", "choice": "no", "delegation_override": True},
                {"voter": "c", "choice": "yes"},
            ],
        )
        code, out, err = self.run_cli(payload)
        self.assertEqual(code, 0)
        self.assertEqual(err, "")
        self.assertEqual(out["effective_weights"], {"a": 2, "c": 7})
        self.assertEqual(out["per_choice"], {"yes": 7, "no": 2})

        bad = json.loads(json.dumps(payload))
        bad["votes"][0]["delegation_override"] = "true"
        code, out, err = self.run_cli(bad)
        self.assertEqual(code, 2)
        self.assertEqual(err, "")
        self.assertEqual(out["error"], "InvalidInputError")
        self.assertIsInstance(out["message"], str)

        bad2 = json.loads(json.dumps(payload))
        bad2["votes"][0]["delegation_override"] = False
        code, out, err = self.run_cli(bad2)
        self.assertEqual(code, 2)
        self.assertEqual(out["error"], "InvalidVoteError")

    def test_error_messages_are_stable(self):
        # 同一错误两次运行，message 一致且不含对象地址。
        payloads = [base_input(choices=[]), base_input(delegations={"a": "z"})]
        for payload in payloads:
            outs = []
            for _ in range(2):
                proc = subprocess.run(
                    [sys.executable, str(MODULE)],
                    input=json.dumps(payload),
                    capture_output=True,
                    text=True,
                )
                outs.append(json.loads(proc.stdout)["message"])
            self.assertEqual(outs[0], outs[1])
            self.assertNotIn("object at", outs[0])


class ProvenanceTests(unittest.TestCase):
    def test_omitted_or_false_keeps_output_shape(self):
        data = base_input(
            delegations={"a": "b"},
            votes=[{"voter": "b", "choice": "yes"}],
        )
        for kwargs in ({}, {"include_provenance": False}):
            result = gt.tally_proposal(data, **kwargs)
            self.assertNotIn("weight_provenance", result)
            self.assertEqual(
                list(result.keys()),
                [
                    "proposal_id", "per_choice", "effective_weights",
                    "uncounted_weight", "counted_weight",
                    "snapshot_total_weight", "winners", "is_tie",
                ],
            )
        # 输入字段显式 false 同样不启用。
        data2 = base_input(
            delegations={"a": "b"},
            votes=[{"voter": "b", "choice": "yes"}],
            include_provenance=False,
        )
        self.assertNotIn("weight_provenance", gt.tally_proposal(data2))

    def test_direct_votes_provenance(self):
        data = base_input(
            votes=[{"voter": "a", "choice": "yes"}, {"voter": "b", "choice": "no"}]
        )
        result = gt.tally_proposal(data, include_provenance=True)
        self.assertEqual(
            result["weight_provenance"], {"a": {"a": 10}, "b": {"b": 5}}
        )

    def test_input_field_enables_provenance(self):
        data = base_input(
            votes=[{"voter": "a", "choice": "yes"}],
            include_provenance=True,
        )
        result = gt.tally_proposal(data)
        self.assertEqual(result["weight_provenance"], {"a": {"a": 10}})

    def test_chain_provenance_in_snapshot_order(self):
        data = base_input(
            snapshot={"c": 4, "a": 2, "b": 3},
            delegations={"a": "b", "b": "c"},
            votes=[{"voter": "c", "choice": "yes"}],
        )
        result = gt.tally_proposal(data, include_provenance=True)
        # 内层按 snapshot 顺序（c, a, b），只列正权重来源。
        self.assertEqual(
            list(result["weight_provenance"]["c"].items()),
            [("c", 4), ("a", 2), ("b", 3)],
        )
        self.assertEqual(
            sum(result["weight_provenance"]["c"].values()),
            result["effective_weights"]["c"],
        )

    def test_override_provenance_attribution(self):
        # a -> b -> c：a 覆盖票只含本人；c 普通票只含剩余来源 b、c。
        data = base_input(
            snapshot={"a": 2, "b": 3, "c": 4},
            delegations={"a": "b", "b": "c"},
            votes=[
                {"voter": "a", "choice": "no", "delegation_override": True},
                {"voter": "c", "choice": "yes"},
            ],
        )
        result = gt.tally_proposal(data, include_provenance=True)
        self.assertEqual(
            result["weight_provenance"],
            {"a": {"a": 2}, "c": {"b": 3, "c": 4}},
        )

    def test_intermediate_override_keeps_upstream_with_trustee(self):
        # b 覆盖抽走本人 3；a 的 2 仍汇入 c。
        data = base_input(
            snapshot={"a": 2, "b": 3, "c": 4},
            delegations={"a": "b", "b": "c"},
            votes=[
                {"voter": "b", "choice": "no", "delegation_override": True},
                {"voter": "c", "choice": "yes"},
            ],
        )
        result = gt.tally_proposal(data, include_provenance=True)
        self.assertEqual(
            result["weight_provenance"],
            {"b": {"b": 3}, "c": {"a": 2, "c": 4}},
        )

    def test_multiple_overrides_each_own_vote(self):
        data = base_input(
            snapshot={"a": 2, "c": 4, "d": 5},
            delegations={"a": "d", "c": "d"},
            votes=[
                {"voter": "a", "choice": "yes", "delegation_override": True},
                {"voter": "c", "choice": "no", "delegation_override": True},
                {"voter": "d", "choice": "yes"},
            ],
        )
        result = gt.tally_proposal(data, include_provenance=True)
        self.assertEqual(
            result["weight_provenance"],
            {"a": {"a": 2}, "c": {"c": 4}, "d": {"d": 5}},
        )

    def test_zero_weight_counted_vote_has_empty_mapping(self):
        data = base_input(
            snapshot={"a": 0, "b": 5},
            votes=[{"voter": "a", "choice": "yes"}, {"voter": "b", "choice": "no"}],
        )
        result = gt.tally_proposal(data, include_provenance=True)
        self.assertEqual(
            result["weight_provenance"], {"a": {}, "b": {"b": 5}}
        )

    def test_zero_weight_override_has_empty_mapping(self):
        data = base_input(
            snapshot={"a": 0, "b": 5},
            delegations={"a": "b"},
            votes=[
                {"voter": "a", "choice": "yes", "delegation_override": True},
                {"voter": "b", "choice": "no"},
            ],
        )
        result = gt.tally_proposal(data, include_provenance=True)
        self.assertEqual(
            result["weight_provenance"], {"a": {}, "b": {"b": 5}}
        )

    def test_zero_weight_source_excluded_from_trustee(self):
        data = base_input(
            snapshot={"a": 0, "b": 5},
            delegations={"a": "b"},
            votes=[{"voter": "b", "choice": "yes"}],
        )
        result = gt.tally_proposal(data, include_provenance=True)
        self.assertEqual(result["weight_provenance"], {"b": {"b": 5}})

    def test_provenance_outer_follows_votes_order(self):
        data = base_input(
            snapshot={"a": 2, "b": 3, "c": 4},
            delegations={"a": "b", "b": "c"},
            votes=[
                {"voter": "c", "choice": "yes"},
                {"voter": "a", "choice": "no", "delegation_override": True},
            ],
        )
        result = gt.tally_proposal(data, include_provenance=True)
        self.assertEqual(list(result["weight_provenance"].keys()), ["c", "a"])
        self.assertEqual(
            list(result["weight_provenance"].keys()),
            list(result["effective_weights"].keys()),
        )

    def test_provenance_sums_equal_effective_weights(self):
        data = base_input(
            snapshot={"a": 2, "b": 3, "c": 4, "d": 5},
            delegations={"a": "b", "b": "c"},
            votes=[
                {"voter": "b", "choice": "no", "delegation_override": True},
                {"voter": "c", "choice": "yes"},
                {"voter": "d", "choice": "yes"},
            ],
        )
        result = gt.tally_proposal(data, include_provenance=True)
        for voter, sources in result["weight_provenance"].items():
            self.assertEqual(sum(sources.values()), result["effective_weights"][voter])
        # 同一来源只归属一张计票。
        all_sources = [
            source for sources in result["weight_provenance"].values() for source in sources
        ]
        self.assertEqual(len(all_sources), len(set(all_sources)))

    def test_non_boolean_include_provenance(self):
        for value in ("true", 1, 0, None, [], {}):
            with self.subTest(value=value):
                data = base_input(votes=[{"voter": "a", "choice": "yes"}])
                with self.assertRaises(gt.InvalidInputError):
                    gt.tally_proposal(data, include_provenance=value)
                data2 = base_input(
                    votes=[{"voter": "a", "choice": "yes"}],
                    include_provenance=value,
                )
                with self.assertRaises(gt.InvalidInputError):
                    gt.tally_proposal(data2)
                with self.assertRaises(gt.InvalidInputError):
                    gt.verify_tally(data2, {})


class VerifyProvenanceTests(unittest.TestCase):
    def setUp(self):
        self.data = base_input(
            snapshot={"a": 2, "b": 3, "c": 4},
            delegations={"a": "b", "b": "c"},
            votes=[
                {"voter": "a", "choice": "no", "delegation_override": True},
                {"voter": "c", "choice": "yes"},
            ],
        )
        self.result = gt.tally_proposal(self.data, include_provenance=True)

    def test_verify_passes_with_provenance(self):
        self.assertTrue(gt.verify_tally(self.data, self.result, include_provenance=True))
        # 输入字段为 true 时，verify 省略参数也会复核 provenance。
        data = base_input(
            snapshot={"a": 2, "b": 3, "c": 4},
            delegations={"a": "b", "b": "c"},
            votes=[
                {"voter": "a", "choice": "no", "delegation_override": True},
                {"voter": "c", "choice": "yes"},
            ],
            include_provenance=True,
        )
        self.assertTrue(gt.verify_tally(data, self.result))

    def test_verify_handcrafted_provenance(self):
        handcrafted = {
            "proposal_id": "p1",
            "per_choice": {"yes": 7, "no": 2},
            "effective_weights": {"a": 2, "c": 7},
            "weight_provenance": {"a": {"a": 2}, "c": {"b": 3, "c": 4}},
            "uncounted_weight": 0,
            "counted_weight": 9,
            "snapshot_total_weight": 9,
            "winners": ["yes"],
            "is_tie": False,
        }
        self.assertTrue(
            gt.verify_tally(self.data, handcrafted, include_provenance=True)
        )

    def test_flag_false_ignores_extra_fields(self):
        # false 时结果额外字段不受约束：provenance 缺失或乱写都通过。
        result = gt.tally_proposal(self.data)
        self.assertTrue(gt.verify_tally(self.data, result))
        bad = json.loads(json.dumps(result))
        bad["weight_provenance"] = {"c": {"a": 999}, "junk": []}
        self.assertTrue(gt.verify_tally(self.data, bad))

    def _assert_rejected(self, result):
        with self.assertRaises(gt.TallyVerificationError):
            gt.verify_tally(self.data, result, include_provenance=True)

    def test_missing_provenance_field(self):
        bad = json.loads(json.dumps(self.result))
        del bad["weight_provenance"]
        self._assert_rejected(bad)

    def test_provenance_not_an_object(self):
        for value in ([], "x", 3, None):
            bad = json.loads(json.dumps(self.result))
            bad["weight_provenance"] = value
            self._assert_rejected(bad)

    def test_outer_order_mismatch(self):
        bad = json.loads(json.dumps(self.result))
        bad["weight_provenance"] = {
            "c": {"b": 3, "c": 4},
            "a": {"a": 2},
        }
        self._assert_rejected(bad)

    def test_outer_missing_and_extra_voter(self):
        bad = json.loads(json.dumps(self.result))
        del bad["weight_provenance"]["a"]
        self._assert_rejected(bad)
        bad = json.loads(json.dumps(self.result))
        bad["weight_provenance"]["z"] = {"z": 1}
        self._assert_rejected(bad)

    def test_inner_order_mismatch(self):
        bad = json.loads(json.dumps(self.result))
        bad["weight_provenance"]["c"] = {"c": 4, "b": 3}
        self._assert_rejected(bad)

    def test_source_misattribution(self):
        # b 被错归到覆盖票 a 名下。
        bad = json.loads(json.dumps(self.result))
        bad["weight_provenance"] = {"a": {"a": 2, "b": 3}, "c": {"c": 4}}
        self._assert_rejected(bad)

    def test_duplicate_attribution_rejected(self):
        # 同一来源归入两张票（同时必然造成键/求和错配，仍须复核失败）。
        bad = json.loads(json.dumps(self.result))
        bad["weight_provenance"]["a"] = {"a": 2, "b": 3}
        self._assert_rejected(bad)

    def test_source_weight_not_snapshot_weight(self):
        bad = json.loads(json.dumps(self.result))
        bad["weight_provenance"]["c"]["b"] = 2
        self._assert_rejected(bad)

    def test_source_weight_non_integer(self):
        for value in (3.0, "3", True, None):
            bad = json.loads(json.dumps(self.result))
            bad["weight_provenance"]["c"]["b"] = value
            self._assert_rejected(bad)

    def test_source_weight_non_positive(self):
        for value in (0, -1):
            bad = json.loads(json.dumps(self.result))
            bad["weight_provenance"]["c"]["b"] = value
            self._assert_rejected(bad)

    def test_sum_mismatch_against_effective_weights(self):
        # 来源映射不变但 effective_weights 被改：求和关系破裂。
        bad = json.loads(json.dumps(self.result))
        bad["effective_weights"]["c"] = 8
        self._assert_rejected(bad)

    def test_zero_weight_vote_provenance_verified(self):
        data = base_input(
            snapshot={"a": 0, "b": 5},
            votes=[{"voter": "a", "choice": "yes"}, {"voter": "b", "choice": "no"}],
        )
        result = gt.tally_proposal(data, include_provenance=True)
        self.assertEqual(result["weight_provenance"], {"a": {}, "b": {"b": 5}})
        self.assertTrue(gt.verify_tally(data, result, include_provenance=True))


class CliProvenanceTests(unittest.TestCase):
    def run_cli(self, payload):
        proc = subprocess.run(
            [sys.executable, str(MODULE)],
            input=json.dumps(payload),
            capture_output=True,
            text=True,
        )
        return proc.returncode, json.loads(proc.stdout), proc.stderr

    def test_cli_provenance_enabled_via_input_field(self):
        payload = base_input(
            snapshot={"a": 2, "b": 3, "c": 4},
            delegations={"a": "b", "b": "c"},
            votes=[
                {"voter": "a", "choice": "no", "delegation_override": True},
                {"voter": "c", "choice": "yes"},
            ],
            include_provenance=True,
        )
        code, out, err = self.run_cli(payload)
        self.assertEqual(code, 0)
        self.assertEqual(err, "")
        self.assertEqual(
            out["weight_provenance"], {"a": {"a": 2}, "c": {"b": 3, "c": 4}}
        )

    def test_cli_provenance_omitted_shape_unchanged(self):
        payload = base_input(votes=[{"voter": "a", "choice": "yes"}])
        code, out, err = self.run_cli(payload)
        self.assertEqual(code, 0)
        self.assertNotIn("weight_provenance", out)

    def test_cli_non_boolean_include_provenance(self):
        payload = base_input(
            votes=[{"voter": "a", "choice": "yes"}],
            include_provenance="true",
        )
        code, out, err = self.run_cli(payload)
        self.assertEqual(code, 2)
        self.assertEqual(err, "")
        self.assertEqual(out["error"], "InvalidInputError")
        self.assertIsInstance(out["message"], str)


if __name__ == "__main__":
    unittest.main()
