"""gov_tally 行为测试：正常计票、委托链、复核与各类异常。"""

import json
import subprocess
import sys
import unittest

from gov_tally import (
    DelegationCycleError,
    InvalidDelegationError,
    InvalidInputError,
    InvalidVoteError,
    TallyVerificationError,
    tally_proposal,
    verify_tally,
)


def base_input(**overrides):
    data = {
        "proposal_id": "p1",
        "choices": ["yes", "no"],
        "votes": [],
        "snapshot": {},
        "delegations": {},
    }
    data.update(overrides)
    return data


class TallyTest(unittest.TestCase):
    def test_simple_tally(self):
        data = base_input(
            snapshot={"a": 10, "b": 5, "c": 3},
            votes=[{"voter": "a", "choice": "yes"}, {"voter": "b", "choice": "no"}],
        )
        result = tally_proposal(data)
        self.assertEqual(result["proposal_id"], "p1")
        self.assertEqual(result["per_choice"], [10, 5])
        self.assertEqual(result["effective_weights"], {"a": 10, "b": 5, "c": 3})
        self.assertEqual(result["counted_weight"], 15)
        self.assertEqual(result["uncounted_weight"], 3)
        self.assertEqual(result["snapshot_total_weight"], 18)
        self.assertEqual(result["winners"], ["yes"])
        self.assertIs(result["is_tie"], False)
        self.assertTrue(verify_tally(data, result))

    def test_delegation_chain_merges_weight(self):
        # a -> b -> c，c 自委托；c 投票合并 a+b+c 权重，a、b 不可投票。
        data = base_input(
            snapshot={"a": 1, "b": 2, "c": 4, "d": 8},
            delegations={"a": "b", "b": "c", "c": "c"},
            votes=[{"voter": "c", "choice": "yes"}],
        )
        result = tally_proposal(data)
        self.assertEqual(result["effective_weights"], {"c": 7, "d": 8})
        self.assertEqual(result["per_choice"], [7, 0])
        self.assertEqual(result["counted_weight"], 7)
        self.assertEqual(result["uncounted_weight"], 8)
        self.assertTrue(verify_tally(data, result))

    def test_tie(self):
        data = base_input(
            snapshot={"a": 5, "b": 5},
            votes=[{"voter": "a", "choice": "yes"}, {"voter": "b", "choice": "no"}],
        )
        result = tally_proposal(data)
        self.assertEqual(result["winners"], ["yes", "no"])
        self.assertIs(result["is_tie"], True)

    def test_no_votes(self):
        result = tally_proposal(base_input(snapshot={"a": 5}))
        self.assertEqual(result["winners"], [])
        self.assertIs(result["is_tie"], False)
        self.assertEqual(result["per_choice"], [0, 0])
        self.assertEqual(result["uncounted_weight"], 5)

    def test_zero_weight_vote_is_not_effective(self):
        data = base_input(
            snapshot={"a": 0},
            votes=[{"voter": "a", "choice": "yes"}],
        )
        result = tally_proposal(data)
        self.assertEqual(result["winners"], [])
        self.assertIs(result["is_tie"], False)

    def test_invalid_input_cases(self):
        bad = [
            {},  # 缺字段
            base_input(choices=[]),  # choices 空
            base_input(choices=["yes", "yes"]),  # choices 重复
            base_input(snapshot={"a": -1}),  # 负权重
            base_input(snapshot={"a": 1.5}),  # 非整数
            base_input(snapshot={"a": True}),  # bool 非整数
            base_input(votes=[{"voter": "a"}]),  # vote 缺 choice
            base_input(delegations={"a": 1}),  # 委托目标类型错误
        ]
        for data in bad:
            with self.assertRaises(InvalidInputError, msg=repr(data)):
                tally_proposal(data)

    def test_invalid_delegation_unknown_account(self):
        data = base_input(snapshot={"a": 1}, delegations={"a": "ghost"})
        with self.assertRaises(InvalidDelegationError):
            tally_proposal(data)
        data = base_input(snapshot={"a": 1}, delegations={"ghost": "a"})
        with self.assertRaises(InvalidDelegationError):
            tally_proposal(data)

    def test_delegation_cycle(self):
        data = base_input(snapshot={"a": 1, "b": 2}, delegations={"a": "b", "b": "a"})
        with self.assertRaises(DelegationCycleError):
            tally_proposal(data)

    def test_invalid_vote_cases(self):
        cases = [
            base_input(  # 重复 voter
                snapshot={"a": 1},
                votes=[{"voter": "a", "choice": "yes"}, {"voter": "a", "choice": "no"}],
            ),
            base_input(  # 委托他人者投票
                snapshot={"a": 1, "b": 1},
                delegations={"a": "b"},
                votes=[{"voter": "a", "choice": "yes"}],
            ),
            base_input(  # voter 不在 snapshot
                snapshot={"a": 1},
                votes=[{"voter": "ghost", "choice": "yes"}],
            ),
            base_input(  # choice 不在 choices
                snapshot={"a": 1},
                votes=[{"voter": "a", "choice": "maybe"}],
            ),
        ]
        for data in cases:
            with self.assertRaises(InvalidVoteError, msg=repr(data)):
                tally_proposal(data)

    def test_verify_tally(self):
        data = base_input(
            snapshot={"a": 10, "b": 5},
            votes=[{"voter": "a", "choice": "yes"}],
        )
        result = tally_proposal(data)
        self.assertTrue(verify_tally(data, result))

        def tamper(**changes):
            r = dict(result)
            r.update(changes)
            return r

        bad_results = [
            tamper(winners=[]),  # 不只比较 winners：此处 winners 错
            tamper(per_choice=[9, 0]),  # 归属/汇总错
            tamper(uncounted_weight=4),  # 守恒错
            tamper(effective_weights={"a": 10}),  # 归属错
            tamper(is_tie=True),
            tamper(counted_weight="10"),  # 类型错
            {k: v for k, v in result.items() if k != "winners"},  # 缺字段
            tamper(extra_field=1),  # 多字段
            "not a dict",
        ]
        for bad in bad_results:
            with self.assertRaises(TallyVerificationError, msg=repr(bad)):
                verify_tally(data, bad)


class CliTest(unittest.TestCase):
    def run_cli(self, payload):
        proc = subprocess.run(
            [sys.executable, "gov_tally.py"],
            input=payload if isinstance(payload, str) else json.dumps(payload),
            capture_output=True,
            text=True,
        )
        return proc.returncode, json.loads(proc.stdout)

    def test_cli_success(self):
        code, out = self.run_cli(
            base_input(
                snapshot={"a": 3},
                votes=[{"voter": "a", "choice": "yes"}],
            )
        )
        self.assertEqual(code, 0)
        self.assertEqual(out["winners"], ["yes"])

    def test_cli_error_exit_2(self):
        code, out = self.run_cli(base_input(choices=[]))
        self.assertEqual(code, 2)
        self.assertEqual(out["error"], "InvalidInputError")
        self.assertIsInstance(out["message"], str)

    def test_cli_invalid_json(self):
        code, out = self.run_cli("{not json")
        self.assertEqual(code, 2)
        self.assertEqual(out["error"], "InvalidInputError")

    def test_cli_cycle_error_name(self):
        code, out = self.run_cli(
            base_input(snapshot={"a": 1}, delegations={"a": "a"}, votes=[])
        )
        self.assertEqual(code, 0)  # 自委托合法
        code, out = self.run_cli(
            base_input(
                snapshot={"a": 1, "b": 1}, delegations={"a": "b", "b": "a"}
            )
        )
        self.assertEqual(code, 2)
        self.assertEqual(out["error"], "DelegationCycleError")


if __name__ == "__main__":
    unittest.main()
