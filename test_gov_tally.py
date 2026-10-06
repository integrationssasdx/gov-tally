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


class DecisionRulesTests(unittest.TestCase):
    def rules(self, **overrides):
        rules = {
            "approval_choices": ["yes"],
            "min_counted_weight": 1,
            "approval_basis_points": 5000,
        }
        rules.update(overrides)
        return rules

    def test_omitted_keeps_output_shape(self):
        data = base_input(votes=[{"voter": "a", "choice": "yes"}])
        result = gt.tally_proposal(data)
        self.assertEqual(
            list(result.keys()),
            [
                "proposal_id", "per_choice", "effective_weights",
                "uncounted_weight", "counted_weight",
                "snapshot_total_weight", "winners", "is_tie",
            ],
        )
        for field in ("quorum_met", "approval_met", "decision"):
            self.assertNotIn(field, result)

    def test_fields_appended_after_existing_fields(self):
        data = base_input(
            votes=[{"voter": "a", "choice": "yes"}, {"voter": "b", "choice": "no"}],
            decision_rules=self.rules(min_counted_weight=15),
        )
        result = gt.tally_proposal(data)
        self.assertEqual(
            list(result.keys())[-3:],
            ["quorum_met", "approval_met", "decision"],
        )

    def test_fields_appended_after_provenance_as_well(self):
        data = base_input(
            votes=[{"voter": "a", "choice": "yes"}],
            decision_rules=self.rules(),
            include_provenance=True,
        )
        result = gt.tally_proposal(data)
        self.assertEqual(
            list(result.keys()),
            [
                "proposal_id", "per_choice", "effective_weights",
                "weight_provenance", "uncounted_weight", "counted_weight",
                "snapshot_total_weight", "winners", "is_tie",
                "quorum_met", "approval_met", "decision",
            ],
        )

    def test_approved(self):
        # yes 10 / no 5，counted 15，门槛 15，赞成率 10/15 ≈ 6667bps。
        data = base_input(
            votes=[{"voter": "a", "choice": "yes"}, {"voter": "b", "choice": "no"}],
            decision_rules=self.rules(min_counted_weight=15, approval_basis_points=6000),
        )
        result = gt.tally_proposal(data)
        self.assertTrue(result["quorum_met"])
        self.assertTrue(result["approval_met"])
        self.assertEqual(result["decision"], "approved")

    def test_rejected_when_approval_below_basis(self):
        data = base_input(
            votes=[{"voter": "a", "choice": "yes"}, {"voter": "b", "choice": "no"}],
            decision_rules=self.rules(min_counted_weight=15, approval_basis_points=7000),
        )
        result = gt.tally_proposal(data)
        self.assertTrue(result["quorum_met"])
        self.assertFalse(result["approval_met"])
        self.assertEqual(result["decision"], "rejected")

    def test_no_quorum_below_min(self):
        data = base_input(
            votes=[{"voter": "a", "choice": "yes"}],
            decision_rules=self.rules(min_counted_weight=11),
        )
        result = gt.tally_proposal(data)
        self.assertFalse(result["quorum_met"])
        # 即使赞成比例足够，未达法定人数优先判 no_quorum。
        self.assertTrue(result["approval_met"])
        self.assertEqual(result["decision"], "no_quorum")

    def test_zero_counted_is_never_quorum_even_min_zero(self):
        data = base_input(decision_rules=self.rules(min_counted_weight=0))
        result = gt.tally_proposal(data)
        self.assertEqual(result["counted_weight"], 0)
        self.assertFalse(result["quorum_met"])
        # 0*10000 >= 0*bps 成立，approval_met 按规则为真，
        # 但 quorum 优先，decision 仍为 no_quorum。
        self.assertTrue(result["approval_met"])
        self.assertEqual(result["decision"], "no_quorum")

    def test_quorum_boundary_is_inclusive(self):
        data = base_input(
            votes=[{"voter": "a", "choice": "yes"}, {"voter": "b", "choice": "no"}],
            decision_rules=self.rules(min_counted_weight=15),
        )
        result = gt.tally_proposal(data)
        self.assertTrue(result["quorum_met"])

    def test_approval_boundary_is_inclusive(self):
        data = base_input(
            snapshot={"a": 5, "b": 5},
            votes=[{"voter": "a", "choice": "yes"}, {"voter": "b", "choice": "no"}],
            decision_rules=self.rules(approval_basis_points=5000),
        )
        result = gt.tally_proposal(data)
        # 5*10000 == 10*5000：恰好相等算通过。
        self.assertTrue(result["approval_met"])
        self.assertEqual(result["decision"], "approved")

        data["decision_rules"] = self.rules(approval_basis_points=5001)
        result = gt.tally_proposal(data)
        self.assertFalse(result["approval_met"])
        self.assertEqual(result["decision"], "rejected")

    def test_basis_points_extremes(self):
        votes = [
            {"voter": "a", "choice": "yes"},
            {"voter": "b", "choice": "no"},
        ]
        # 1 bps：有任意赞成权重即通过。
        data = base_input(
            votes=votes,
            decision_rules=self.rules(
                min_counted_weight=15, approval_basis_points=1
            ),
        )
        result = gt.tally_proposal(data)
        self.assertEqual(result["decision"], "approved")

        # 10000 bps：全部计票权重都在赞成选项上才通过。
        all_yes = base_input(
            votes=[{"voter": "a", "choice": "yes"}],
            decision_rules=self.rules(
                min_counted_weight=10, approval_basis_points=10000
            ),
        )
        self.assertEqual(gt.tally_proposal(all_yes)["decision"], "approved")
        mixed = base_input(
            votes=votes,
            decision_rules=self.rules(
                min_counted_weight=15, approval_basis_points=10000
            ),
        )
        self.assertEqual(gt.tally_proposal(mixed)["decision"], "rejected")

    def test_approval_choices_subset_sums_members(self):
        data = base_input(
            choices=["yes", "no", "abstain"],
            snapshot={"a": 6, "b": 2, "c": 1},
            votes=[
                {"voter": "a", "choice": "yes"},
                {"voter": "b", "choice": "no"},
                {"voter": "c", "choice": "abstain"},
            ],
            decision_rules=self.rules(
                approval_choices=["yes", "abstain"],
                min_counted_weight=9,
                approval_basis_points=7777,
            ),
        )
        result = gt.tally_proposal(data)
        # 赞成权重 7/9 ≈ 7778bps：7777 通过。
        self.assertTrue(result["approval_met"])
        self.assertEqual(result["decision"], "approved")
        data["decision_rules"]["approval_basis_points"] = 7778
        result = gt.tally_proposal(data)
        self.assertFalse(result["approval_met"])
        self.assertEqual(result["decision"], "rejected")

    def test_thresholds_apply_to_merged_delegated_weights(self):
        data = base_input(
            snapshot={"a": 2, "b": 3, "c": 4},
            delegations={"a": "b", "b": "c"},
            votes=[{"voter": "c", "choice": "yes"}],
            decision_rules=self.rules(min_counted_weight=9),
        )
        result = gt.tally_proposal(data)
        self.assertTrue(result["quorum_met"])
        self.assertEqual(result["decision"], "approved")

    def test_threshold_fields_are_booleans_and_string(self):
        data = base_input(
            votes=[{"voter": "a", "choice": "yes"}],
            decision_rules=self.rules(),
        )
        result = gt.tally_proposal(data)
        self.assertIsInstance(result["quorum_met"], bool)
        self.assertIsInstance(result["approval_met"], bool)
        self.assertIsInstance(result["decision"], str)


class InvalidDecisionRulesTests(unittest.TestCase):
    def rules(self, **overrides):
        rules = {
            "approval_choices": ["yes"],
            "min_counted_weight": 1,
            "approval_basis_points": 5000,
        }
        rules.update(overrides)
        return rules

    def assert_invalid(self, rules):
        data = base_input(votes=[{"voter": "a", "choice": "yes"}])
        data["decision_rules"] = rules
        with self.assertRaises(gt.InvalidInputError):
            gt.tally_proposal(data)

    def test_rules_not_an_object(self):
        for value in ([], "x", 5, None, True):
            with self.subTest(value=value):
                self.assert_invalid(value)

    def test_missing_field(self):
        for field in (
            "approval_choices",
            "min_counted_weight",
            "approval_basis_points",
        ):
            rules = self.rules()
            del rules[field]
            self.assert_invalid(rules)

    def test_extra_field(self):
        self.assert_invalid(self.rules(extra=1))
        self.assert_invalid(self.rules(quorum_met=True))

    def test_bad_approval_choices(self):
        self.assert_invalid(self.rules(approval_choices="yes"))
        self.assert_invalid(self.rules(approval_choices=[]))
        self.assert_invalid(self.rules(approval_choices=[1]))
        self.assert_invalid(self.rules(approval_choices=[True]))
        self.assert_invalid(self.rules(approval_choices=["nope"]))
        self.assert_invalid(self.rules(approval_choices=["yes", "yes"]))

    def test_bad_min_counted_weight(self):
        for value in (True, False, 1.5, "1", None, -1):
            with self.subTest(value=value):
                self.assert_invalid(self.rules(min_counted_weight=value))

    def test_bad_approval_basis_points(self):
        for value in (True, False, 1.0, "1", None, 0, -1, 10001):
            with self.subTest(value=value):
                self.assert_invalid(self.rules(approval_basis_points=value))

    def test_boundary_values_accepted(self):
        data = base_input(
            votes=[{"voter": "a", "choice": "yes"}],
            decision_rules=self.rules(
                min_counted_weight=0, approval_basis_points=1
            ),
        )
        self.assertEqual(gt.tally_proposal(data)["decision"], "approved")
        data["decision_rules"]["approval_basis_points"] = 10000
        self.assertEqual(gt.tally_proposal(data)["decision"], "approved")

    def test_verify_invalid_rules_raise_input_error(self):
        data = base_input(decision_rules=self.rules(approval_basis_points=0))
        with self.assertRaises(gt.InvalidInputError):
            gt.verify_tally(data, {})


class VerifyDecisionRulesTests(unittest.TestCase):
    def setUp(self):
        self.data = base_input(
            votes=[{"voter": "a", "choice": "yes"}, {"voter": "b", "choice": "no"}],
            decision_rules={
                "approval_choices": ["yes"],
                "min_counted_weight": 15,
                "approval_basis_points": 6000,
            },
        )
        self.result = gt.tally_proposal(self.data)

    def test_verify_passes(self):
        self.assertTrue(gt.verify_tally(self.data, self.result))
        self.assertEqual(
            (
                self.result["quorum_met"],
                self.result["approval_met"],
                self.result["decision"],
            ),
            (True, True, "approved"),
        )

    def test_verify_handcrafted_decision_result(self):
        handcrafted = {
            "proposal_id": "p1",
            "per_choice": {"yes": 10, "no": 5},
            "effective_weights": {"a": 10, "b": 5},
            "uncounted_weight": 0,
            "counted_weight": 15,
            "snapshot_total_weight": 15,
            "winners": ["yes"],
            "is_tie": False,
            "quorum_met": True,
            "approval_met": True,
            "decision": "approved",
        }
        self.assertTrue(gt.verify_tally(self.data, handcrafted))

    def _assert_rejected(self, result):
        with self.assertRaises(gt.TallyVerificationError):
            gt.verify_tally(self.data, result)

    def test_missing_decision_field(self):
        for field in ("quorum_met", "approval_met", "decision"):
            bad = json.loads(json.dumps(self.result))
            del bad[field]
            self._assert_rejected(bad)

    def test_extra_field_rejected(self):
        bad = json.loads(json.dumps(self.result))
        bad["extra"] = 1
        self._assert_rejected(bad)

    def test_fields_wrong_order(self):
        bad = json.loads(json.dumps(self.result))
        reordered = {k: v for k, v in bad.items() if k != "quorum_met"}
        reordered["quorum_met"] = True  # 挪到尾部
        self._assert_rejected(reordered)

        # 标志不同（rejected：quorum 真、approval 假）时互换键值：
        # 键序不变但取值与重算结果不符，仍须复核失败。
        data = base_input(
            votes=[{"voter": "a", "choice": "yes"}, {"voter": "b", "choice": "no"}],
            decision_rules={
                "approval_choices": ["yes"],
                "min_counted_weight": 15,
                "approval_basis_points": 7000,
            },
        )
        rejected = gt.tally_proposal(data)
        self.assertEqual(rejected["decision"], "rejected")
        swapped = json.loads(json.dumps(rejected))
        swapped["quorum_met"], swapped["approval_met"] = (
            swapped["approval_met"],
            swapped["quorum_met"],
        )
        with self.assertRaises(gt.TallyVerificationError):
            gt.verify_tally(data, swapped)

    def test_tampered_threshold_flags(self):
        bad = json.loads(json.dumps(self.result))
        bad["quorum_met"] = False
        self._assert_rejected(bad)
        bad = json.loads(json.dumps(self.result))
        bad["approval_met"] = False
        self._assert_rejected(bad)

    def test_tampered_decision(self):
        bad = json.loads(json.dumps(self.result))
        bad["decision"] = "rejected"
        self._assert_rejected(bad)

    def test_bad_threshold_types_and_values(self):
        tamperings = [
            ("quorum_met", 1),
            ("quorum_met", "true"),
            ("quorum_met", None),
            ("approval_met", 0),
            ("decision", "maybe"),
            ("decision", 3),
            ("decision", None),
        ]
        for field, value in tamperings:
            bad = json.loads(json.dumps(self.result))
            bad[field] = value
            self._assert_rejected(bad)

    def test_inconsistent_decision_combo(self):
        bad = json.loads(json.dumps(self.result))
        bad["quorum_met"] = False
        bad["approval_met"] = False
        bad["decision"] = "approved"
        self._assert_rejected(bad)

    def test_no_quorum_case_verified(self):
        data = base_input(
            votes=[{"voter": "a", "choice": "yes"}],
            decision_rules={
                "approval_choices": ["yes"],
                "min_counted_weight": 11,
                "approval_basis_points": 5000,
            },
        )
        result = gt.tally_proposal(data)
        self.assertEqual(result["decision"], "no_quorum")
        self.assertTrue(gt.verify_tally(data, result))
        bad = json.loads(json.dumps(result))
        bad["decision"] = "approved"
        with self.assertRaises(gt.TallyVerificationError):
            gt.verify_tally(data, bad)

    def test_decision_fields_without_config_rejected(self):
        data = base_input(
            votes=[{"voter": "a", "choice": "yes"}, {"voter": "b", "choice": "no"}]
        )
        result = gt.tally_proposal(data)
        self.assertTrue(gt.verify_tally(data, result))
        for field, value in (
            ("quorum_met", True),
            ("approval_met", False),
            ("decision", "approved"),
        ):
            bad = json.loads(json.dumps(result))
            bad[field] = value
            with self.assertRaises(gt.TallyVerificationError):
                gt.verify_tally(data, bad)


class CliDecisionRulesTests(unittest.TestCase):
    def run_cli(self, payload):
        proc = subprocess.run(
            [sys.executable, str(MODULE)],
            input=json.dumps(payload),
            capture_output=True,
            text=True,
        )
        return proc.returncode, json.loads(proc.stdout), proc.stderr

    def test_cli_decision_rules_success(self):
        payload = base_input(
            votes=[{"voter": "a", "choice": "yes"}, {"voter": "b", "choice": "no"}],
            decision_rules={
                "approval_choices": ["yes"],
                "min_counted_weight": 15,
                "approval_basis_points": 6000,
            },
        )
        code, out, err = self.run_cli(payload)
        self.assertEqual(code, 0)
        self.assertEqual(err, "")
        self.assertTrue(out["quorum_met"])
        self.assertTrue(out["approval_met"])
        self.assertEqual(out["decision"], "approved")
        self.assertEqual(
            list(out.keys())[-3:],
            ["quorum_met", "approval_met", "decision"],
        )

    def test_cli_without_rules_shape_unchanged(self):
        code, out, err = self.run_cli(base_input(votes=[{"voter": "a", "choice": "yes"}]))
        self.assertEqual(code, 0)
        for field in ("quorum_met", "approval_met", "decision"):
            self.assertNotIn(field, out)

    def test_cli_invalid_rules_exit_two(self):
        bad_cases = [
            {"approval_choices": ["yes"], "min_counted_weight": 1,
             "approval_basis_points": 0},
            {"approval_choices": ["nope"], "min_counted_weight": 1,
             "approval_basis_points": 5000},
            {"approval_choices": [], "min_counted_weight": 1,
             "approval_basis_points": 5000},
            {"approval_choices": ["yes"], "min_counted_weight": -1,
             "approval_basis_points": 5000},
            ["yes", 1, 5000],
        ]
        for rules in bad_cases:
            with self.subTest(rules=rules):
                payload = base_input(
                    votes=[{"voter": "a", "choice": "yes"}],
                    decision_rules=rules,
                )
                code, out, err = self.run_cli(payload)
                self.assertEqual(code, 2)
                self.assertEqual(err, "")
                self.assertEqual(out["error"], "InvalidInputError")


class ReviewPackageTests(unittest.TestCase):
    """build_review_package：独立结果复核包装配。"""

    def build(self, claimed_result, **overrides):
        data = {
            "proposal_id": "p1",
            "snapshot_block": 42,
            "snapshot": {"a": 2, "b": 3, "c": 4, "d": 5, "e": 7},
            "votes": [{"voter": "c", "choice": "yes"}],
            "delegations": {"a": "b", "b": "c", "d": "c"},
        }
        data.update(overrides)
        return gt.build_review_package(
            data["proposal_id"],
            data["snapshot_block"],
            data["snapshot"],
            data["votes"],
            data["delegations"],
            claimed_result,
        )

    def claimed(self, **overrides):
        claim = {
            "proposal_id": "p1",
            "snapshot_block": 42,
            "results_by_choice": {"yes": 14},
            "direct_participated_weight": 4,
            "delegated_weight": 10,
            "non_participated_weight": 7,
        }
        claim.update(overrides)
        return claim

    def test_matched_success_package(self):
        pkg = self.build(self.claimed())
        self.assertEqual(pkg["review_status"], "matched")
        self.assertEqual(pkg["field_differences"], [])
        self.assertTrue(pkg["weight_conservation_holds"])
        self.assertEqual(pkg["results_by_choice"], {"yes": 14})
        self.assertEqual(pkg[  # a2 + b3 + d5 经委托汇入已投票受托人 c
            "effective_delegations"
        ], [
            {"delegator": "a", "trustee": "c", "weight": 2},
            {"delegator": "b", "trustee": "c", "weight": 3},
            {"delegator": "d", "trustee": "c", "weight": 5},
        ])
        # 直接参与＝已投票受托人本人 4；未参与＝独立未投票者 e 的 7。
        self.assertEqual(pkg["direct_participated_weight"], 4)
        self.assertEqual(pkg["delegated_weight"], 10)
        self.assertEqual(pkg["non_participated_weight"], 7)

    def test_package_field_order_is_stable(self):
        pkg = self.build(self.claimed())
        self.assertEqual(
            list(pkg.keys()),
            [
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
            ],
        )

    def test_multiple_choices_sorted_and_accumulated(self):
        # c 支持、e 反对；a/b/d 的委托权重随 c 进 yes；e 是独立账户。
        pkg = self.build(
            self.claimed(
                results_by_choice={"no": 7, "yes": 14},
                direct_participated_weight=11,
                non_participated_weight=0,
            ),
            votes=[
                {"voter": "c", "choice": "yes"},
                {"voter": "e", "choice": "no"},
            ],
        )
        # 选项按词法排序确定输出，e 由未参与转为直接参与。
        self.assertEqual(list(pkg["results_by_choice"].keys()), ["no", "yes"])
        self.assertEqual(pkg["results_by_choice"], {"no": 7, "yes": 14})
        self.assertEqual(pkg["direct_participated_weight"], 11)
        self.assertEqual(pkg["non_participated_weight"], 0)
        self.assertEqual(pkg["review_status"], "matched")

    def test_abstain_like_choice_is_counted_as_participation(self):
        # 现有语义：弃权也是一个选项，票权计入该选项且属于已参与票权；
        # 选项集合只由投票记录派生，未被投出的选项不出现在结果中。
        claim = self.claimed(
            results_by_choice={"abstain": 14},
            direct_participated_weight=4,
            non_participated_weight=7,
        )
        pkg = self.build(
            claim,
            votes=[{"voter": "c", "choice": "abstain"}],
        )
        self.assertEqual(pkg["results_by_choice"], {"abstain": 14})
        self.assertEqual(pkg["review_status"], "matched")

    def test_empty_votes_zero_tally_package(self):
        claim = {
            "proposal_id": "p1",
            "snapshot_block": 42,
            "results_by_choice": {},
            "direct_participated_weight": 0,
            "delegated_weight": 0,
            "non_participated_weight": 21,
        }
        pkg = self.build(claim, votes=[], delegations={})
        self.assertEqual(pkg["review_status"], "matched")
        self.assertEqual(pkg["results_by_choice"], {})
        self.assertEqual(pkg["effective_delegations"], [])
        self.assertEqual(pkg[  # 全部账户保留为未参与，不丢弃、不分配
            "non_participated_weight"
        ], 21)
        self.assertTrue(pkg["weight_conservation_holds"])

    def test_non_delegated_non_voting_accounts_kept_unparticipated(self):
        # a->b，两人都没投票；独立账户 c/d/e 也没投。
        claim = self.claimed(
            results_by_choice={},
            direct_participated_weight=0,
            delegated_weight=0,
            non_participated_weight=21,
        )
        pkg = self.build(claim, votes=[], delegations={"a": "b"})
        self.assertEqual(pkg["review_status"], "matched")
        # 委托结构存在但未送达任何已投票受托人，不进有效委托明细。
        self.assertEqual(pkg["effective_delegations"], [])
        self.assertEqual(pkg["non_participated_weight"], 21)

    def test_delegated_weight_to_nonvoting_trustee_is_unparticipated(self):
        # a->b->c，无人投票：委托权重不达已投票受托人，全部未参与。
        claim = self.claimed(
            results_by_choice={},
            direct_participated_weight=0,
            delegated_weight=0,
            non_participated_weight=9,
        )
        pkg = self.build(
            claim,
            snapshot={"a": 2, "b": 3, "c": 4},
            votes=[],
            delegations={"a": "b", "b": "c"},
        )
        self.assertEqual(pkg["review_status"], "matched")
        self.assertEqual(pkg["effective_delegations"], [])

    def test_zero_weight_accounts_and_total(self):
        # 零权重账户可投票（直接参与为 0），守恒仍成立；全部零权重时零票包有效。
        claim = {
            "proposal_id": "p1",
            "snapshot_block": 42,
            "results_by_choice": {"yes": 0},
            "direct_participated_weight": 0,
            "delegated_weight": 0,
            "non_participated_weight": 0,
        }
        pkg = self.build(
            claim,
            snapshot={"a": 0, "b": 0},
            votes=[{"voter": "a", "choice": "yes"}],
            delegations={},
        )
        self.assertEqual(pkg["review_status"], "matched")
        self.assertTrue(pkg["weight_conservation_holds"])

    def test_mismatched_reports_per_field_differences_in_field_order(self):
        claim = self.claimed(
            proposal_id="OTHER",
            results_by_choice={"yes": 13},
            delegated_weight=9,
        )
        pkg = self.build(claim)
        self.assertEqual(pkg["review_status"], "mismatched")
        self.assertEqual(
            [d["field"] for d in pkg["field_differences"]],
            ["proposal_id", "results_by_choice", "delegated_weight"],
        )
        first = pkg["field_differences"][0]
        self.assertEqual(first["computed"], "p1")
        self.assertEqual(first["claimed"], "OTHER")
        rc = next(d for d in pkg["field_differences"] if d["field"] == "results_by_choice")
        self.assertEqual(rc["computed"], {"yes": 14})
        self.assertEqual(rc["claimed"], {"yes": 13})
        dw = next(d for d in pkg["field_differences"] if d["field"] == "delegated_weight")
        self.assertEqual(dw["computed"], 10)
        self.assertEqual(dw["claimed"], 9)
        # 一致的字段不出现在差异中。
        fields = {d["field"] for d in pkg["field_differences"]}
        self.assertNotIn("direct_participated_weight", fields)
        self.assertNotIn("snapshot_block", fields)

    def test_missing_claimed_field_is_difference_not_exception(self):
        claim = self.claimed()
        del claim["delegated_weight"]
        pkg = self.build(claim)
        self.assertEqual(pkg["review_status"], "mismatched")
        diff = next(d for d in pkg["field_differences"] if d["field"] == "delegated_weight")
        self.assertEqual(diff["computed"], 10)
        self.assertIsNone(diff["claimed"])

    def test_results_by_choice_extra_choice_is_mismatch(self):
        # 计算结果只有 yes；待核对多出 abstain（语义不同，不是"未知字段"——
        # 选项名不属于顶层字段白名单，未知字段只针对 claimed 顶层）。
        pkg = self.build(self.claimed(results_by_choice={"yes": 14, "abstain": 0}))
        self.assertEqual(pkg["review_status"], "mismatched")
        self.assertEqual(
            [d["field"] for d in pkg["field_differences"]],
            ["results_by_choice"],
        )
        self.assertEqual(
            pkg["field_differences"][0]["claimed"],
            {"abstain": 0, "yes": 14},
        )

    def test_claimed_choice_order_normalized_in_difference(self):
        # 多选项时待核对映射以反序构造；比对按选项名归一化排序，内容一致即 matched。
        rev_claim = {
            "proposal_id": "p1",
            "snapshot_block": 42,
            "results_by_choice": {"yes": 14, "no": 7},  # 与计算顺序 no,yes 相反
            "direct_participated_weight": 11,
            "delegated_weight": 10,
            "non_participated_weight": 0,
        }
        pkg = self.build(
            rev_claim,
            votes=[
                {"voter": "c", "choice": "yes"},
                {"voter": "e", "choice": "no"},
            ],
        )
        self.assertEqual(pkg["review_status"], "matched")
        self.assertEqual(pkg["field_differences"], [])

    def test_deterministic_across_repeated_calls(self):
        claim = self.claimed()
        pkg1 = self.build(json.loads(json.dumps(claim)))
        pkg2 = self.build(json.loads(json.dumps(claim)))
        self.assertEqual(
            json.dumps(pkg1, ensure_ascii=False, sort_keys=False),
            json.dumps(pkg2, ensure_ascii=False, sort_keys=False),
        )
        # 即使输入的 delegations/votes 顺序不同，有效委托按委托人排序。
        pkg3 = self.build(
            json.loads(json.dumps(claim)),
            delegations={"d": "c", "b": "c", "a": "b"},
            votes=[{"voter": "c", "choice": "yes"}],
        )
        self.assertEqual(
            [d["delegator"] for d in pkg3["effective_delegations"]],
            ["a", "b", "d"],
        )

    def test_package_delivered_only_via_return_value(self):
        # 调用前后模块自身不留状态；结果可 JSON 序列化（纯返回值交付的可观察保证）。
        claim = json.loads(json.dumps(self.claimed()))
        snapshot = dict(a=2, b=3, c=4, d=5, e=7)
        votes = [{"voter": "c", "choice": "yes"}]
        delegations = {"a": "b", "b": "c", "d": "c"}
        before = {k: v for k, v in gt.__dict__.items() if not k.startswith("_")}
        pkg = gt.build_review_package("p1", 42, snapshot, votes, delegations, claim)
        after = {k: v for k, v in gt.__dict__.items() if not k.startswith("_")}
        self.assertEqual(set(before), set(after))
        json.dumps(pkg, ensure_ascii=False)

    def test_effective_delegations_weights_sum_to_delegated_weight(self):
        pkg = self.build(self.claimed())
        self.assertEqual(
            sum(item["weight"] for item in pkg["effective_delegations"]),
            pkg["delegated_weight"],
        )
        # 明细受托人全部是已投票受托人，且没有自委托条目。
        for item in pkg["effective_delegations"]:
            self.assertNotEqual(item["delegator"], item["trustee"])
            self.assertEqual(item["trustee"], "c")

    def test_empty_claimed_zero_tally_lists_every_field(self):
        # 空待核对结果 + 空投票：零票包有效，六个公开字段全部逐项列差异。
        pkg = gt.build_review_package(
            "p1", 0, {"a": 1, "b": 2}, [], {}, {}
        )
        self.assertEqual(pkg["review_status"], "mismatched")
        self.assertEqual(
            [d["field"] for d in pkg["field_differences"]],
            [
                "proposal_id",
                "snapshot_block",
                "results_by_choice",
                "direct_participated_weight",
                "delegated_weight",
                "non_participated_weight",
            ],
        )
        self.assertEqual(
            [d["claimed"] for d in pkg["field_differences"]],
            [None] * 6,
        )

    def test_conservation_flag_reflects_the_boolean_identity(self):
        pkg = self.build(self.claimed())
        expected_total = 2 + 3 + 4 + 5 + 7
        self.assertTrue(pkg["weight_conservation_holds"])
        self.assertEqual(
            pkg["direct_participated_weight"]
            + pkg["delegated_weight"]
            + pkg["non_participated_weight"],
            expected_total,
        )


class ReviewPackageErrorTests(unittest.TestCase):
    def build(self, **overrides):
        args = dict(
            proposal_id="p1",
            snapshot_block=42,
            snapshot={"a": 2, "b": 3, "c": 4},
            votes=[],
            delegations={},
            claimed_result={},
        )
        args.update(overrides)
        return gt.build_review_package(**args)

    def test_snapshot_integrity_errors(self):
        with self.assertRaises(gt.SnapshotIntegrityError):
            self.build(proposal_id=7)
        with self.assertRaises(gt.SnapshotIntegrityError):
            self.build(snapshot_block=-1)
        with self.assertRaises(gt.SnapshotIntegrityError):
            self.build(snapshot_block=True)
        with self.assertRaises(gt.SnapshotIntegrityError):
            self.build(snapshot="not-an-object")
        with self.assertRaises(gt.SnapshotIntegrityError):
            self.build(snapshot={1: 5})
        for bad in (-1, 1.5, "3", None, True):
            with self.subTest(bad=bad):
                with self.assertRaises(gt.SnapshotIntegrityError):
                    self.build(snapshot={"a": bad})

    def test_ballot_validation_errors(self):
        base_snapshot = {"a": 2, "b": 3, "c": 4}
        with self.assertRaises(gt.BallotValidationError):
            self.build(votes="nope")
        with self.assertRaises(gt.BallotValidationError):
            self.build(votes=[{}])
        with self.assertRaises(gt.BallotValidationError):
            self.build(votes=[{"choice": "yes"}])
        with self.assertRaises(gt.BallotValidationError):
            self.build(votes=[{"voter": "a"}])
        with self.assertRaises(gt.BallotValidationError):
            self.build(votes=[{"voter": 9, "choice": "yes"}])
        with self.assertRaises(gt.BallotValidationError):
            self.build(votes=[{"voter": "a", "choice": 7}])
        with self.assertRaises(gt.BallotValidationError):
            self.build(votes=[{"voter": "z", "choice": "yes"}])
        with self.assertRaises(gt.BallotValidationError):
            self.build(
                votes=[
                    {"voter": "a", "choice": "yes"},
                    {"voter": "a", "choice": "no"},
                ]
            )
        # 已委托账户直接投票：直接投票与委托同时使用票权。
        with self.assertRaises(gt.BallotValidationError):
            self.build(
                snapshot=base_snapshot,
                delegations={"a": "b"},
                votes=[{"voter": "a", "choice": "yes"}],
            )

    def test_delegation_conflict_errors(self):
        with self.assertRaises(gt.DelegationConflictError):
            self.build(delegations="nope")
        with self.assertRaises(gt.DelegationConflictError):
            self.build(delegations={1: "a"})
        with self.assertRaises(gt.DelegationConflictError):
            self.build(delegations={"a": 1})
        with self.assertRaises(gt.DelegationConflictError):
            self.build(delegations={"z": "a"})
        with self.assertRaises(gt.DelegationConflictError):
            self.build(delegations={"a": "z"})
        with self.assertRaises(gt.DelegationConflictError):
            self.build(delegations={"a": "a"})
        with self.assertRaises(gt.DelegationConflictError):
            self.build(delegations={"a": "b", "b": "a"})
        with self.assertRaises(gt.DelegationConflictError):
            self.build(
                delegations={"a": "b", "b": "c", "c": "a"}
            )

    def test_non_mapping_delegations_rejected(self):
        # 委托关系必须是 account -> trustee 的映射；列表等容器（可表达
        # 同一委托方指向多个受托人）在结构校验阶段直接拒绝。
        with self.assertRaises(gt.DelegationConflictError):
            self.build(delegations=[("a", "b"), ("a", "c")])

    def test_claimed_leaf_value_of_wrong_type_is_a_difference(self):
        # 叶子值类型错误不算结构契约违反：逐项差异返回，不抛异常。
        correct = {
            "proposal_id": "p1",
            "snapshot_block": 42,
            "results_by_choice": {"yes": 14},
            "direct_participated_weight": 4,
            "delegated_weight": 10,
            "non_participated_weight": 7,
        }
        kwargs = dict(
            snapshot={"a": 2, "b": 3, "c": 4, "d": 5, "e": 7},
            delegations={"a": "b", "b": "c", "d": "c"},
            votes=[{"voter": "c", "choice": "yes"}],
        )
        for bad_value in (-1, 1.5, "3", True, None):
            with self.subTest(bad_value=bad_value):
                claim = json.loads(json.dumps(correct))
                claim["delegated_weight"] = bad_value
                pkg = self.build(claimed_result=claim, **kwargs)
                self.assertEqual(pkg["review_status"], "mismatched")
                diff = next(
                    d for d in pkg["field_differences"]
                    if d["field"] == "delegated_weight"
                )
                self.assertEqual(diff["computed"], 10)
                self.assertEqual(diff["claimed"], bad_value)

    def test_claimed_result_validation_errors(self):
        with self.assertRaises(gt.ClaimedResultValidationError):
            self.build(claimed_result=[])
        with self.assertRaises(gt.ClaimedResultValidationError):
            self.build(claimed_result="x")
        with self.assertRaises(gt.ClaimedResultValidationError):
            self.build(claimed_result=None)
        with self.assertRaises(gt.ClaimedResultValidationError):
            self.build(claimed_result={"unknown_field": 1})
        with self.assertRaises(gt.ClaimedResultValidationError):
            self.build(claimed_result={"review_status": "matched"})
        # results_by_choice 必须是字符串键的对象；叶子权重类型错是差异。
        with self.assertRaises(gt.ClaimedResultValidationError):
            self.build(claimed_result={"results_by_choice": []})
        with self.assertRaises(gt.ClaimedResultValidationError):
            self.build(claimed_result={"results_by_choice": "yes"})

    def test_claimed_results_by_choice_leaf_wrong_type_is_difference(self):
        base = dict(
            snapshot={"a": 2, "b": 3, "c": 4},
            delegations={"a": "b", "b": "c"},
            votes=[{"voter": "c", "choice": "yes"}],
        )
        correct = {
            "proposal_id": "p1",
            "snapshot_block": 42,
            "results_by_choice": {"yes": 9},
            "direct_participated_weight": 4,
            "delegated_weight": 5,
            "non_participated_weight": 0,
        }
        for bad in (-1, "9", 9.0, True, None):
            with self.subTest(bad=bad):
                claim = json.loads(json.dumps(correct))
                claim["results_by_choice"]["yes"] = bad
                pkg = self.build(claimed_result=claim, **base)
                self.assertEqual(pkg["review_status"], "mismatched")
                diff = next(
                    d for d in pkg["field_differences"]
                    if d["field"] == "results_by_choice"
                )
                self.assertEqual(diff["claimed"], {"yes": bad})
                self.assertEqual(diff["computed"], {"yes": 9})

    def test_error_types_are_tally_errors_and_distinct(self):
        for cls in (
            gt.SnapshotIntegrityError,
            gt.BallotValidationError,
            gt.DelegationConflictError,
            gt.ClaimedResultValidationError,
        ):
            self.assertTrue(issubclass(cls, gt.TallyError))
        self.assertIsNot(gt.SnapshotIntegrityError, gt.BallotValidationError)

    def test_partial_claimed_result_with_unknown_field_still_raises(self):
        # 未知字段优先报错，即使其他字段都正确。
        with self.assertRaises(gt.ClaimedResultValidationError):
            self.build(
                snapshot={"a": 2, "b": 3, "c": 4, "d": 5, "e": 7},
                delegations={"a": "b", "b": "c", "d": "c"},
                votes=[{"voter": "c", "choice": "yes"}],
                claimed_result={"proposal_id": "p1", "bogus": 0},
            )


class ReviewOverrideTests(unittest.TestCase):
    """review：delegation_override 覆盖票的归属与守恒。"""

    def build(self, claimed_result, **overrides):
        data = {
            "proposal_id": "p1",
            "snapshot_block": 42,
            "snapshot": {"a": 2, "b": 3, "c": 4},
            "votes": [
                {"voter": "b", "choice": "no", "delegation_override": True},
                {"voter": "c", "choice": "yes"},
            ],
            "delegations": {"a": "b", "b": "c"},
        }
        data.update(overrides)
        return gt.build_review_package(
            data["proposal_id"], data["snapshot_block"], data["snapshot"],
            data["votes"], data["delegations"], claimed_result,
        )

    def claimed(self, **overrides):
        claim = {
            "proposal_id": "p1",
            "snapshot_block": 42,
            "results_by_choice": {"no": 3, "yes": 6},
            "direct_participated_weight": 7,
            "delegated_weight": 2,
            "non_participated_weight": 0,
        }
        claim.update(overrides)
        return claim

    def test_override_own_weight_direct_upstream_still_delegated(self):
        # a->b->c：b 覆盖投本人 3（direct）；a 的 2 仍转发到已投票受托人 c
        # （delegated）；c 本人 4 是 direct。
        pkg = self.build(self.claimed())
        self.assertEqual(pkg["review_status"], "matched")
        self.assertTrue(pkg["weight_conservation_holds"])
        self.assertEqual(pkg["results_by_choice"], {"no": 3, "yes": 6})
        self.assertEqual(pkg["direct_participated_weight"], 7)
        self.assertEqual(pkg["delegated_weight"], 2)
        self.assertEqual(pkg["non_participated_weight"], 0)
        # 覆盖者 b 本人权重不列入委托明细；实际转发的上游 a 按来源列入。
        self.assertEqual(
            pkg["effective_delegations"],
            [{"delegator": "a", "trustee": "c", "weight": 2}],
        )

    def test_order_does_not_affect_deduction(self):
        # 受托人票在前、覆盖票在后，结果与顺序无关。
        pkg = self.build(
            self.claimed(results_by_choice={"no": 2, "yes": 7},
                         direct_participated_weight=6,
                         delegated_weight=3),
            votes=[
                {"voter": "c", "choice": "yes"},
                {"voter": "a", "choice": "no", "delegation_override": True},
            ],
        )
        self.assertEqual(pkg["review_status"], "matched")
        self.assertEqual(pkg["results_by_choice"], {"no": 2, "yes": 7})
        # a 覆盖抽走 2；b 的 3 仍到 c：受托人 c 合并 4+3=7。
        self.assertEqual(pkg["direct_participated_weight"], 6)  # c4 + a2
        self.assertEqual(pkg["delegated_weight"], 3)
        self.assertEqual(
            pkg["effective_delegations"],
            [{"delegator": "b", "trustee": "c", "weight": 3}],
        )

    def test_override_without_trustee_vote_rest_is_unparticipated(self):
        # a 覆盖投出本人 2；受托人 b 未投票，b 的 3 保留为未参与。
        pkg = gt.build_review_package(
            "p1", 42, {"a": 2, "b": 3},
            [{"voter": "a", "choice": "yes", "delegation_override": True}],
            {"a": "b"},
            {
                "proposal_id": "p1", "snapshot_block": 42,
                "results_by_choice": {"yes": 2},
                "direct_participated_weight": 2,
                "delegated_weight": 0,
                "non_participated_weight": 3,
            },
        )
        self.assertEqual(pkg["review_status"], "matched")
        self.assertEqual(pkg["results_by_choice"], {"yes": 2})
        self.assertEqual(pkg["effective_delegations"], [])
        self.assertTrue(pkg["weight_conservation_holds"])

    def test_multiple_overrides_each_counted_direct(self):
        # a、c 各自覆盖投本人；受托人 d 投票只持本人 5；b->d 的 3 仍受托。
        pkg = gt.build_review_package(
            "p1", 42,
            {"a": 2, "b": 3, "c": 4, "d": 5},
            [
                {"voter": "a", "choice": "yes", "delegation_override": True},
                {"voter": "c", "choice": "no", "delegation_override": True},
                {"voter": "d", "choice": "yes"},
            ],
            {"a": "d", "b": "d", "c": "d"},
            {
                "proposal_id": "p1", "snapshot_block": 42,
                "results_by_choice": {"no": 4, "yes": 10},
                "direct_participated_weight": 11,
                "delegated_weight": 3,
                "non_participated_weight": 0,
            },
        )
        self.assertEqual(pkg["review_status"], "matched")
        self.assertEqual(
            pkg["effective_delegations"],
            [{"delegator": "b", "trustee": "d", "weight": 3}],
        )

    def test_override_zero_weight_is_direct_zero(self):
        pkg = gt.build_review_package(
            "p1", 42, {"a": 0, "b": 5},
            [
                {"voter": "a", "choice": "yes", "delegation_override": True},
                {"voter": "b", "choice": "no"},
            ],
            {"a": "b"},
            {
                "proposal_id": "p1", "snapshot_block": 42,
                "results_by_choice": {"no": 5, "yes": 0},
                "direct_participated_weight": 5,
                "delegated_weight": 0,
                "non_participated_weight": 0,
            },
        )
        self.assertEqual(pkg["review_status"], "matched")
        self.assertTrue(pkg["weight_conservation_holds"])

    def test_override_false_delegated_account_still_rejected(self):
        with self.assertRaises(gt.BallotValidationError):
            self.build(
                {},
                votes=[{"voter": "b", "choice": "yes",
                        "delegation_override": False}],
            )

    def test_override_without_delegation_is_ballot_error(self):
        with self.assertRaises(gt.BallotValidationError):
            gt.build_review_package(
                "p1", 0, {"a": 1},
                [{"voter": "a", "choice": "yes",
                  "delegation_override": True}], {}, {},
            )

    def test_override_duplicate_voter_rejected(self):
        with self.assertRaises(gt.BallotValidationError):
            self.build(
                {},
                votes=[
                    {"voter": "b", "choice": "yes",
                     "delegation_override": True},
                    {"voter": "b", "choice": "no",
                     "delegation_override": True},
                ],
            )

    def test_override_non_boolean_is_ballot_error(self):
        for value in ("true", 1, 0, None, []):
            with self.subTest(value=value):
                with self.assertRaises(gt.BallotValidationError):
                    self.build(
                        {},
                        votes=[{"voter": "b", "choice": "yes",
                               "delegation_override": value}],
                    )

    def test_misclaimed_override_attribution_is_mismatched(self):
        # 把覆盖者 b 的本人 3 错记为委托送达受托人：delegated 应为 2 非 5。
        claim = self.claimed(delegated_weight=5, direct_participated_weight=4)
        pkg = self.build(claim)
        self.assertEqual(pkg["review_status"], "mismatched")
        fields = {d["field"]: d for d in pkg["field_differences"]}
        self.assertEqual(fields["delegated_weight"]["computed"], 2)
        self.assertEqual(fields["delegated_weight"]["claimed"], 5)
        self.assertEqual(fields["direct_participated_weight"]["computed"], 7)


class ReviewChoicesTests(unittest.TestCase):
    """review：可选 choices 给出完整选项集合。"""

    BASE = dict(
        proposal_id="p1",
        snapshot_block=42,
        snapshot={"a": 2, "b": 3, "c": 4, "d": 5, "e": 7},
        votes=[{"voter": "c", "choice": "yes"}],
        delegations={"a": "b", "b": "c", "d": "c"},
    )

    def claim(self, results, **overrides):
        claim = {
            "proposal_id": "p1", "snapshot_block": 42,
            "results_by_choice": results,
            "direct_participated_weight": 4,
            "delegated_weight": 10,
            "non_participated_weight": 7,
        }
        claim.update(overrides)
        return claim

    def build(self, choices, claimed_result, **overrides):
        args = dict(self.BASE)
        args.update(overrides)
        return gt.build_review_package(
            args["proposal_id"], args["snapshot_block"], args["snapshot"],
            args["votes"], args["delegations"], claimed_result,
            choices=choices,
        )

    def test_unvoted_choices_listed_as_zero_and_sorted(self):
        pkg = self.build(
            ["yes", "no", "abstain"],
            self.claim({"abstain": 0, "no": 0, "yes": 14}),
        )
        self.assertEqual(pkg["review_status"], "matched")
        self.assertEqual(
            list(pkg["results_by_choice"].keys()),
            ["abstain", "no", "yes"],
        )
        self.assertEqual(
            pkg["results_by_choice"],
            {"abstain": 0, "no": 0, "yes": 14},
        )

    def test_claimed_missing_zero_choice_is_mismatch(self):
        pkg = self.build(["yes", "no"], self.claim({"yes": 14}))
        self.assertEqual(pkg["review_status"], "mismatched")
        diff = next(
            d for d in pkg["field_differences"]
            if d["field"] == "results_by_choice"
        )
        self.assertEqual(diff["computed"], {"no": 0, "yes": 14})
        self.assertEqual(diff["claimed"], {"yes": 14})

    def test_vote_choice_outside_choices_is_ballot_error(self):
        with self.assertRaises(gt.BallotValidationError):
            self.build(
                ["yes", "no"], {},
                votes=[{"voter": "c", "choice": "abstain"}],
            )

    def test_invalid_choices_raise_invalid_input(self):
        kwargs = dict(self.BASE)
        for bad in ([], ["yes", "yes"], ["yes", 3], "yes"):
            with self.subTest(bad=bad):
                with self.assertRaises(gt.InvalidInputError):
                    gt.build_review_package(
                        kwargs["proposal_id"], kwargs["snapshot_block"],
                        kwargs["snapshot"], kwargs["votes"],
                        kwargs["delegations"], {}, choices=bad,
                    )

    def test_choices_empty_votes_gives_zero_results_for_all_choices(self):
        pkg = gt.build_review_package(
            "p1", 0, {"a": 1, "b": 2}, [], {},
            {
                "proposal_id": "p1", "snapshot_block": 0,
                "results_by_choice": {"no": 0, "yes": 0},
                "direct_participated_weight": 0,
                "delegated_weight": 0,
                "non_participated_weight": 3,
            },
            choices=["yes", "no"],
        )
        self.assertEqual(pkg["review_status"], "matched")
        self.assertEqual(
            pkg["results_by_choice"], {"no": 0, "yes": 0}
        )


class ReviewDecisionRulesTests(unittest.TestCase):
    """review：decision_rules 门槛判定的独立核对。"""

    RULES = {
        "approval_choices": ["yes"],
        "min_counted_weight": 5,
        "approval_basis_points": 6000,
    }

    def build(self, claimed_result, rules=None, **overrides):
        data = {
            "proposal_id": "p1",
            "snapshot_block": 42,
            "snapshot": {"a": 2, "b": 3, "c": 4},
            "votes": [
                {"voter": "b", "choice": "no", "delegation_override": True},
                {"voter": "c", "choice": "yes"},
            ],
            "delegations": {"a": "b", "b": "c"},
            "choices": ["yes", "no"],
        }
        data.update(overrides)
        return gt.build_review_package(
            data["proposal_id"], data["snapshot_block"], data["snapshot"],
            data["votes"], data["delegations"], claimed_result,
            choices=data["choices"],
            decision_rules=self.RULES if rules is None else rules,
        )

    def claim(self, **overrides):
        claim = {
            "proposal_id": "p1",
            "snapshot_block": 42,
            "results_by_choice": {"no": 3, "yes": 6},
            "direct_participated_weight": 7,
            "delegated_weight": 2,
            "non_participated_weight": 0,
            "quorum_met": True,
            "approval_met": True,
            "decision": "approved",
        }
        claim.update(overrides)
        return claim

    def test_decision_fields_appended_and_matched(self):
        pkg = self.build(self.claim())
        self.assertEqual(pkg["review_status"], "matched")
        self.assertEqual(pkg["field_differences"], [])
        self.assertEqual(
            list(pkg.keys())[-3:],
            ["quorum_met", "approval_met", "decision"],
        )
        self.assertTrue(pkg["quorum_met"])
        self.assertTrue(pkg["approval_met"])
        self.assertEqual(pkg["decision"], "approved")

    def test_rejected_decision_matched(self):
        # 6/9 赞成 ≈ 6667bps：门槛 7000 -> rejected。
        pkg = self.build(
            self.claim(approval_met=False, decision="rejected"),
            rules={
                "approval_choices": ["yes"],
                "min_counted_weight": 5,
                "approval_basis_points": 7000,
            },
        )
        self.assertEqual(pkg["review_status"], "matched")
        self.assertFalse(pkg["approval_met"])
        self.assertEqual(pkg["decision"], "rejected")

    def test_no_quorum_matched(self):
        pkg = self.build(
            self.claim(
                results_by_choice={"no": 0, "yes": 0},
                direct_participated_weight=0,
                delegated_weight=0,
                non_participated_weight=9,
                quorum_met=False,
                approval_met=True,
                decision="no_quorum",
            ),
            rules={
                "approval_choices": ["yes"],
                "min_counted_weight": 0,
                "approval_basis_points": 5000,
            },
            votes=[],
        )
        self.assertEqual(pkg["review_status"], "matched")
        self.assertFalse(pkg["quorum_met"])
        self.assertEqual(pkg["decision"], "no_quorum")

    def test_each_gate_field_mismatch_reports_computed_and_claimed(self):
        for field, bad in (
            ("quorum_met", False),
            ("approval_met", False),
            ("decision", "rejected"),
        ):
            with self.subTest(field=field):
                pkg = self.build(self.claim(**{field: bad}))
                self.assertEqual(pkg["review_status"], "mismatched")
                diff = next(
                    d for d in pkg["field_differences"] if d["field"] == field
                )
                self.assertEqual(diff["claimed"], bad)
                self.assertNotEqual(diff["computed"], bad)

    def test_missing_gate_field_is_a_difference(self):
        claim = self.claim()
        del claim["decision"]
        pkg = self.build(claim)
        self.assertEqual(pkg["review_status"], "mismatched")
        diff = next(
            d for d in pkg["field_differences"] if d["field"] == "decision"
        )
        self.assertEqual(diff["computed"], "approved")
        self.assertIsNone(diff["claimed"])

    def test_rules_require_choices(self):
        with self.assertRaises(gt.InvalidInputError):
            gt.build_review_package(
                "p1", 42, {"a": 1}, [], {}, {},
                decision_rules=self.RULES,
            )

    def test_invalid_rules_raise_invalid_input(self):
        for bad in (
            [],
            {"approval_choices": ["nope"], "min_counted_weight": 1,
             "approval_basis_points": 5000},
            {"approval_choices": ["yes"], "min_counted_weight": -1,
             "approval_basis_points": 5000},
            {"approval_choices": ["yes"], "min_counted_weight": 1,
             "approval_basis_points": 0},
            {"approval_choices": [], "min_counted_weight": 1,
             "approval_basis_points": 5000},
            {"approval_choices": ["yes"], "min_counted_weight": 1},
        ):
            with self.subTest(bad=bad):
                with self.assertRaises(gt.InvalidInputError):
                    self.build({}, rules=bad)

    def test_gate_fields_without_rules_raise_claimed_result_error(self):
        for field, value in (
            ("quorum_met", True),
            ("approval_met", False),
            ("decision", "approved"),
        ):
            with self.subTest(field=field):
                with self.assertRaises(gt.ClaimedResultValidationError):
                    gt.build_review_package(
                        "p1", 42, {"a": 1}, [], {},
                        {field: value},
                    )

    def test_thresholds_count_delegated_and_override_weights(self):
        # yes6+no3，counted 9，门槛 9 且赞成率 6667bps：approved。
        pkg = self.build(
            self.claim(),
            rules={
                "approval_choices": ["yes"],
                "min_counted_weight": 9,
                "approval_basis_points": 6000,
            },
        )
        self.assertEqual(pkg["review_status"], "matched")
        self.assertEqual(pkg["decision"], "approved")


class CliReviewExtensionTests(unittest.TestCase):
    """review 子命令透传 choices / delegation_override / decision_rules。"""

    def run_cli(self, payload):
        proc = subprocess.run(
            [sys.executable, str(MODULE), "review"],
            input=json.dumps(payload),
            capture_output=True, text=True,
        )
        return proc.returncode, json.loads(proc.stdout), proc.stderr

    def payload(self, **overrides):
        data = {
            "proposal_id": "p1",
            "snapshot_block": 42,
            "snapshot": {"a": 2, "b": 3, "c": 4},
            "votes": [
                {"voter": "b", "choice": "no", "delegation_override": True},
                {"voter": "c", "choice": "yes"},
            ],
            "delegations": {"a": "b", "b": "c"},
            "choices": ["yes", "no"],
            "decision_rules": {
                "approval_choices": ["yes"],
                "min_counted_weight": 5,
                "approval_basis_points": 6000,
            },
            "claimed_result": {
                "proposal_id": "p1",
                "snapshot_block": 42,
                "results_by_choice": {"no": 3, "yes": 6},
                "direct_participated_weight": 7,
                "delegated_weight": 2,
                "non_participated_weight": 0,
                "quorum_met": True,
                "approval_met": True,
                "decision": "approved",
            },
        }
        data.update(overrides)
        return data

    def test_full_extension_matched(self):
        code, out, err = self.run_cli(self.payload())
        self.assertEqual(code, 0)
        self.assertEqual(err, "")
        self.assertEqual(out["review_status"], "matched")
        self.assertEqual(out["effective_delegations"],
                         [{"delegator": "a", "trustee": "c", "weight": 2}])
        self.assertEqual(
            list(out.keys())[-3:],
            ["quorum_met", "approval_met", "decision"],
        )

    def test_override_ballot_error(self):
        payload = self.payload()
        payload["votes"][0]["delegation_override"] = "true"
        code, out, err = self.run_cli(payload)
        self.assertEqual(code, 2)
        self.assertEqual(out["error"], "BallotValidationError")

    def test_rules_without_choices_is_invalid_input(self):
        payload = self.payload()
        del payload["choices"]
        code, out, err = self.run_cli(payload)
        self.assertEqual(code, 2)
        self.assertEqual(out["error"], "InvalidInputError")

    def test_gate_field_without_rules_is_claimed_error(self):
        payload = self.payload()
        del payload["decision_rules"]
        code, out, err = self.run_cli(payload)
        self.assertEqual(code, 2)
        self.assertEqual(out["error"], "ClaimedResultValidationError")

    def test_explicit_null_optional_fields_rejected(self):
        payload = self.payload()
        payload["choices"] = None
        code, out, err = self.run_cli(payload)
        self.assertEqual(code, 2)
        self.assertEqual(out["error"], "InvalidInputError")

        payload = self.payload()
        payload["decision_rules"] = None
        code, out, err = self.run_cli(payload)
        self.assertEqual(code, 2)
        self.assertEqual(out["error"], "InvalidInputError")

    def test_omitted_optional_fields_keeps_legacy_shape(self):
        payload = self.payload()
        del payload["choices"]
        del payload["decision_rules"]
        # 无 choices 时选项从投票派生（仅 no、yes，恰好相同），claimed 去掉门槛字段。
        for field in ("quorum_met", "approval_met", "decision"):
            del payload["claimed_result"][field]
        code, out, err = self.run_cli(payload)
        self.assertEqual(code, 0)
        self.assertEqual(out["review_status"], "matched")
        for field in ("quorum_met", "approval_met", "decision"):
            self.assertNotIn(field, out)




    def review_input(self, **overrides):
        data = {
            "proposal_id": "p1",
            "snapshot_block": 42,
            "snapshot": {"a": 2, "b": 3, "c": 4, "d": 5, "e": 7},
            "votes": [{"voter": "c", "choice": "yes"}],
            "delegations": {"a": "b", "b": "c", "d": "c"},
            "claimed_result": {
                "proposal_id": "p1",
                "snapshot_block": 42,
                "results_by_choice": {"yes": 14},
                "direct_participated_weight": 4,
                "delegated_weight": 10,
                "non_participated_weight": 7,
            },
        }
        data.update(overrides)
        return data

    def run_cli(self, payload, args=("review",)):
        proc = subprocess.run(
            [sys.executable, str(MODULE), *args],
            input=payload if isinstance(payload, str) else json.dumps(payload),
            capture_output=True,
            text=True,
        )
        return proc.returncode, json.loads(proc.stdout), proc.stderr

    def test_matched_success_exit_zero(self):
        code, out, err = self.run_cli(self.review_input())
        self.assertEqual(code, 0)
        self.assertEqual(err, "")
        self.assertEqual(out["review_status"], "matched")
        self.assertEqual(out["field_differences"], [])
        self.assertTrue(out["weight_conservation_holds"])
        self.assertEqual(out["results_by_choice"], {"yes": 14})
        self.assertEqual(
            list(out.keys()),
            [
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
            ],
        )

    def test_mismatched_exit_zero_with_field_differences(self):
        payload = self.review_input()
        payload["claimed_result"]["delegated_weight"] = 9
        del payload["claimed_result"]["snapshot_block"]
        code, out, err = self.run_cli(payload)
        self.assertEqual(code, 0)
        self.assertEqual(err, "")
        self.assertEqual(out["review_status"], "mismatched")
        self.assertEqual(
            out["field_differences"],
            [
                {"field": "snapshot_block", "computed": 42, "claimed": None},
                {"field": "delegated_weight", "computed": 10, "claimed": 9},
            ],
        )

    def test_missing_or_extra_top_level_field(self):
        for mutate in (
            lambda d: d.pop("claimed_result"),
            lambda d: d.__setitem__("bogus", ["yes"]),
        ):
            payload = self.review_input()
            mutate(payload)
            with self.subTest(payload=sorted(payload)):
                code, out, err = self.run_cli(payload)
                self.assertEqual(code, 2)
                self.assertEqual(err, "")
                self.assertEqual(out["error"], "InvalidInputError")
                self.assertIsInstance(out["message"], str)

    def test_optional_choices_and_decision_rules_accepted(self):
        # 两个可选顶层字段不再被当作未知字段；省略 choices 派生选项，
        # 给出 choices 与 decision_rules 时成功并追加门槛字段。
        payload = self.review_input(
            choices=["yes", "no"],
            decision_rules={
                "approval_choices": ["yes"],
                "min_counted_weight": 10,
                "approval_basis_points": 5000,
            },
        )
        payload["claimed_result"]["results_by_choice"] = {"no": 0, "yes": 14}
        payload["claimed_result"].update(
            quorum_met=True, approval_met=True, decision="approved"
        )
        code, out, err = self.run_cli(payload)
        self.assertEqual(code, 0)
        self.assertEqual(err, "")
        self.assertEqual(out["review_status"], "matched")
        self.assertEqual(list(out.keys())[-3:],
                         ["quorum_met", "approval_met", "decision"])

    def test_non_object_and_invalid_json(self):
        for raw in ("[1, 2]", "42", "\"x\"", "{not json"):
            with self.subTest(raw=raw):
                code, out, err = self.run_cli(raw)
                self.assertEqual(code, 2)
                self.assertEqual(out["error"], "InvalidInputError")

    def test_each_error_class_on_stdout(self):
        cases = [
            (self.review_input(snapshot={"a": -1}), "SnapshotIntegrityError"),
            (self.review_input(snapshot_block=True), "SnapshotIntegrityError"),
            (
                self.review_input(votes=[{"voter": "z", "choice": "yes"}]),
                "BallotValidationError",
            ),
            (
                self.review_input(delegations={"a": "b", "b": "a"}),
                "DelegationConflictError",
            ),
            (
                self.review_input(claimed_result={"bogus": 1}),
                "ClaimedResultValidationError",
            ),
            (self.review_input(claimed_result=None), "ClaimedResultValidationError"),
        ]
        for payload, name in cases:
            with self.subTest(name=name):
                code, out, err = self.run_cli(payload)
                self.assertEqual(code, 2)
                self.assertEqual(err, "")
                self.assertEqual(out["error"], name)
                self.assertIsInstance(out["message"], str)
                self.assertNotIn("0x", out["message"])  # 无内存地址

    def test_unknown_command_is_invalid_input(self):
        code, out, err = self.run_cli(self.review_input(), args=("tally",))
        self.assertEqual(code, 2)
        self.assertEqual(out["error"], "InvalidInputError")

    def test_no_args_keeps_tally_behavior(self):
        payload = base_input(
            votes=[{"voter": "a", "choice": "yes"}, {"voter": "b", "choice": "no"}]
        )
        code, out, err = self.run_cli(payload, args=())
        self.assertEqual(code, 0)
        self.assertEqual(err, "")
        self.assertEqual(out["per_choice"], {"yes": 10, "no": 5})
        self.assertNotIn("review_status", out)


def events_input(**overrides):
    data = {
        "proposal_id": "p1",
        "choices": ["yes", "no"],
        "votes": [],
        "snapshot": {"a": 2, "b": 3, "c": 4},
        "snapshot_block": 10,
        "delegation_events": [],
    }
    data.update(overrides)
    return data


class EventsTallyTests(unittest.TestCase):
    def test_empty_events_matches_static_tally(self):
        data = events_input(votes=[{"voter": "a", "choice": "yes"}])
        result = gt.tally_proposal_from_events(data)
        static = gt.tally_proposal(base_input(
            snapshot={"a": 2, "b": 3, "c": 4},
            votes=[{"voter": "a", "choice": "yes"}],
        ))
        for field, value in static.items():
            self.assertEqual(result[field], value)
        self.assertEqual(
            list(result.keys())[-2:], ["snapshot_block", "resolved_delegations"]
        )
        self.assertEqual(result["snapshot_block"], 10)
        self.assertEqual(
            result["resolved_delegations"], {"a": "a", "b": "b", "c": "c"}
        )

    def test_events_rebuild_delegation_state(self):
        data = events_input(
            votes=[{"voter": "c", "choice": "yes"}],
            delegation_events=[
                {"block": 5, "delegator": "a", "trustee": "b"},
                {"block": 6, "delegator": "b", "trustee": "c"},
            ],
        )
        result = gt.tally_proposal_from_events(data)
        # a -> b -> c：c 合并 4 + 3 + 2。
        self.assertEqual(result["effective_weights"], {"c": 9})
        self.assertEqual(result["per_choice"], {"yes": 9, "no": 0})
        self.assertEqual(
            result["resolved_delegations"], {"a": "c", "b": "c", "c": "c"}
        )

    def test_same_block_later_event_overrides(self):
        data = events_input(
            votes=[{"voter": "c", "choice": "yes"}],
            delegation_events=[
                {"block": 5, "delegator": "a", "trustee": "b"},
                {"block": 5, "delegator": "a", "trustee": "c"},
            ],
        )
        result = gt.tally_proposal_from_events(data)
        self.assertEqual(result["resolved_delegations"]["a"], "c")
        self.assertEqual(result["effective_weights"], {"c": 6})

    def test_null_trustee_clears_delegation(self):
        data = events_input(
            votes=[
                {"voter": "a", "choice": "yes"},
                {"voter": "c", "choice": "no"},
            ],
            delegation_events=[
                {"block": 5, "delegator": "a", "trustee": "c"},
                {"block": 7, "delegator": "a", "trustee": None},
            ],
        )
        result = gt.tally_proposal_from_events(data)
        self.assertEqual(result["resolved_delegations"]["a"], "a")
        self.assertEqual(result["per_choice"], {"yes": 2, "no": 4})

    def test_events_after_snapshot_block_are_ignored(self):
        data = events_input(
            votes=[{"voter": "c", "choice": "yes"}],
            snapshot_block=6,
            delegation_events=[
                {"block": 5, "delegator": "a", "trustee": "c"},
                {"block": 7, "delegator": "a", "trustee": None},
                {"block": 8, "delegator": "b", "trustee": "c"},
            ],
        )
        result = gt.tally_proposal_from_events(data)
        # 快照后事件无效：a 仍委托给 c，b 未委托。
        self.assertEqual(
            result["resolved_delegations"], {"a": "c", "b": "b", "c": "c"}
        )
        self.assertEqual(result["effective_weights"], {"c": 6})

    def test_events_after_snapshot_block_still_validated(self):
        data = events_input(
            snapshot_block=6,
            delegation_events=[
                {"block": 9, "delegator": "a", "trustee": "a"},
            ],
        )
        with self.assertRaises(gt.DelegationEventError):
            gt.tally_proposal_from_events(data)

    def test_delegation_override_with_events(self):
        data = events_input(
            votes=[
                {"voter": "a", "choice": "no", "delegation_override": True},
                {"voter": "c", "choice": "yes"},
            ],
            delegation_events=[
                {"block": 5, "delegator": "a", "trustee": "b"},
                {"block": 6, "delegator": "b", "trustee": "c"},
            ],
        )
        result = gt.tally_proposal_from_events(data)
        self.assertEqual(result["effective_weights"], {"a": 2, "c": 7})
        self.assertEqual(result["per_choice"], {"yes": 7, "no": 2})

    def test_decision_rules_appended_after_event_fields(self):
        data = events_input(
            votes=[{"voter": "c", "choice": "yes"}],
            delegation_events=[
                {"block": 6, "delegator": "b", "trustee": "c"},
            ],
            decision_rules={
                "approval_choices": ["yes"],
                "min_counted_weight": 5,
                "approval_basis_points": 5000,
            },
        )
        result = gt.tally_proposal_from_events(data)
        self.assertEqual(
            list(result.keys())[-5:],
            [
                "snapshot_block",
                "resolved_delegations",
                "quorum_met",
                "approval_met",
                "decision",
            ],
        )
        self.assertEqual(result["decision"], "approved")

    def test_include_provenance_with_events(self):
        data = events_input(
            votes=[{"voter": "c", "choice": "yes"}],
            delegation_events=[
                {"block": 5, "delegator": "a", "trustee": "b"},
                {"block": 6, "delegator": "b", "trustee": "c"},
            ],
            include_provenance=True,
        )
        result = gt.tally_proposal_from_events(data)
        self.assertEqual(
            result["weight_provenance"], {"c": {"a": 2, "b": 3, "c": 4}}
        )
        self.assertEqual(
            list(result.keys())[-2:], ["snapshot_block", "resolved_delegations"]
        )


class EventsErrorTests(unittest.TestCase):
    def test_missing_or_extra_event_field(self):
        for event in (
            {"block": 1, "delegator": "a"},
            {"block": 1, "delegator": "a", "trustee": "b", "extra": 1},
        ):
            with self.subTest(event=event):
                data = events_input(delegation_events=[event])
                with self.assertRaises(gt.DelegationEventError):
                    gt.tally_proposal_from_events(data)

    def test_event_not_object_and_events_not_list(self):
        for events in ([1, 2], [{"block": 1}], "x", 42):
            with self.subTest(events=events):
                data = events_input(delegation_events=events)
                with self.assertRaises(gt.DelegationEventError):
                    gt.tally_proposal_from_events(data)

    def test_block_type_invalid(self):
        for block in (True, -1, 1.5, "3", None):
            with self.subTest(block=block):
                data = events_input(
                    delegation_events=[
                        {"block": block, "delegator": "a", "trustee": "b"}
                    ]
                )
                with self.assertRaises(gt.DelegationEventError):
                    gt.tally_proposal_from_events(data)

    def test_block_must_not_decrease(self):
        data = events_input(
            delegation_events=[
                {"block": 5, "delegator": "a", "trustee": "b"},
                {"block": 4, "delegator": "b", "trustee": "c"},
            ]
        )
        with self.assertRaises(gt.DelegationEventError):
            gt.tally_proposal_from_events(data)

    def test_accounts_must_be_in_snapshot(self):
        for event in (
            {"block": 1, "delegator": "z", "trustee": "b"},
            {"block": 1, "delegator": "a", "trustee": "z"},
        ):
            with self.subTest(event=event):
                data = events_input(delegation_events=[event])
                with self.assertRaises(gt.DelegationEventError):
                    gt.tally_proposal_from_events(data)

    def test_trustee_must_differ_from_delegator(self):
        data = events_input(
            delegation_events=[{"block": 1, "delegator": "a", "trustee": "a"}]
        )
        with self.assertRaises(gt.DelegationEventError):
            gt.tally_proposal_from_events(data)

    def test_delegation_cycle_from_events(self):
        data = events_input(
            delegation_events=[
                {"block": 1, "delegator": "a", "trustee": "b"},
                {"block": 2, "delegator": "b", "trustee": "a"},
            ]
        )
        with self.assertRaises(gt.DelegationEventError):
            gt.tally_proposal_from_events(data)

    def test_cycle_only_after_snapshot_block_is_ignored(self):
        data = events_input(
            snapshot_block=1,
            votes=[{"voter": "a", "choice": "yes"}],
            delegation_events=[
                {"block": 2, "delegator": "a", "trustee": "b"},
                {"block": 3, "delegator": "b", "trustee": "a"},
            ],
        )
        result = gt.tally_proposal_from_events(data)
        self.assertEqual(result["counted_weight"], 2)

    def test_skeleton_errors_keep_invalid_input(self):
        for mutate in (
            lambda d: d.pop("snapshot_block"),
            lambda d: d.pop("delegation_events"),
            lambda d: d.__setitem__("snapshot_block", True),
            lambda d: d.__setitem__("snapshot_block", -1),
            lambda d: d.__setitem__("snapshot_block", "10"),
        ):
            data = events_input()
            mutate(data)
            with self.subTest(data=sorted(data)):
                with self.assertRaises(gt.InvalidInputError):
                    gt.tally_proposal_from_events(data)

    def test_vote_and_threshold_errors_keep_existing_classes(self):
        data = events_input(votes=[{"voter": "z", "choice": "yes"}])
        with self.assertRaises(gt.InvalidVoteError):
            gt.tally_proposal_from_events(data)
        data = events_input(
            delegation_events=[{"block": 1, "delegator": "a", "trustee": "b"}],
            votes=[{"voter": "a", "choice": "yes"}],
        )
        with self.assertRaises(gt.InvalidVoteError):
            gt.tally_proposal_from_events(data)
        data = events_input(decision_rules={"approval_choices": []})
        with self.assertRaises(gt.InvalidInputError):
            gt.tally_proposal_from_events(data)


class VerifyEventsTests(unittest.TestCase):
    def data(self, **overrides):
        return events_input(
            votes=[{"voter": "c", "choice": "yes"}],
            delegation_events=[
                {"block": 5, "delegator": "a", "trustee": "b"},
                {"block": 6, "delegator": "b", "trustee": "c"},
            ],
            **overrides,
        )

    def test_valid_result_returns_true(self):
        data = self.data()
        result = gt.tally_proposal_from_events(data)
        self.assertTrue(gt.verify_tally_from_events(data, result))

    def test_missing_event_fields_rejected(self):
        data = self.data()
        for field in ("snapshot_block", "resolved_delegations"):
            with self.subTest(field=field):
                result = gt.tally_proposal_from_events(data)
                del result[field]
                with self.assertRaises(gt.TallyVerificationError):
                    gt.verify_tally_from_events(data, result)

    def test_snapshot_block_mismatch(self):
        data = self.data()
        for bad in (9, True, "10", -1):
            with self.subTest(bad=bad):
                result = gt.tally_proposal_from_events(data)
                result["snapshot_block"] = bad
                with self.assertRaises(gt.TallyVerificationError):
                    gt.verify_tally_from_events(data, result)

    def test_resolved_delegations_value_mismatch(self):
        data = self.data()
        result = gt.tally_proposal_from_events(data)
        result["resolved_delegations"]["a"] = "b"
        with self.assertRaises(gt.TallyVerificationError):
            gt.verify_tally_from_events(data, result)

    def test_resolved_delegations_order_mismatch(self):
        data = self.data()
        result = gt.tally_proposal_from_events(data)
        resolved = result["resolved_delegations"]
        result["resolved_delegations"] = dict(reversed(list(resolved.items())))
        with self.assertRaises(gt.TallyVerificationError):
            gt.verify_tally_from_events(data, result)

    def test_weight_and_winner_mismatch(self):
        data = self.data()
        result = gt.tally_proposal_from_events(data)
        result["per_choice"]["yes"] = 8
        with self.assertRaises(gt.TallyVerificationError):
            gt.verify_tally_from_events(data, result)
        result = gt.tally_proposal_from_events(data)
        result["winners"] = ["no"]
        with self.assertRaises(gt.TallyVerificationError):
            gt.verify_tally_from_events(data, result)

    def test_decision_rules_verified_with_event_fields(self):
        data = self.data(
            decision_rules={
                "approval_choices": ["yes"],
                "min_counted_weight": 5,
                "approval_basis_points": 5000,
            }
        )
        result = gt.tally_proposal_from_events(data)
        self.assertTrue(gt.verify_tally_from_events(data, result))
        self.assertEqual(
            list(result.keys())[-3:],
            ["quorum_met", "approval_met", "decision"],
        )
        result["decision"] = "rejected"
        with self.assertRaises(gt.TallyVerificationError):
            gt.verify_tally_from_events(data, result)

    def test_provenance_verified_with_events(self):
        data = self.data(include_provenance=True)
        result = gt.tally_proposal_from_events(data)
        self.assertTrue(gt.verify_tally_from_events(data, result))
        result["weight_provenance"]["c"] = {"c": 9}
        with self.assertRaises(gt.TallyVerificationError):
            gt.verify_tally_from_events(data, result)


class ReviewEventsTests(unittest.TestCase):
    def review(self, **overrides):
        data = {
            "proposal_id": "p1",
            "snapshot_block": 10,
            "snapshot": {"a": 2, "b": 3, "c": 4, "d": 5},
            "votes": [{"voter": "c", "choice": "yes"}],
            "delegation_events": [
                {"block": 5, "delegator": "a", "trustee": "b"},
                {"block": 6, "delegator": "b", "trustee": "c"},
                {"block": 7, "delegator": "d", "trustee": "c"},
                {"block": 8, "delegator": "d", "trustee": None},
            ],
            "claimed_result": {
                "proposal_id": "p1",
                "snapshot_block": 10,
                "results_by_choice": {"yes": 9},
                "direct_participated_weight": 4,
                "delegated_weight": 5,
                "non_participated_weight": 5,
            },
        }
        data.update(overrides)
        return data

    def build(self, data):
        return gt.build_review_package_from_events(
            data["proposal_id"],
            data["snapshot_block"],
            data["snapshot"],
            data["votes"],
            data["delegation_events"],
            data["claimed_result"],
            data.get("choices"),
            data.get("decision_rules"),
        )

    def test_matched_with_resolved_delegations(self):
        package = self.build(self.review())
        self.assertEqual(package["review_status"], "matched")
        self.assertEqual(package["field_differences"], [])
        self.assertTrue(package["weight_conservation_holds"])
        self.assertEqual(
            package["resolved_delegations"],
            {"a": "c", "b": "c", "c": "c", "d": "d"},
        )
        self.assertEqual(list(package.keys())[-1], "resolved_delegations")
        self.assertEqual(
            package["effective_delegations"],
            [
                {"delegator": "a", "trustee": "c", "weight": 2},
                {"delegator": "b", "trustee": "c", "weight": 3},
            ],
        )

    def test_mismatched_reports_differences(self):
        data = self.review()
        data["claimed_result"]["delegated_weight"] = 4
        package = self.build(data)
        self.assertEqual(package["review_status"], "mismatched")
        self.assertEqual(
            package["field_differences"],
            [{"field": "delegated_weight", "computed": 5, "claimed": 4}],
        )

    def test_decision_rules_appended_after_resolved_delegations(self):
        data = self.review(
            choices=["yes", "no"],
            decision_rules={
                "approval_choices": ["yes"],
                "min_counted_weight": 5,
                "approval_basis_points": 5000,
            },
        )
        data["claimed_result"]["results_by_choice"] = {"no": 0, "yes": 9}
        data["claimed_result"].update(
            quorum_met=True, approval_met=True, decision="approved"
        )
        package = self.build(data)
        self.assertEqual(package["review_status"], "matched")
        self.assertEqual(
            list(package.keys())[-4:],
            ["resolved_delegations", "quorum_met", "approval_met", "decision"],
        )

    def test_equivalence_with_static_review_package(self):
        data = self.review()
        events_package = self.build(data)
        static_package = gt.build_review_package(
            "p1",
            10,
            {"a": 2, "b": 3, "c": 4, "d": 5},
            [{"voter": "c", "choice": "yes"}],
            {"a": "b", "b": "c"},
            data["claimed_result"],
        )
        for field, value in static_package.items():
            self.assertEqual(events_package[field], value)

    def test_event_errors_raise_delegation_event_error(self):
        for events in (
            [{"block": 1, "delegator": "a"}],
            [{"block": 2, "delegator": "a", "trustee": "b"},
             {"block": 1, "delegator": "b", "trustee": "c"}],
            [{"block": 1, "delegator": "a", "trustee": "a"}],
            [{"block": 1, "delegator": "a", "trustee": "b"},
             {"block": 2, "delegator": "b", "trustee": "a"}],
            [{"block": 1, "delegator": "a", "trustee": "z"}],
        ):
            with self.subTest(events=events):
                with self.assertRaises(gt.DelegationEventError):
                    self.build(self.review(delegation_events=events))

    def test_other_error_classes_unchanged(self):
        with self.assertRaises(gt.SnapshotIntegrityError):
            self.build(self.review(snapshot={"a": -1}))
        with self.assertRaises(gt.BallotValidationError):
            self.build(self.review(votes=[{"voter": "z", "choice": "yes"}]))
        with self.assertRaises(gt.ClaimedResultValidationError):
            self.build(self.review(claimed_result={"bogus": 1}))
        with self.assertRaises(gt.InvalidInputError):
            self.build(self.review(choices=[]))


class CliEventsTests(unittest.TestCase):
    def run_cli(self, payload, args):
        proc = subprocess.run(
            [sys.executable, str(MODULE), *args],
            input=payload if isinstance(payload, str) else json.dumps(payload),
            capture_output=True,
            text=True,
        )
        return proc.returncode, json.loads(proc.stdout), proc.stderr

    def test_events_success_exit_zero(self):
        code, out, err = self.run_cli(
            events_input(
                votes=[{"voter": "c", "choice": "yes"}],
                delegation_events=[
                    {"block": 6, "delegator": "b", "trustee": "c"}
                ],
            ),
            ("events",),
        )
        self.assertEqual(code, 0)
        self.assertEqual(err, "")
        self.assertEqual(out["per_choice"], {"yes": 7, "no": 0})
        self.assertEqual(out["snapshot_block"], 10)
        self.assertEqual(
            out["resolved_delegations"], {"a": "a", "b": "c", "c": "c"}
        )

    def test_events_error_exit_two(self):
        code, out, err = self.run_cli(
            events_input(
                delegation_events=[
                    {"block": 1, "delegator": "a", "trustee": "a"}
                ]
            ),
            ("events",),
        )
        self.assertEqual(code, 2)
        self.assertEqual(err, "")
        self.assertEqual(out["error"], "DelegationEventError")
        self.assertIsInstance(out["message"], str)
        self.assertNotIn("0x", out["message"])

    def test_events_missing_field_is_invalid_input(self):
        payload = events_input()
        del payload["delegation_events"]
        code, out, err = self.run_cli(payload, ("events",))
        self.assertEqual(code, 2)
        self.assertEqual(out["error"], "InvalidInputError")

    def test_review_events_success_and_field_order(self):
        payload = {
            "proposal_id": "p1",
            "snapshot_block": 10,
            "snapshot": {"a": 2, "b": 3, "c": 4},
            "votes": [{"voter": "c", "choice": "yes"}],
            "delegation_events": [
                {"block": 5, "delegator": "a", "trustee": "b"},
                {"block": 6, "delegator": "b", "trustee": "c"},
            ],
            "claimed_result": {
                "proposal_id": "p1",
                "snapshot_block": 10,
                "results_by_choice": {"yes": 9},
                "direct_participated_weight": 4,
                "delegated_weight": 5,
                "non_participated_weight": 0,
            },
        }
        code, out, err = self.run_cli(payload, ("review-events",))
        self.assertEqual(code, 0)
        self.assertEqual(err, "")
        self.assertEqual(out["review_status"], "matched")
        self.assertEqual(list(out.keys())[-1], "resolved_delegations")
        self.assertEqual(
            out["resolved_delegations"], {"a": "c", "b": "c", "c": "c"}
        )

    def test_review_events_error_classes(self):
        base = {
            "proposal_id": "p1",
            "snapshot_block": 10,
            "snapshot": {"a": 2, "b": 3},
            "votes": [],
            "delegation_events": [],
            "claimed_result": {},
        }
        cases = [
            (dict(base, delegation_events=[
                {"block": 1, "delegator": "a", "trustee": "b"},
                {"block": 2, "delegator": "b", "trustee": "a"},
            ]), "DelegationEventError"),
            (dict(base, snapshot={"a": -1}), "SnapshotIntegrityError"),
            (dict(base, votes=[{"voter": "z", "choice": "yes"}]),
             "BallotValidationError"),
        ]
        for payload, name in cases:
            with self.subTest(name=name):
                code, out, err = self.run_cli(payload, ("review-events",))
                self.assertEqual(code, 2)
                self.assertEqual(err, "")
                self.assertEqual(out["error"], name)

    def test_review_events_missing_or_extra_field(self):
        payload = {
            "proposal_id": "p1",
            "snapshot_block": 10,
            "snapshot": {"a": 2},
            "votes": [],
            "delegation_events": [],
            "claimed_result": {},
        }
        for mutate in (
            lambda d: d.pop("delegation_events"),
            lambda d: d.__setitem__("delegations", {}),
        ):
            data = json.loads(json.dumps(payload))
            mutate(data)
            with self.subTest(data=sorted(data)):
                code, out, err = self.run_cli(data, ("review-events",))
                self.assertEqual(code, 2)
                self.assertEqual(out["error"], "InvalidInputError")

    def test_legacy_commands_ignore_event_features(self):
        # 不带事件字段的旧输入保持原行为。
        code, out, err = self.run_cli(
            base_input(votes=[{"voter": "a", "choice": "yes"}]), ()
        )
        self.assertEqual(code, 0)
        self.assertNotIn("resolved_delegations", out)
        self.assertNotIn("snapshot_block", out)


if __name__ == "__main__":
    unittest.main()
