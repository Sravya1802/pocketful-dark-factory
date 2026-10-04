"""POST /settlements (atomic net settlements)."""
import json
import threading
import time
import unittest

from lib import *


def T(f, t, a, **kw):
    d = {"from_handle": f, "to_handle": t, "amount": a}
    d.update(kw)
    return d


class Settlements(Base):
    def test_auth_and_permission(self):
        "[SET-01] no token 401; authenticated non-operator 403 forbidden; operator ok"
        body = {"transfers": [T("ada", "bob", 1)]}
        self.err(call("POST", "/settlements", body, key=k()), 401, "unauthenticated")
        self.err(call("POST", "/settlements", body, key=k(), token="bogus"), 401, "unauthenticated")
        for n in ("ada", "bob", "dee"):
            self.err(settle(n, [T("ada", "bob", 1)]), 403, "forbidden")
        self.assertEqual(bal("ada"), 10000)
        self.assertEqual(settle("op", [T("ada", "bob", 1)]).status, 201)
        self.assertEqual(settle("op2", [T("ada", "bob", 1)]).status, 201)

    def test_no_operators_in_fixture(self):
        "[SET-02] settlement_operator_ids defaults to []: nobody is an operator"
        fx = base_fixture()
        del fx["settlement_operator_ids"]
        reset(fx)
        self.err(settle("op", [T("ada", "bob", 1)]), 403, "forbidden")
        self.err(settle("ada", [T("ada", "bob", 1)]), 403, "forbidden")
        reset(base_fixture(settlement_operator_ids=["u_op"]))
        self.err(settle("op2", [T("ada", "bob", 1)]), 403, "forbidden")
        self.assertEqual(settle("op", [T("ada", "bob", 1)]).status, 201)

    def test_authorisation_before_validation(self):
        "[SET-03] a non-operator is 403 even with an invalid body (permission is checked first); operator without key 400"
        self.err(settle("ada", [T("ada", "nobody", 1)]), 403, "forbidden")
        self.err(call("POST", "/settlements", {"transfers": [T("ada", "bob", 1)]}, token=tok("op")), 400, "missing_idempotency_key")

    def test_receipt_shape(self):
        "[SET-04] 201 with settlement_id, committed_at, payments in input order; members are ordinary payments linked by settlement_id"
        tr = [T("ada", "bob", 100, note="a"), T("bob", "cy", 50, visibility="private"), T("cy", "dee", 7, note="é☕", visibility="public")]
        r = settle("op", tr)
        self.assertEqual(r.status, 201, r)
        b = r.body
        self.assertIsInstance(b["settlement_id"], str)
        self.assertLessEqual(len(b["settlement_id"]), 64)
        committed = self.ts(b["committed_at"])
        self.assertEqual(len(b["payments"]), 3)
        for p, t in zip(b["payments"], tr):
            self.payment_shape(p, t["from_handle"], t["to_handle"], t["amount"], t.get("note", ""), t.get("visibility", "public"))
            self.assertEqual(p["settlement_id"], b["settlement_id"])
            self.assertIsNone(p["request_id"])
            self.assertEqual(p["created_at"], b["committed_at"])
        self.assertEqual(len({p["payment_id"] for p in b["payments"]}), 3)
        self.assertEqual((bal("ada"), bal("bob"), bal("cy"), bal("dee")), (9900, 2550, 1043, 7))

    def test_defaults_note_visibility(self):
        "[SET-05] transfer note defaults to '' and visibility to public"
        p = settle("op", [T("ada", "bob", 5)]).body["payments"][0]
        self.assertEqual((p["note"], p["visibility"]), ("", "public"))

    def test_members_in_feed_with_settlement_id(self):
        "[SET-06] members appear in the feed under ordinary visibility, with settlement_id; non-members null"
        direct = pay("ada", "bob", 3).body["payment_id"]
        r = settle("op", [T("ada", "bob", 10, visibility="private", note="m1"), T("ada", "cy", 11, note="m2")]).body
        sid = r["settlement_id"]
        feed = {p["payment_id"]: p for p in activity("ada", limit=200)["payments"]}
        for p in r["payments"]:
            self.assertEqual(feed[p["payment_id"]], p)
        self.assertIsNone(feed[direct]["settlement_id"])
        self.assertEqual({p["payment_id"] for p in activity("bob", limit=200)["payments"]} >= {r["payments"][0]["payment_id"]}, True)
        # third party: private member hidden, public visible
        eve = {p["payment_id"] for p in activity("eve", limit=200)["payments"]}
        self.assertNotIn(r["payments"][0]["payment_id"], eve)
        self.assertIn(r["payments"][1]["payment_id"], eve)
        # operator is not a party and gets no extra feed rights
        op = {p["payment_id"] for p in activity("op", limit=200)["payments"]}
        self.assertNotIn(r["payments"][0]["payment_id"], op)
        self.assertEqual(sum(1 for p in activity("ada", limit=200)["payments"] if p["settlement_id"] == sid), 2)

    def test_net_affordability_is_order_independent(self):
        "[SET-07] affordable iff every wallet's final balance is >= 0: a zero-balance wallet may pass money through"
        # dee has 0: pays out before it is paid in (listed first), net 0
        r = settle("op", [T("dee", "cy", 50), T("ada", "dee", 50)])
        self.assertEqual(r.status, 201, r)
        self.assertEqual((bal("ada"), bal("dee"), bal("cy")), (9950, 0, 1050))
        # chain through two empty wallets
        r = settle("op", [T("op", "dee", 70), T("dee", "op2", 70), T("op2", "cy", 70), T("eve", "op", 70)])
        self.assertEqual(r.status, 201, r)
        self.assertEqual((bal("op"), bal("dee"), bal("op2"), bal("eve"), bal("cy")), (0, 0, 0, 4930, 1120))
        self.assertEqual(total(), BASE_TOTAL)

    def test_circular_netting(self):
        "[SET-08] a cycle among zero-balance wallets nets to zero and commits"
        r = settle("op", [T("dee", "op", 100), T("op", "op2", 100), T("op2", "dee", 100)])
        self.assertEqual(r.status, 201, r)
        self.assertEqual([bal(n) for n in ("dee", "op", "op2")], [0, 0, 0])
        self.assertEqual(len(r.body["payments"]), 3)

    def test_collective_insufficient_funds_is_all_or_nothing(self):
        "[SET-09] insufficient collective funds -> 409 insufficient_funds; no payment, no balance change, key unclaimed"
        before = [bal(n) for n in BASE_NAMES]
        feeds = {n: feed_ids(n) for n in BASE_NAMES}
        key = k()
        tr = [T("ada", "bob", 100), T("dee", "cy", 1)]
        self.err(settle("op", tr, key=key), 409, "insufficient_funds")
        self.assertEqual([bal(n) for n in BASE_NAMES], before)
        self.assertEqual({n: feed_ids(n) for n in BASE_NAMES}, feeds)
        # net-negative through a pass-through wallet
        self.err(settle("op", [T("ada", "dee", 50), T("dee", "cy", 100)]), 409, "insufficient_funds")
        # many valid transfers + one that overdraws
        big = [T("ada", "bob", 1)] * 10 + [T("cy", "dee", 1001)]
        self.err(settle("op", big), 409, "insufficient_funds")
        self.assertEqual([bal(n) for n in BASE_NAMES], before)
        # key unclaimed: same key, a now-affordable body, is a first use
        r = settle("op", [T("ada", "bob", 100)], key=key)
        self.assertEqual(r.status, 201, r)

    def test_exact_zero_boundary(self):
        "[SET-10] a wallet may end at exactly 0; one unit more fails"
        self.assertEqual(settle("op", [T("cy", "bob", 600), T("cy", "dee", 400)]).status, 201)
        self.assertEqual(bal("cy"), 0)
        self.err(settle("op", [T("cy", "bob", 1)]), 409, "insufficient_funds")
        self.err(settle("op", [T("bob", "ada", 3102), T("ada", "bob", 1)]), 409, "insufficient_funds")  # bob would end at -1
        self.assertEqual(settle("op", [T("bob", "ada", 3101), T("ada", "bob", 1)]).status, 201)  # bob ends at exactly 0
        self.assertEqual(bal("bob"), 0)

    def test_batch_shape_validation(self):
        "[SET-11] transfers must be 1..32 objects; malformed batch shape -> 422 validation_failed; nothing happens"
        t = tok("op")
        for body in ({}, {"transfers": []}, {"transfers": None}, {"transfers": "x"}, {"transfers": {}}, {"transfers": 5},
                     {"transfers": [T("ada", "bob", 1)] * 33}, {"transfers": [5]}, {"transfers": [None]}, {"transfers": ["ada"]},
                     {"transfers": [[]]}, {"transfers": [{}]}, {"transfers": [{"from_handle": "ada", "to_handle": "bob"}]},
                     {"transfers": [{"from_handle": "ada", "amount": 1}]}, {"transfers": [{"to_handle": "bob", "amount": 1}]}):
            r = call("POST", "/settlements", body, token=t, key=k())
            self.err(r, 422, "validation_failed")
        self.assertEqual(bal("ada"), 10000)
        self.assertEqual(settle("op", [T("ada", "bob", 1)] * 32).status, 201)  # boundary: 32 ok
        self.assertEqual(bal("ada"), 10000 - 32)
        self.assertEqual(settle("op", [T("ada", "bob", 1)]).status, 201)  # boundary: 1 ok

    def test_non_object_body(self):
        "[SET-12] unparseable body -> 400; key stays unclaimed"
        key = k()
        self.err(call("POST", "/settlements", raw=b"{", token=tok("op"), key=key), 400, "malformed_request")
        self.assertEqual(settle("op", [T("ada", "bob", 1)], key=key).status, 201)

    def test_entry_validation(self):
        "[SET-13] amount range, note, visibility, handle types per ordinary payment rules"
        for bad in (T("ada", "bob", 0), T("ada", "bob", -1), T("ada", "bob", 1000000001), T("ada", "bob", "5"), T("ada", "bob", True),
                    T("ada", "bob", 2.5), T("ada", "bob", 1, note="n" * 201), T("ada", "bob", 1, note=None), T("ada", "bob", 1, note=5),
                    T("ada", "bob", 1, visibility="friends"), T("ada", "bob", 1, visibility=None)):
            self.err(settle("op", [bad]), 422, "validation_failed")
        self.assertEqual(settle("op", [T("ada", "bob", 1, note="n" * 200)]).status, 201)
        self.assertEqual(settle("op", [T("ada", "bob", 1.0)]).body["payments"][0]["amount"], 1)
        r = call("POST", "/settlements", raw='{"transfers":[{"from_handle":"ada","to_handle":"bob","amount":1e0}]}', token=tok("op"), key=k())
        self.assertEqual(r.status, 201, r)
        self.assertEqual(settle("op", [T("ada", "bob", 1, junk=1)]).status, 201)  # unknown fields ignored
        r = call("POST", "/settlements", {"transfers": [T("ada", "bob", 1)], "extra": [1]}, token=tok("op"), key=k())
        self.assertEqual(r.status, 201)
        reset(base_fixture(users=[user("ada", 3 * 10**9), user("bob", 0), user("op", 0)], payments=[], requests=[],
                           settlement_operator_ids=["u_op"]))
        self.assertEqual(settle("op", [T("ada", "bob", 1000000000)]).status, 201)
        self.err(settle("op", [T("ada", "bob", 1000000001)]), 422, "validation_failed")

    def test_handle_errors_and_precedence(self):
        "[SET-25] unknown handle 404, self-transfer 422 self_payment; entry errors win in input order, before insufficient funds"
        self.err(settle("op", [T("ada", "nobody", 1)]), 404, "not_found")
        self.err(settle("op", [T("nobody", "bob", 1)]), 404, "not_found")
        self.err(settle("op", [T("ada", "ada", 1)]), 422, "self_payment")
        self.err(settle("op", [T("dee", "cy", 100), T("ada", "nobody", 1)]), 404, "not_found")  # overdraw listed first, 404 still wins
        self.err(settle("op", [T("dee", "cy", 100), T("ada", "ada", 1)]), 422, "self_payment")
        self.err(settle("op", [T("ada", "ada", 1), T("ada", "nobody", 1)]), 422, "self_payment")  # first error in input order
        self.err(settle("op", [T("ada", "nobody", 1), T("ada", "ada", 1)]), 404, "not_found")
        self.err(settle("op", [T("ada", "bob", 0), T("ada", "nobody", 1)]), 422, "validation_failed")
        self.err(settle("op", [T("ada", "nobody", 1), T("ada", "bob", 0)]), 404, "not_found")
        self.err(settle("op", [T("ada", "bob", 1)] * 3 + [T("ada", "bob", 1, visibility="x")]), 422, "validation_failed")
        self.assertEqual([bal(n) for n in BASE_NAMES], [10000, 2500, 1000, 0, 5000, 0, 0])

    def test_failed_validation_claims_no_key(self):
        "[SET-15] failed validation claims no idempotency key and creates no payment"
        key = k()
        before = [feed_ids(n) for n in BASE_NAMES]
        self.err(settle("op", [T("ada", "bob", 5), T("ada", "nobody", 5)], key=key), 404, "not_found")
        self.err(settle("op", [], key=key), 422, "validation_failed")
        self.assertEqual([feed_ids(n) for n in BASE_NAMES], before)
        r = settle("op", [T("ada", "bob", 5)], key=key)
        self.assertEqual(r.status, 201)
        self.assertEqual(settle("op", [T("ada", "bob", 5)], key=key).status, 200)

    def test_same_server_timestamp(self):
        "[SET-16] every member shares one server-assigned created_at equal to committed_at (and it is real time)"
        r = settle("op", [T("ada", "bob", 1)] * 20).body
        self.assertEqual({p["created_at"] for p in r["payments"]}, {r["committed_at"]})
        self.ts(r["committed_at"])
        feed = {p["payment_id"]: p for p in activity("ada", limit=200)["payments"]}
        self.assertEqual({feed[p["payment_id"]]["created_at"] for p in r["payments"]}, {r["committed_at"]})

    def test_replay_after_state_change(self):
        "[SET-17] replay returns the complete original response although balances have changed since"
        tr = [T("ada", "bob", 9000)]
        key = k()
        a = settle("op", tr, key=key)
        pay("ada", "cy", 1000)
        b = settle("op", tr, key=key)  # would be insufficient if re-evaluated
        self.assertEqual((a.status, b.status), (201, 200))
        self.assertEqual(a.body, b.body)
        self.assertEqual(bal("ada"), 0)

    def test_operator_gains_nothing_else(self):
        "[SET-18] operator permission does not grant access to others' requests or private activity"
        r = req("bob", "ada", 10).body["request_id"]
        self.assertEqual(requests_of("op")["requests"], [])
        for act in ("decline", "cancel"):
            x = call("POST", "/requests/%s/%s" % (r, act), token=tok("op"))
            self.assertIn(x.status, (403, 404))
        self.assertIn(pay_req("op", r).status, (403, 404))
        p = pay("ada", "bob", 4, visibility="private", note="op-hidden").body["payment_id"]
        self.assertNotIn(p, feed_ids("op"))
        # but the operator is an ordinary user for its own wallet
        self.assertEqual(me("op")["balance"], 0)

    def test_operator_can_move_any_wallet_while_not_a_party(self):
        "[SET-19] an operator not involved in a transfer can still execute it; operator's own wallet unaffected"
        before = bal("op")
        self.assertEqual(settle("op", [T("eve", "cy", 2000)]).status, 201)
        self.assertEqual((bal("eve"), bal("cy"), bal("op")), (3000, 3000, before))

    def test_new_user_in_settlement(self):
        "[SET-20] a signed-up user can be a settlement party"
        signup("set.tle@example.com")
        self.assertEqual(settle("op", [T("ada", "set_tle", 5), T("set_tle", "bob", 5)]).status, 201)

    def test_settlement_with_paid_request_context(self):
        "[SET-21] settlements do not touch requests: pending requests stay pending, request_id null"
        req("bob", "ada", 10)
        r = settle("op", [T("ada", "bob", 10)]).body
        self.assertIsNone(r["payments"][0]["request_id"])
        self.assertTrue(all(q["status"] in ("pending", "declined", "cancelled") for q in requests_of("ada")["requests"]))

    def test_interleaved_invalid_then_valid_with_same_key_and_other_body(self):
        "[SET-22] after a 4xx a key may carry a different body; after success a different body is reuse"
        key = k()
        self.err(settle("op", [T("ada", "ada", 5)], key=key), 422, "self_payment")
        self.assertEqual(settle("op", [T("ada", "bob", 5)], key=key).status, 201)
        self.err(settle("op", [T("ada", "bob", 6)], key=key), 409, "idempotency_key_reuse")
        self.err(settle("op", [T("ada", "ada", 6)], key=key), 409, "idempotency_key_reuse")  # key outranks validation

    def test_zero_balance_reset_with_operator_dees(self):
        "[SET-23] big batch of 32 distinct transfers across all wallets commits atomically, conserving the total"
        names = ["ada", "bob", "cy", "eve"]
        tr = []
        for i in range(32):
            tr.append(T(names[i % 4], names[(i + 1) % 4], 10 + i))
        r = settle("op", tr)
        self.assertEqual(r.status, 201, r)
        self.assertEqual([p["amount"] for p in r.body["payments"]], [10 + i for i in range(32)])
        self.assertEqual(total(), BASE_TOTAL)


if __name__ == "__main__":
    unittest.main()
