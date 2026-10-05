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


def review_input(**overrides):
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


class ReviewInputApiTests(unittest.TestCase):
    """review_package_from_input：CLI 顶层契约的纯函数入口。"""

    def test_dispatches_to_review_package(self):
        pkg = gt.review_package_from_input(review_input())
        self.assertEqual(pkg["review_status"], "matched")
        self.assertEqual(pkg["results_by_choice"], {"yes": 14})

    def test_top_level_contract_rejected_in_python(self):
        with self.assertRaises(gt.InvalidInputError):
            gt.review_package_from_input([])
        with self.assertRaises(gt.InvalidInputError):
            gt.review_package_from_input({"proposal_id": "p1"})
        extra = review_input()
        extra["decision_rules"] = {}
        with self.assertRaises(gt.InvalidInputError):
            gt.review_package_from_input(extra)

    def test_non_string_choice_key_is_claimed_result_error(self):
        # JSON 解析不出非字符串键；该契约只可能经 Python 直接调用触达。
        payload = review_input()
        payload["claimed_result"] = {"results_by_choice": {0: 1}}
        with self.assertRaises(gt.ClaimedResultValidationError):
            gt.review_package_from_input(payload)


class CliReviewTests(unittest.TestCase):
    """命令行 review 子命令：stdin 复核输入 -> stdout 复核包。"""

    def run_cli(self, payload, *args):
        if isinstance(payload, str):
            raw = payload
        else:
            raw = json.dumps(payload)
        proc = subprocess.run(
            [sys.executable, str(MODULE), *args],
            input=raw,
            capture_output=True,
            text=True,
        )
        return proc.returncode, json.loads(proc.stdout), proc.stderr

    def test_review_matched_exit_zero(self):
        code, out, err = self.run_cli(review_input(), "review")
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

    def test_review_mismatched_still_exit_zero_with_differences(self):
        # claimed 不一致不是异常：退出 0，逐项差异进 field_differences。
        payload = review_input()
        payload["claimed_result"]["delegated_weight"] = 9
        code, out, _ = self.run_cli(payload, "review")
        self.assertEqual(code, 0)
        self.assertEqual(out["review_status"], "mismatched")
        self.assertEqual(
            out["field_differences"],
            [{"field": "delegated_weight", "computed": 10, "claimed": 9}],
        )

    def test_review_sorting_results_and_delegations(self):
        # results_by_choice 按 choice、effective_delegations 按 delegator 排序，
        # 与选票和委托的输入顺序无关。
        payload = review_input(
            votes=[
                {"voter": "e", "choice": "no"},
                {"voter": "c", "choice": "yes"},
            ],
            delegations={"d": "c", "a": "b", "b": "c"},
            claimed_result={
                "proposal_id": "p1",
                "snapshot_block": 42,
                "results_by_choice": {"no": 7, "yes": 14},
                "direct_participated_weight": 11,
                "delegated_weight": 10,
                "non_participated_weight": 0,
            },
        )
        code, out, _ = self.run_cli(payload, "review")
        self.assertEqual(code, 0)
        self.assertEqual(list(out["results_by_choice"].keys()), ["no", "yes"])
        self.assertEqual(
            [d["delegator"] for d in out["effective_delegations"]],
            ["a", "b", "d"],
        )

    def test_review_missing_top_level_field_is_invalid_input(self):
        for removed in (
            "proposal_id",
            "snapshot_block",
            "snapshot",
            "votes",
            "delegations",
            "claimed_result",
        ):
            with self.subTest(removed=removed):
                payload = review_input()
                del payload[removed]
                code, out, _ = self.run_cli(payload, "review")
                self.assertEqual(code, 2)
                self.assertEqual(out["error"], "InvalidInputError")

    def test_review_extra_top_level_field_is_invalid_input(self):
        # delegation_override / decision_rules 不进入 review，出现即多余字段。
        for extra in ("delegation_override", "decision_rules", "choices", "x"):
            with self.subTest(extra=extra):
                code, out, _ = self.run_cli(review_input(**{extra: 1}), "review")
                self.assertEqual(code, 2)
                self.assertEqual(out["error"], "InvalidInputError")

    def test_review_invalid_json_and_non_object(self):
        code, out, _ = self.run_cli("{not json", "review")
        self.assertEqual(code, 2)
        self.assertEqual(out["error"], "InvalidInputError")

        code, out, _ = self.run_cli("[1, 2]", "review")
        self.assertEqual(code, 2)
        self.assertEqual(out["error"], "InvalidInputError")

    def test_review_domain_errors_each_exit_two(self):
        cases = [
            (review_input(snapshot_block=-1), "SnapshotIntegrityError"),
            (review_input(snapshot={"a": -1}), "SnapshotIntegrityError"),
            (
                review_input(votes=[{"voter": "z", "choice": "yes"}]),
                "BallotValidationError",
            ),
            (review_input(delegations={"a": "a"}), "DelegationConflictError"),
            (
                review_input(delegations={"a": "b", "b": "a"}),
                "DelegationConflictError",
            ),
            (review_input(claimed_result=[]), "ClaimedResultValidationError"),
            (
                review_input(claimed_result={"unknown_field": 1}),
                "ClaimedResultValidationError",
            ),
            (
                review_input(
                    claimed_result={"results_by_choice": ["yes", 1]}
                ),
                "ClaimedResultValidationError",
            ),
        ]
        for payload, name in cases:
            with self.subTest(name=name):
                code, out, _ = self.run_cli(payload, "review")
                self.assertEqual(code, 2)
                self.assertEqual(out["error"], name)
                self.assertIsInstance(out["message"], str)

    def test_review_error_message_is_stable(self):
        payload = review_input()
        del payload["proposal_id"]
        messages = []
        for _ in range(2):
            _, out, _ = self.run_cli(payload, "review")
            messages.append(out["message"])
        self.assertEqual(messages[0], messages[1])
        self.assertNotIn("0x", messages[0])

    def test_no_argument_keeps_legacy_tally_behaviour(self):
        payload = {
            "proposal_id": "p1",
            "choices": ["yes", "no"],
            "votes": [
                {"voter": "a", "choice": "yes"},
                {"voter": "b", "choice": "no"},
            ],
            "snapshot": {"a": 10, "b": 5},
            "delegations": {},
        }
        code, out, err = self.run_cli(payload)
        self.assertEqual(code, 0)
        self.assertEqual(err, "")
        self.assertEqual(
            list(out.keys()),
            [
                "proposal_id",
                "per_choice",
                "effective_weights",
                "uncounted_weight",
                "counted_weight",
                "snapshot_total_weight",
                "winners",
                "is_tie",
            ],
        )
        self.assertEqual(out["per_choice"], {"yes": 10, "no": 5})

    def test_unknown_command_and_extra_arguments(self):
        code, out, _ = self.run_cli(review_input(), "tally")
        self.assertEqual(code, 2)
        self.assertEqual(out["error"], "InvalidInputError")

        code, out, _ = self.run_cli(review_input(), "review", "extra")
        self.assertEqual(code, 2)
        self.assertEqual(out["error"], "InvalidInputError")


if __name__ == "__main__":
    unittest.main()
