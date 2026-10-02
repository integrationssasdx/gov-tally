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


if __name__ == "__main__":
    unittest.main()
