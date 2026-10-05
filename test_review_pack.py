"""Review Pack 测试 —— 标准库 unittest。"""

import unittest

import review_pack as rp


def base_args(**overrides):
    args = {
        "proposal_id": "p1",
        "snapshot_block": 100,
        "snapshot": {"a": 10, "b": 5, "c": 4},
        "votes": [],
        "delegations": [],
        "claimed_result": {},
    }
    args.update(overrides)
    return args


def build(**overrides):
    args = base_args(**overrides)
    return rp.build_review_pack(
        args["proposal_id"],
        args["snapshot_block"],
        args["snapshot"],
        args["votes"],
        args["delegations"],
        args["claimed_result"],
    )


class ReviewPackTests(unittest.TestCase):
    def test_empty_votes_zero_pack(self):
        pack = build()
        self.assertEqual(pack["proposal_id"], "p1")
        self.assertEqual(pack["snapshot_block"], 100)
        self.assertEqual(pack["results"], {})
        self.assertEqual(pack["direct_weight"], 0)
        self.assertEqual(pack["delegated_weight"], 0)
        self.assertEqual(pack["non_participating_weight"], 19)
        self.assertEqual(pack["delegations"], [])
        self.assertTrue(pack["weight_conserved"])
        self.assertEqual(
            pack["review"], {"status": "matched", "mismatches": []}
        )

    def test_direct_votes_only(self):
        pack = build(
            votes=[
                {"voter": "a", "choice": "for"},
                {"voter": "b", "choice": "against"},
            ]
        )
        self.assertEqual(pack["results"], {"against": 5, "for": 10})
        self.assertEqual(pack["direct_weight"], 15)
        self.assertEqual(pack["delegated_weight"], 0)
        self.assertEqual(pack["non_participating_weight"], 4)
        self.assertTrue(pack["weight_conserved"])

    def test_delegation_chain_aggregates_to_final_trustee(self):
        pack = build(
            votes=[{"voter": "c", "choice": "for"}],
            delegations=[
                {"delegator": "a", "trustee": "b"},
                {"delegator": "b", "trustee": "c"},
            ],
        )
        # a -> b -> c：c 投出本人 4 + b 5 + a 10。
        self.assertEqual(pack["results"], {"for": 19})
        self.assertEqual(pack["direct_weight"], 4)
        self.assertEqual(pack["delegated_weight"], 15)
        self.assertEqual(pack["non_participating_weight"], 0)
        self.assertEqual(
            pack["delegations"],
            [
                {"delegator": "a", "trustee": "c", "weight": 10},
                {"delegator": "b", "trustee": "c", "weight": 5},
            ],
        )
        self.assertTrue(pack["weight_conserved"])

    def test_delegation_to_non_voting_trustee_stays_unparticipated(self):
        pack = build(
            votes=[{"voter": "c", "choice": "for"}],
            delegations=[{"delegator": "a", "trustee": "b"}],
        )
        # a 委托给未投票的 b：a、b 的票权都留在未参与票权。
        self.assertEqual(pack["results"], {"for": 4})
        self.assertEqual(pack["direct_weight"], 4)
        self.assertEqual(pack["delegated_weight"], 0)
        self.assertEqual(pack["non_participating_weight"], 15)
        self.assertEqual(pack["delegations"], [])
        self.assertTrue(pack["weight_conserved"])

    def test_unvoted_undelegated_accounts_kept_as_non_participating(self):
        pack = build(votes=[{"voter": "a", "choice": "abstain"}])
        self.assertEqual(pack["results"], {"abstain": 10})
        self.assertEqual(pack["non_participating_weight"], 9)
        self.assertTrue(pack["weight_conserved"])

    def test_zero_weight_accounts(self):
        pack = build(
            snapshot={"a": 0, "b": 5},
            votes=[{"voter": "a", "choice": "for"}],
        )
        self.assertEqual(pack["results"], {"for": 0})
        self.assertEqual(pack["direct_weight"], 0)
        self.assertEqual(pack["non_participating_weight"], 5)
        self.assertTrue(pack["weight_conserved"])

    def test_matched_claimed_result(self):
        claimed = {
            "results": {"for": 15},
            "direct_weight": 10,
            "delegated_weight": 5,
            "non_participating_weight": 4,
        }
        pack = build(
            votes=[{"voter": "a", "choice": "for"}],
            delegations=[{"delegator": "b", "trustee": "a"}],
            claimed_result=claimed,
        )
        self.assertEqual(pack["review"]["status"], "matched")
        self.assertEqual(pack["review"]["mismatches"], [])

    def test_mismatched_claimed_result_lists_each_field(self):
        claimed = {
            "results": {"for": 999},
            "direct_weight": 1,
            "snapshot_block": 100,
        }
        pack = build(
            votes=[{"voter": "a", "choice": "for"}],
            claimed_result=claimed,
        )
        self.assertEqual(pack["review"]["status"], "mismatched")
        self.assertEqual(
            pack["review"]["mismatches"],
            [
                {
                    "field": "results",
                    "computed": {"for": 10},
                    "claimed": {"for": 999},
                },
                {"field": "direct_weight", "computed": 10, "claimed": 1},
            ],
        )

    def test_mismatch_is_not_an_exception(self):
        pack = build(claimed_result={"weight_conserved": False})
        self.assertEqual(pack["review"]["status"], "mismatched")
        self.assertEqual(
            pack["review"]["mismatches"],
            [{"field": "weight_conserved", "computed": True, "claimed": False}],
        )

    def test_claimed_unknown_field_raises(self):
        with self.assertRaises(rp.ClaimedResultValidationError):
            build(claimed_result={"unknown_field": 1})

    def test_claimed_not_an_object_raises(self):
        with self.assertRaises(rp.ClaimedResultValidationError):
            build(claimed_result=["not", "an", "object"])

    def test_snapshot_integrity_errors(self):
        with self.assertRaises(rp.SnapshotIntegrityError):
            build(proposal_id=123)
        with self.assertRaises(rp.SnapshotIntegrityError):
            build(snapshot_block=-1)
        with self.assertRaises(rp.SnapshotIntegrityError):
            build(snapshot_block=True)
        with self.assertRaises(rp.SnapshotIntegrityError):
            build(snapshot=["not", "an", "object"])
        with self.assertRaises(rp.SnapshotIntegrityError):
            build(snapshot={"a": -1})
        with self.assertRaises(rp.SnapshotIntegrityError):
            build(snapshot={"a": "10"})
        with self.assertRaises(rp.SnapshotIntegrityError):
            build(snapshot={"a": True})
        with self.assertRaises(rp.SnapshotIntegrityError):
            build(snapshot={"a": float("nan")})

    def test_ballot_validation_errors(self):
        with self.assertRaises(rp.BallotValidationError):
            build(votes=[{"voter": "ghost", "choice": "for"}])
        with self.assertRaises(rp.BallotValidationError):
            build(
                votes=[
                    {"voter": "a", "choice": "for"},
                    {"voter": "a", "choice": "against"},
                ]
            )
        with self.assertRaises(rp.BallotValidationError):
            build(votes=[{"voter": "a"}])
        with self.assertRaises(rp.BallotValidationError):
            build(votes="not-a-list")

    def test_delegation_conflict_errors(self):
        # 委托方/受托方不在快照
        with self.assertRaises(rp.DelegationConflictError):
            build(delegations=[{"delegator": "ghost", "trustee": "a"}])
        with self.assertRaises(rp.DelegationConflictError):
            build(delegations=[{"delegator": "a", "trustee": "ghost"}])
        # 自委托
        with self.assertRaises(rp.DelegationConflictError):
            build(delegations=[{"delegator": "a", "trustee": "a"}])
        # 一个账户指向多个受托人
        with self.assertRaises(rp.DelegationConflictError):
            build(
                delegations=[
                    {"delegator": "a", "trustee": "b"},
                    {"delegator": "a", "trustee": "c"},
                ]
            )
        # 委托环
        with self.assertRaises(rp.DelegationConflictError):
            build(
                delegations=[
                    {"delegator": "a", "trustee": "b"},
                    {"delegator": "b", "trustee": "a"},
                ]
            )
        # 同一账户既直接投票又委托他人
        with self.assertRaises(rp.DelegationConflictError):
            build(
                votes=[{"voter": "a", "choice": "for"}],
                delegations=[{"delegator": "a", "trustee": "b"}],
            )

    def test_cycle_detected_even_without_votes(self):
        with self.assertRaises(rp.DelegationConflictError):
            build(
                delegations=[
                    {"delegator": "a", "trustee": "b"},
                    {"delegator": "b", "trustee": "c"},
                    {"delegator": "c", "trustee": "a"},
                ]
            )

    def test_deterministic_output_and_field_order(self):
        args = base_args(
            votes=[
                {"voter": "c", "choice": "for"},
                {"voter": "a", "choice": "against"},
            ],
            delegations=[{"delegator": "b", "trustee": "c"}],
            claimed_result={"results": {"for": 9, "against": 10}},
        )
        first = rp.build_review_pack(*args.values())
        second = rp.build_review_pack(*args.values())
        self.assertEqual(first, second)
        self.assertEqual(
            list(first.keys()),
            [
                "proposal_id",
                "snapshot_block",
                "results",
                "direct_weight",
                "delegated_weight",
                "non_participating_weight",
                "delegations",
                "weight_conserved",
                "review",
            ],
        )
        self.assertEqual(list(first["results"].keys()), ["against", "for"])

    def test_float_weights(self):
        pack = build(
            snapshot={"a": 0.1, "b": 0.2, "c": 1.5},
            votes=[{"voter": "c", "choice": "for"}],
            delegations=[
                {"delegator": "a", "trustee": "c"},
                {"delegator": "b", "trustee": "c"},
            ],
        )
        self.assertAlmostEqual(pack["results"]["for"], 1.8)
        self.assertAlmostEqual(pack["delegated_weight"], 0.3)
        self.assertTrue(pack["weight_conserved"])

    def test_all_exceptions_share_base_class(self):
        for exc in (
            rp.SnapshotIntegrityError,
            rp.BallotValidationError,
            rp.DelegationConflictError,
            rp.ClaimedResultValidationError,
        ):
            self.assertTrue(issubclass(exc, rp.ReviewPackError))


if __name__ == "__main__":
    unittest.main()
