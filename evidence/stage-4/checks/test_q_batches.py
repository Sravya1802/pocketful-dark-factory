"""Stage 4: correction batches."""
import json
import threading
import time
import unittest

from lib import *

NAMES = ["ada", "bob", "cy", "dee", "eve", "op", "op2"]


def T(f, t, a, **kw):
    d = {"from_handle": f, "to_handle": t, "amount": a}
    d.update(kw)
    return d


class BatchBase(Base):
    def mk(self, frm="ada", to="bob", amount=1000, **kw):
        r = pay(frm, to, amount, **kw)
        self.assertEqual(r.status, 201, r)
        return r.body["payment_id"]

    def settlement(self, key=None):
        st = settle("op", [T("ada", "bob", 100), T("bob", "cy", 50), T("ada", "dee", 20)], key=key)
        self.assertEqual(st.status, 201, st)
        return st.body

    def revs_of(self, pids, who="op"):
        out = {}
        for p in pids:
            for w in ("ada", "bob", "cy", "dee", "eve"):
                r = revisions(w, p)
                if r.status == 200:
                    out[p] = r.body["revisions"]
                    break
        return out

    def conserved(self):
        self.assertEqual(sum(bal(n) for n in NAMES), BASE_TOTAL)
        self.assertTrue(all(bal(n) >= 0 for n in NAMES))


class BatchBasics(BatchBase):
    def test_authentication_and_permission(self):
        "[BC-01] no token 401; authenticated non-operators (even the payment's sender) 403 before the body is looked at; operator needs a key (400/422)"
        pid = self.mk()
        body = {"corrections": [citem(pid, 1, 500, ago(seconds=2))]}
        self.err(call("POST", "/correction-batches", body, key=k()), 401, "unauthenticated")
        self.err(call("POST", "/correction-batches", body, key=k(), token="bogus"), 401, "unauthenticated")
        self.err(call("POST", "/correction-batches", body), 401, "unauthenticated")            # authentication before the key check
        for who in ("ada", "bob", "cy", "eve"):
            self.err(batch(who, [citem(pid, 1, 500, ago(seconds=2))]), 403, "forbidden")
            self.err(call("POST", "/correction-batches", raw=b"{garbage", token=tok(who), key=k()), 403, "forbidden")
            self.err(call("POST", "/correction-batches", {"corrections": []}, token=tok(who), key=k()), 403, "forbidden")
        self.err(call("POST", "/correction-batches", body, token=tok("op")), 400, "missing_idempotency_key")
        self.err(call("POST", "/correction-batches", body, token=tok("op"), key=""), 400, "missing_idempotency_key")
        self.err(call("POST", "/correction-batches", body, token=tok("op"), key="z" * 256), 422, "validation_failed")
        self.assertEqual(rev_list("ada", pid)[-1]["revision"], 1)
        self.assertEqual(batch("op", [citem(pid, 1, 500, ago(seconds=2))]).status, 201)
        self.assertEqual(batch("op2", [citem(pid, 2, 400, ago(seconds=2))]).status, 201)   # every settlement operator qualifies

    def test_single_item_shape_and_effects(self):
        "[BC-02] 201 with correction_batch_id, recorded_at and revisions in input order; each revision exposes correction_batch_id; money moves between the same two wallets; an operator need not be a party"
        pid = self.mk("ada", "bob", 1000)
        e = ago(seconds=3)
        r = batch("op", [citem(pid, 1, 400, e, "operator fix")])
        self.assertEqual(r.status, 201, r)
        b = r.body
        self.assertIsInstance(b["correction_batch_id"], str)
        self.assertTrue(1 <= len(b["correction_batch_id"]) <= 64)
        self.assertRegex(b["recorded_at"], TS_STRICT)
        self.assertEqual(len(b["revisions"]), 1)
        x = b["revisions"][0]
        self.assertEqual((x["payment_id"], x["revision"], x["amount"], x["reason"], x["correction_batch_id"]), (pid, 2, 400, "operator fix", b["correction_batch_id"]))
        self.assertEqual((dt(x["effective_at"]), dt(x["recorded_at"])), (e, dt(b["recorded_at"])))
        self.assertEqual((bal("ada"), bal("bob")), (10000 - 400, 2500 + 400))
        self.conserved()
        # the revision endpoint exposes the batch id on revision 2 only; the original receipt is untouched
        rv = rev_list("ada", pid)
        self.assertEqual([x_["revision"] for x_ in rv], [1, 2])
        self.assertEqual(rv[1]["correction_batch_id"], b["correction_batch_id"])
        self.assertIsNone(rv[0].get("correction_batch_id"))
        self.assertEqual(rv[0]["amount"], 1000)
        self.assertEqual(dt(rv[1]["recorded_at"]), dt(b["recorded_at"]))
        item = [p for p in activity("ada", limit=200)["payments"] if p["payment_id"] == pid][0]
        self.assertEqual(item["amount"], 1000)

    def test_single_correction_revisions_have_no_batch_id(self):
        "[BC-03] ordinary single-payment corrections still work for nonmembers (by the sender) and carry no batch id"
        pid = self.mk()
        r = correct("ada", pid, 1, 700, ago(seconds=1))
        self.assertEqual(r.status, 201, r)
        self.assertIsNone(r.body.get("correction_batch_id"))
        self.assertIsNone(rev_list("ada", pid)[1].get("correction_batch_id"))

    def test_many_items_and_shared_recorded_at(self):
        "[BC-04] several ordinary and request payments at once: input-order revisions, one shared recorded_at strictly later than every member's previous recorded_at"
        p1, p2, p3 = self.mk("ada", "bob", 500), self.mk("bob", "cy", 300), self.mk("cy", "ada", 200)
        rid = req("bob", "ada", 100).body["request_id"]
        p4 = pay_req("ada", rid).body["payment_id"]
        correct("ada", p1, 1, 450, ago(seconds=5))          # p1 now has two revisions
        prev = {p: max(dt(r["recorded_at"]) for r in rev_list("ada" if p in (p1, p4) else ("bob" if p == p2 else "cy"), p)) for p in (p1, p2, p3, p4)}
        items = [citem(p3, 1, 150, ago(seconds=4)), citem(p1, 2, 400, ago(seconds=4)), citem(p4, 1, 90, ago(seconds=4)), citem(p2, 1, 0, ago(seconds=4))]
        r = batch("op", items)
        self.assertEqual(r.status, 201, r)
        revs = r.body["revisions"]
        self.assertEqual([x["payment_id"] for x in revs], [p3, p1, p4, p2])
        self.assertEqual([x["revision"] for x in revs], [2, 3, 2, 2])
        self.assertEqual({dt(x["recorded_at"]) for x in revs}, {dt(r.body["recorded_at"])})
        self.assertEqual({x["correction_batch_id"] for x in revs}, {r.body["correction_batch_id"]})
        for p, v in prev.items():
            self.assertGreater(dt(r.body["recorded_at"]), v, p)
        self.conserved()
        self.assertEqual(len(rev_list("bob", p2)), 2)

    def test_settlement_members_all_together(self):
        "[BC-05] a settlement can be corrected when every member is included with one effective instant (offset spellings may differ); members stay linked and the original receipts are unchanged"
        sk = k()
        st = self.settlement(key=sk)
        ids = [m["payment_id"] for m in st["payments"]]
        e = ago(seconds=3)
        spell = [iso6(e), e.astimezone(timezone(timedelta(hours=5, minutes=30))).isoformat(timespec="microseconds"), e.strftime("%Y-%m-%dT%H:%M:%S.%fZ")]
        items = [citem(ids[0], 1, 60, spell[0]), citem(ids[1], 1, 0, spell[1]), citem(ids[2], 1, 20, spell[2])]
        r = batch("op", items)
        self.assertEqual(r.status, 201, r)
        self.assertEqual({dt(x["effective_at"]) for x in r.body["revisions"]}, {e})
        self.assertEqual([x["amount"] for x in r.body["revisions"]], [60, 0, 20])
        self.assertEqual((bal("ada"), bal("bob"), bal("cy"), bal("dee")), (9920, 2560, 1000, 20))
        self.conserved()
        # receipts and retries
        again = settle("op", [T("ada", "bob", 100), T("bob", "cy", 50), T("ada", "dee", 20)], key=sk)
        self.assertEqual((again.status, again.body), (200, st))
        feed = {p["payment_id"]: p for p in activity("ada", limit=200)["payments"]}
        for m in st["payments"]:
            if m["payment_id"] in feed:
                self.assertEqual(feed[m["payment_id"]], m)
        # statements reflect the new revisions with the settlement link kept
        ents = {e_["payment"]["payment_id"]: e_ for e_ in all_statement("ada")[1]}
        self.assertEqual((ents[ids[0]]["revision"], ents[ids[0]]["payment"]["amount"], ents[ids[0]]["payment"]["settlement_id"]), (2, 60, st["settlement_id"]))
        self.assertEqual(dt(ents[ids[0]]["recorded_at"]), dt(r.body["recorded_at"]))
        # single corrections of members stay refused
        self.err(correct("ada", ids[0], 2, 10, ago(seconds=1)), 422, "linked_payment_immutable")

    def test_incomplete_and_inconsistent_settlements(self):
        "[BC-06] correcting some but not all members -> 422 incomplete_settlement; members with different effective instants -> 422 validation_failed; nothing changes"
        st = self.settlement()
        ids = [m["payment_id"] for m in st["payments"]]
        e = ago(seconds=3)
        before = (self.revs_of(ids), {n: bal(n) for n in NAMES})
        self.err(batch("op", [citem(ids[0], 1, 60, e)]), 422, "incomplete_settlement")
        self.err(batch("op", [citem(ids[0], 1, 60, e), citem(ids[2], 1, 10, e)]), 422, "incomplete_settlement")
        self.err(batch("op", [citem(ids[2], 1, 10, e), citem(ids[1], 1, 10, e)]), 422, "incomplete_settlement")
        self.err(batch("op", [citem(ids[0], 1, 60, e), citem(ids[1], 1, 40, e), citem(ids[2], 1, 10, ago(seconds=4))]), 422, "validation_failed")
        self.err(batch("op", [citem(ids[0], 1, 60, e), citem(ids[1], 1, 40, e + timedelta(microseconds=1)), citem(ids[2], 1, 10, e)]), 422, "validation_failed")
        self.assertEqual((self.revs_of(ids), {n: bal(n) for n in NAMES}), before)
        # an incomplete subset plus a non-member is still incomplete
        other = self.mk("eve", "dee", 30)
        self.err(batch("op", [citem(other, 1, 10, e), citem(ids[0], 1, 60, e)]), 422, "incomplete_settlement")
        # the whole thing is fine
        self.assertEqual(batch("op", [citem(ids[0], 1, 60, e), citem(ids[1], 1, 40, e), citem(ids[2], 1, 10, e), citem(other, 1, 10, ago(seconds=9))]).status, 201)

    def test_two_settlements_in_one_batch(self):
        "[BC-07] several settlements can be corrected together if each is complete and internally consistent (instants may differ between settlements)"
        s1 = self.settlement()
        s2 = settle("op", [T("eve", "cy", 100), T("cy", "dee", 30)]).body
        ids1, ids2 = [m["payment_id"] for m in s1["payments"]], [m["payment_id"] for m in s2["payments"]]
        e1, e2 = ago(seconds=6), ago(seconds=3)
        items = [citem(ids1[0], 1, 90, e1), citem(ids2[0], 1, 50, e2), citem(ids1[1], 1, 45, e1), citem(ids2[1], 1, 20, e2), citem(ids1[2], 1, 20, e1)]
        r = batch("op", items)
        self.assertEqual(r.status, 201, r)
        self.assertEqual([x["payment_id"] for x in r.body["revisions"]], [ids1[0], ids2[0], ids1[1], ids2[1], ids1[2]])
        self.conserved()
        # dropping one member of the second settlement makes the whole batch fail
        self.err(batch("op", [citem(ids1[0], 2, 80, e1), citem(ids1[1], 2, 40, e1), citem(ids1[2], 2, 10, e1), citem(ids2[0], 2, 40, e2)]), 422, "incomplete_settlement")


class BatchValidation(BatchBase):
    def test_shape_and_size(self):
        "[BC-08] corrections must contain 1..32 objects with distinct payment_ids, else 422 validation_failed; unknown fields ignored"
        pid = self.mk()
        t = tok("op")
        for body in ({}, {"corrections": []}, {"corrections": None}, {"corrections": "x"}, {"corrections": {}}, {"corrections": 5}, {"corrections": [5]}, {"corrections": [None]},
                     {"corrections": [[]]}, {"corrections": [{}]},
                     {"corrections": [citem(pid, 1, 5, ago(seconds=2)), citem(pid, 1, 6, ago(seconds=2))]}):
            self.err(call("POST", "/correction-batches", body, token=t, key=k()), 422, "validation_failed")
        self.err(call("POST", "/correction-batches", raw=b"{", token=t, key=k()), 400, "malformed_request")
        self.assertEqual(rev_list("ada", pid)[-1]["revision"], 1)
        r = batch("op", [citem(pid, 1, 500, ago(seconds=2), junk=1)], extra=[1], note="x")
        self.assertEqual(r.status, 201, r)
        self.assertNotIn("junk", r.body["revisions"][0])

    def test_thirty_two_items(self):
        "[BC-09] exactly 32 distinct payments in one batch work (revisions in input order); 33 -> 422"
        ids = [self.mk("ada", "bob", 10) for _ in range(33)]
        e = ago(seconds=2)
        self.err(batch("op", [citem(p, 1, 5, e) for p in ids]), 422, "validation_failed")
        r = batch("op", [citem(p, 1, 5, e) for p in ids[:32]])
        self.assertEqual(r.status, 201, r)
        self.assertEqual([x["payment_id"] for x in r.body["revisions"]], ids[:32])
        self.assertEqual(len({x["recorded_at"] for x in r.body["revisions"]}), 1)
        self.assertEqual((bal("ada"), bal("bob")), (10000 - 330 + 160, 2500 + 330 - 160))
        self.assertEqual(rev_list("ada", ids[32])[-1]["revision"], 1)
        self.conserved()

    def test_item_validation_matrix(self):
        "[BC-10] every item has the ordinary correction fields and validation (required fields, integer revision >= 1, amount 0..1e9, reason 1..200, effective_at an instant not later than now)"
        pid = self.mk()
        t = tok("op")
        good = citem(pid, 1, 100, ago(seconds=2))

        def post(item):
            return call("POST", "/correction-batches", {"corrections": [item]}, token=t, key=k())

        for f in ("payment_id", "expected_revision", "amount", "effective_at", "reason"):
            b = dict(good)
            del b[f]
            self.err(post(b), 422, "validation_failed")
        for diff in ({"expected_revision": 0}, {"expected_revision": -1}, {"expected_revision": 1.5}, {"amount": -1}, {"amount": 1000000001}, {"amount": 1.5}, {"amount": "5"},
                     {"amount": True}, {"reason": ""}, {"reason": "x" * 201}, {"effective_at": iso6(now_utc() + timedelta(hours=1))}, {"effective_at": "2026-09-20T12:00:00"},
                     {"effective_at": "2026-09-20"}, {"effective_at": ""}, {"effective_at": "garbage"}):
            self.err(post({**good, **diff}), 422, "validation_failed")
        for diff in ({"expected_revision": "1"}, {"reason": 5}, {"effective_at": 5}, {"payment_id": 5}, {"amount": None}, {"expected_revision": None}):
            self.err4xx(post({**good, **diff}), {"validation_failed", "malformed_request"})
        self.assertEqual(rev_list("ada", pid)[-1]["revision"], 1)
        for diff in ({"amount": 0}, {"reason": "x" * 200}, {"reason": "y"}):
            self.assertEqual(post({**dict(citem(pid, len(rev_list("ada", pid)), 100, ago(seconds=2))), **diff}).status, 201)

    def test_unknown_and_stale(self):
        "[BC-11] unknown payment 404; stale expected revision 409 stale_revision; neither claims the key nor changes anything"
        pid = self.mk()
        e = ago(seconds=2)
        key = k()
        self.err(batch("op", [citem("p_nope", 1, 5, e)], key=key), 404, "not_found")
        self.err(batch("op", [citem(pid, 2, 5, e)], key=key), 409, "stale_revision")
        self.err(batch("op", [citem(pid, 99, 5, e)], key=key), 409, "stale_revision")
        self.assertEqual(rev_list("ada", pid)[-1]["revision"], 1)
        self.assertEqual(batch("op", [citem(pid, 1, 5, e)], key=key).status, 201)         # the key was never claimed
        self.err(batch("op", [citem(pid, 1, 6, e)]), 409, "stale_revision")

    def test_captures_and_refunds_are_immutable(self):
        "[BC-12] captures and refund payments remain immutable (422 linked_payment_immutable); ordinary and request payments are correctable"
        a = authorize("ada", "cy", 300).body["authorization_id"]
        cap = capture("cy", a).body["payment_id"]
        p = self.mk("ada", "bob", 400)
        rf = refund("bob", p, 100).body["payment_id"]
        rid = req("bob", "ada", 70).body["request_id"]
        rp = pay_req("ada", rid).body["payment_id"]
        e = ago(seconds=2)
        for target in (cap, rf):
            self.err(batch("op", [citem(target, 1, 1, e)]), 422, "linked_payment_immutable")
            self.err(batch("op", [citem(p, 1, 400, e), citem(target, 1, 1, e)]), 422, "linked_payment_immutable")
        self.assertEqual(batch("op", [citem(rp, 1, 60, e), citem(p, 1, 400, e)]).status, 201)
        self.assertEqual(len(rev_list("ada", cap)), 1)

    def test_refund_floor(self):
        "[BC-13] a batch cannot reduce a payment below its already-refunded amount (422 refund_exceeds_payment); raising it is fine"
        p = self.mk("ada", "bob", 400)
        refund("bob", p, 250)
        e = ago(seconds=2)
        self.err(batch("op", [citem(p, 1, 249, e)]), 422, "refund_exceeds_payment")
        self.assertEqual(batch("op", [citem(p, 1, 250, e)]).status, 201)
        self.assertEqual(batch("op", [citem(p, 2, 600, ago(seconds=1))]).status, 201)
        self.conserved()


class BatchPrecedence(BatchBase):
    def test_item_errors_in_input_order(self):
        "[BC-14] item errors are reported in input order: the first failing item decides (validation 422, unknown 404, stale 409, immutable 422)"
        p1, p2, p3 = self.mk(), self.mk("bob", "cy", 100), self.mk("cy", "dee", 50)
        a = authorize("ada", "cy", 100).body["authorization_id"]
        cap = capture("cy", a).body["payment_id"]
        e = ago(seconds=2)
        ok = lambda p: citem(p, 1, 5, e)
        stale = citem(p2, 7, 5, e)
        invalid = citem(p3, 1, -5, e)
        unknown = citem("p_nope", 1, 5, e)
        immut = citem(cap, 1, 5, e)
        cases = [([stale, invalid], 409, "stale_revision"), ([invalid, stale], 422, "validation_failed"), ([invalid, unknown], 422, "validation_failed"),
                 ([unknown, invalid], 404, "not_found"), ([ok(p1), unknown, stale], 404, "not_found"), ([ok(p1), stale, unknown], 409, "stale_revision"),
                 ([ok(p1), immut, stale], 422, "linked_payment_immutable"), ([stale, immut], 409, "stale_revision"), ([immut, stale], 422, "linked_payment_immutable")]
        before = {n: bal(n) for n in NAMES}
        for items, status, code in cases:
            self.err(batch("op", items), status, code)
        self.assertEqual({n: bal(n) for n in NAMES}, before)
        self.assertEqual(rev_list("ada", p1)[-1]["revision"], 1)

    def test_item_errors_beat_settlement_completeness_and_funds(self):
        "[BC-15] item errors (in input order) take precedence over incomplete_settlement, insufficient_funds and historical_overdraft"
        st = self.settlement()
        ids = [m["payment_id"] for m in st["payments"]]
        other = self.mk("eve", "dee", 40)
        e = ago(seconds=2)
        self.err(batch("op", [citem(ids[0], 1, 60, e), citem(other, 9, 5, e)]), 409, "stale_revision")
        self.err(batch("op", [citem(ids[0], 1, 60, e), citem("p_nope", 1, 5, e)]), 404, "not_found")
        self.err(batch("op", [citem(ids[0], 1, 60, e), citem(other, 1, -3, e)]), 422, "validation_failed")
        # unaffordable AND an item error later: the item error wins
        big = self.mk("ada", "dee", 3000)
        self.err(batch("op", [citem(big, 1, 0, e), citem(other, 4, 5, e)]), 409, "stale_revision")

    def test_completeness_beats_funds_and_funds_beat_history(self):
        "[BC-16] precedence after item errors: incomplete_settlement, then insufficient_funds (current available), then historical_overdraft"
        st = self.settlement()
        ids = [m["payment_id"] for m in st["payments"]]
        e = ago(seconds=2)
        big = self.mk("ada", "dee", 3000)                              # dee holds 3000 ... then spends it
        pay("dee", "cy", 3000)
        # incomplete settlement + unaffordable item: completeness wins
        self.err(batch("op", [citem(big, 1, 0, e), citem(ids[0], 1, 90, e)]), 422, "incomplete_settlement")
        # unaffordable (dee is empty) AND historically impossible: insufficient_funds wins
        self.err(batch("op", [citem(big, 1, 0, e)]), 409, "insufficient_funds")
        specs = [("p_001", "ada", "bob", 500, 10), ("p_002", "bob", "cy", 500, 8), ("p_003", "eve", "bob", 1000, 2)]
        fx, led, n0 = hist(specs, openings={"bob": 0})
        reset(fx)
        # affordable now (bob holds 1000) but bob would have been negative at the -8h boundary: historical_overdraft
        self.err(batch("op", [citem("p_001", 1, 100, n0 - timedelta(hours=10))]), 409, "historical_overdraft")
        self.assertEqual(bal("bob"), 1000)
        # unaffordable now: bob spends the 1000
        pay("bob", "cy", 1000)
        self.err(batch("op", [citem("p_001", 1, 100, n0 - timedelta(hours=10))]), 409, "insufficient_funds")

    def test_affordability_uses_the_combined_effect(self):
        "[BC-17] current affordability is judged on the COMBINED effect of all proposed revisions per wallet"
        for spend, expect in ((1000, 201), (1001, 409)):
            reset()
            a = pay("eve", "dee", 1000).body                      # dee receives 1000 ...
            b = pay("cy", "dee", 100).body                        # ... and 100
            pay("dee", "bob", spend)                              # dee keeps 1100 - spend
            items = [citem(a["payment_id"], 1, 400, a["created_at"]), citem(b["payment_id"], 1, 600, b["created_at"])]   # dee: -600 +500 = -100 net
            single = correct("eve", a["payment_id"], 1, 400, a["created_at"])
            self.err(single, 409, "insufficient_funds")           # alone it needs 600 back
            r = batch("op", items)
            if expect == 201:
                self.assertEqual(r.status, 201, r)
                self.assertEqual(me("dee")["available"], 0)
                self.conserved()
            else:
                self.err(r, 409, "insufficient_funds")
                self.assertEqual(me("dee")["available"], 99)
                self.assertEqual(rev_list("eve", a["payment_id"])[-1]["revision"], 1)

    def test_rejection_is_atomic(self):
        "[BC-18] a rejected batch leaves history, balances, statements, feeds and idempotency records exactly as before (the key is reusable)"
        p = [self.mk("ada", "bob", 100 + i) for i in range(3)]
        pay("bob", "cy", 2600)                                      # bob keeps 203: not enough to give 303 back
        names = ("ada", "bob", "cy", "dee")
        before = (self.revs_of(p), {n: bal(n) for n in NAMES}, {n: statement(n, limit=200).body["entries"] for n in names}, {n: activity(n, limit=200) for n in names})
        key = k()
        e = ago(seconds=2)
        for items, status, code in (([citem(p[0], 1, 0, e), citem(p[1], 1, 0, e), citem(p[2], 1, 0, e)], 409, "insufficient_funds"),
                                    ([citem(p[0], 1, 5, e), citem(p[1], 4, 5, e)], 409, "stale_revision"),
                                    ([citem(p[0], 1, 5, e), citem(p[1], 1, 5, iso6(now_utc() + timedelta(hours=1)))], 422, "validation_failed")):
            self.err(batch("op", items, key=key), status, code)
            after = (self.revs_of(p), {n: bal(n) for n in NAMES}, {n: statement(n, limit=200).body["entries"] for n in names}, {n: activity(n, limit=200) for n in names})
            self.assertEqual(after, before)
        # the key never got claimed: a different valid body under it is a first use
        r = batch("op", [citem(p[0], 1, 90, e)], key=key)
        self.assertEqual(r.status, 201, r)


class BatchRetries(BatchBase):
    def test_replay_and_reuse(self):
        "[BC-19] replay returns the ORIGINAL batch response with 200 (after newer revisions too); a different body is 409 idempotency_key_reuse (also for invalid bodies); operators have separate key scopes"
        p1, p2 = self.mk(), self.mk("bob", "cy", 200)
        e = iso6(ago(seconds=3))
        body = {"corrections": [citem(p1, 1, 400, e), citem(p2, 1, 100, e)]}
        key = k()
        a = call("POST", "/correction-batches", body, token=tok("op"), key=key)
        self.assertEqual(a.status, 201, a)
        self.assertEqual(correct("ada", p1, 2, 300, ago(seconds=2)).status, 201)       # newer revision
        after = {n: bal(n) for n in NAMES}
        for raw in (json.dumps(body), json.dumps(body, indent=2), json.dumps({"corrections": body["corrections"]}, sort_keys=True)):
            b = call("POST", "/correction-batches", raw=raw, token=tok("op"), key=key)
            self.assertEqual((b.status, b.body), (200, a.body))
        self.assertEqual({n: bal(n) for n in NAMES}, after)
        self.err(call("POST", "/correction-batches", {"corrections": [body["corrections"][1], body["corrections"][0]]}, token=tok("op"), key=key), 409, "idempotency_key_reuse")
        self.err(call("POST", "/correction-batches", {"corrections": [citem(p1, 1, 401, e), body["corrections"][1]]}, token=tok("op"), key=key), 409, "idempotency_key_reuse")
        self.err(call("POST", "/correction-batches", {"corrections": []}, token=tok("op"), key=key), 409, "idempotency_key_reuse")
        self.err(call("POST", "/correction-batches", {"corrections": [citem(p1, "bad", -1, "x")]}, token=tok("op"), key=key), 409, "idempotency_key_reuse")
        # another operator with the same key string: first use (here on a stale revision so it is rejected, proving it was not a replay)
        self.err(call("POST", "/correction-batches", body, token=tok("op2"), key=key), 409, "stale_revision")

    def test_originals_never_change(self):
        "[BC-20] original payments and receipts never change: retries of the original payment, request payment and settlement return their original bodies; the feed keeps the original amounts"
        kp, ks = k(), k()
        p = pay("ada", "bob", 1000, key=kp, note="orig")
        st = settle("op", [T("ada", "cy", 100), T("cy", "dee", 40)], key=ks)
        feed_before = activity("ada", limit=200)
        ids = [m["payment_id"] for m in st.body["payments"]]
        e = ago(seconds=2)
        self.assertEqual(batch("op", [citem(p.body["payment_id"], 1, 200, e), citem(ids[0], 1, 50, e), citem(ids[1], 1, 20, e)]).status, 201)
        again = pay("ada", "bob", 1000, key=kp, note="orig")
        self.assertEqual((again.status, again.body), (200, p.body))
        self.assertEqual(again.body["amount"], 1000)
        s2 = settle("op", [T("ada", "cy", 100), T("cy", "dee", 40)], key=ks)
        self.assertEqual((s2.status, s2.body), (200, st.body))
        self.assertEqual(activity("ada", limit=200), feed_before)
        for pid in [p.body["payment_id"]] + ids:
            self.assertEqual(rev_list("ada" if pid != ids[1] else "cy", pid)[0]["revision"], 1)

    def test_statements_snapshots_and_known_at(self):
        "[BC-21] new statements reflect the batch revisions (selected revision, shared recorded_at); earlier snapshots keep paging their frozen entries; known_at before the batch sees the old amounts"
        st = self.settlement()
        ids = [m["payment_id"] for m in st["payments"]]
        pid = self.mk("ada", "eve", 500)
        snap_a = statement("ada", limit=2).body
        frozen = statement("ada", snapshot=snap_a["snapshot"], limit=200).body
        frozen_b = statement("bob", limit=200).body
        time.sleep(0.05)
        e = ago(seconds=3)
        r = batch("op", [citem(ids[0], 1, 60, e), citem(ids[1], 1, 40, e), citem(ids[2], 1, 10, e), citem(pid, 1, 100, e)])
        self.assertEqual(r.status, 201, r)
        rec = dt(r.body["recorded_at"])
        self.assertEqual(statement("ada", snapshot=snap_a["snapshot"], limit=200).body, frozen)
        b, ents = all_statement("ada")
        by = {e_["payment"]["payment_id"]: e_ for e_ in ents}
        for x in r.body["revisions"]:
            if x["payment_id"] in by:
                self.assertEqual((by[x["payment_id"]]["revision"], by[x["payment_id"]]["payment"]["amount"], dt(by[x["payment_id"]]["recorded_at"])), (2, x["amount"], rec))
        self.assertEqual(b["closing_balance"], bal("ada"))
        old = statement("ada", known_at=iso6(rec - timedelta(microseconds=1)), limit=200).body
        orig = {st["payments"][0]["payment_id"]: 100, st["payments"][2]["payment_id"]: 20, pid: 500}
        seen = [(x["payment"]["payment_id"], x["payment"]["amount"], x["revision"]) for x in old["entries"] if x["payment"]["payment_id"] in orig]
        self.assertEqual(sorted(seen), sorted((k_, v, 1) for k_, v in orig.items()))
        # money is conserved in every historical view
        for t_ in (rec - timedelta(seconds=10), e, rec, now_utc() + timedelta(days=1)):
            self.assertEqual(sum(me_at(n, iso6(t_)).body["balance"] for n in NAMES), BASE_TOTAL)
        self.assertEqual(sum(me_at(n, iso6(rec), iso6(rec - timedelta(microseconds=1))).body["balance"] for n in NAMES), BASE_TOTAL)

    def test_ten_independent_write_paths(self):
        "[BC-22] ten idempotent write paths: one key string is a first use on every path, and each path replays independently"
        key = k()
        t, tb, top, tc = tok("ada"), tok("bob"), tok("op"), tok("cy")
        base = self.mk("ada", "bob", 100)
        aid = authorize("ada", "cy", 10).body["authorization_id"]
        rid = req("bob", "ada", 7).body["request_id"]
        other = self.mk("eve", "dee", 50)
        e = iso6(ago(seconds=2))
        calls = [("POST", "/payments", {"to_handle": "bob", "amount": 5}, t), ("POST", "/requests", {"payer_handle": "bob", "amount": 5}, t),
                 ("POST", "/requests/%s/pay" % rid, {}, t), ("POST", "/splits", {"amount": 9, "participant_handles": ["bob"]}, t),
                 ("POST", "/settlements", {"transfers": [T("ada", "bob", 1)]}, top), ("POST", "/authorizations", {"to_handle": "bob", "amount": 5}, t),
                 ("POST", "/authorizations/%s/capture" % aid, {}, tc),
                 ("POST", "/payments/%s/corrections" % base, {"expected_revision": 1, "amount": 50, "effective_at": e, "reason": "r"}, t),
                 ("POST", "/payments/%s/refunds" % base, {"amount": 5}, tb),
                 ("POST", "/correction-batches", {"corrections": [citem(other, 1, 40, e)]}, top)]
        first = [call(m, p, b, token=tk, key=key) for m, p, b, tk in calls]
        self.assertEqual([r.status for r in first], [201] * 10, first)
        again = [call(m, p, b, token=tk, key=key) for m, p, b, tk in calls]
        self.assertEqual([r.status for r in again], [200] * 10)
        self.assertEqual([r.body for r in again], [r.body for r in first])
        self.err(call("POST", "/payments/%s/refunds" % base, {"amount": 6}, token=tb, key=key), 409, "idempotency_key_reuse")
        self.conserved()

    def test_effective_not_later_than_now(self):
        "[BC-23] effective times cannot be later than now (any item), in any spelling"
        pid = self.mk()
        for off in (timedelta(seconds=5), timedelta(hours=2), timedelta(days=30)):
            for tz in (timezone.utc, timezone(timedelta(hours=-8)), timezone(timedelta(hours=5, minutes=30))):
                bad = (now_utc() + off).astimezone(tz).isoformat(timespec="microseconds")
                self.err(batch("op", [citem(pid, 1, 5, bad)]), 422, "validation_failed")
        self.assertEqual(batch("op", [citem(pid, 1, 5, (now_utc() - timedelta(seconds=1)).astimezone(timezone(timedelta(hours=-8))).isoformat(timespec="microseconds"))]).status, 201)

    def test_hostile_inputs(self):
        "[BC-24] hostile bodies on /correction-batches are 4xx with the error body, never 5xx"
        for raw in (b"", b"null", b"[]", b"{", b'{"corrections":[' + b",".join([b"{}"] * 5000) + b"]}", b"\xff\xfe", b"[" * 20000, b'{"corrections":[{"payment_id":"x","expected_revision":1e999,"amount":1,"effective_at":"x","reason":"r"}]}',
                    b'{"corrections":[{"payment_id":"' + b"p" * 300000 + b'","expected_revision":1,"amount":1,"effective_at":"2026-01-01T00:00:00Z","reason":"r"}]}'):
            r = safe_call("POST", "/correction-batches", raw=raw, token=tok("op"), headers={"Idempotency-Key": k()})
            self.assertTrue(r.error is None, (raw[:30], r.error))
            self.assertLess(r.status, 500, (raw[:30], r))
            if r.status >= 400:
                self.assertIsInstance(r.body["error"]["code"], str)
        self.assertEqual(call("GET", "/health").status, 200)


class BatchHistory(BatchBase):
    def test_historical_boundaries_with_combined_items(self):
        "[BC-25] historical_overdraft considers ALL proposed revisions together and every wallet at every boundary"
        specs = [("p_001", "ada", "bob", 500, 10), ("p_002", "bob", "cy", 500, 8), ("p_003", "eve", "bob", 1000, 2), ("p_004", "cy", "dee", 100, 6)]
        fx, led, n0 = hist(specs, openings={"bob": 0})
        reset(fx)
        e = lambda h: n0 - timedelta(hours=h)
        # moving the outflow earlier than its funding, in a batch with an innocent item
        self.err(batch("op", [citem("p_004", 1, 90, e(6)), citem("p_002", 1, 500, e(12))]), 409, "historical_overdraft")
        # each alone is fine; together the cy->dee payment (needs cy's 500 from bob) is funded only by the earlier p_002
        self.assertEqual(batch("op", [citem("p_004", 1, 90, e(5)), citem("p_002", 1, 500, e(9))]).status, 201)
        # reversing the funding payment while its proceeds were spent earlier is a historical failure
        names = ["ada", "bob", "cy", "dee", "eve", "op", "op2"]
        tot = sum(led.opening.values())
        self.assertEqual(sum(bal(n) for n in names), tot)
        for h in (11, 9.5, 7, 5.5, 3, 1):
            self.assertEqual(sum(me_at(n, iso6(e(h))).body["balance"] for n in names), tot)

    def test_batch_with_holds(self):
        "[BC-26] available (not only total) is checked at past hold boundaries for batches too"
        specs = [("p_001", "ada", "bob", 100, 10)]
        fx, led, n0 = hist(specs, openings={"ada": 1000})
        reset(fx)
        authorize("ada", "cy", 800)
        time.sleep(1.2)
        pay("eve", "ada", 500)
        self.err(batch("op", [citem("p_001", 1, 700, n0 - timedelta(hours=10))]), 409, "historical_overdraft")
        self.assertEqual(batch("op", [citem("p_001", 1, 150, n0 - timedelta(hours=10))]).status, 201)


class BatchConcurrency(BatchBase):
    def test_batches_sharing_an_expected_revision(self):
        "[BC-27] concurrent batches sharing an expected revision of any payment: exactly one wins, the rest stale_revision; money moves once"
        p1, p2, p3 = self.mk("ada", "bob", 300), self.mk("bob", "cy", 200), self.mk("cy", "dee", 100)
        e = iso6(ago(seconds=2))
        t = tok("op")
        fns = []
        for i in range(12):
            if i % 2 == 0:
                items = [citem(p1, 1, 100 + i, e), citem(p2, 1, 50 + i, e)]
            else:
                items = [citem(p2, 1, 60 + i, e), citem(p3, 1, 10 + i, e)]       # shares p2 with the other kind
            fns.append(lambda items=items: call("POST", "/correction-batches", {"corrections": items}, token=t, key=k()))
        rs = parallel(fns)
        self.assertEqual(sum(r.status == 201 for r in rs), 1, rs)
        self.assertTrue(all(r.status == 409 and r.code == "stale_revision" for r in rs if r.status != 201), rs)
        self.assertEqual(rev_list("bob", p2)[-1]["revision"], 2)
        self.conserved()

    def test_identical_batches_with_a_fresh_key(self):
        "[BC-28] N concurrent identical batches with one fresh key: exactly one 201, the others 200 with the same body"
        p1, p2 = self.mk(), self.mk("bob", "cy", 100)
        body = {"corrections": [citem(p1, 1, 300, ago(seconds=2)), citem(p2, 1, 40, ago(seconds=2))]}
        t, key = tok("op"), k()
        rs = parallel([lambda: call("POST", "/correction-batches", body, token=t, key=key) for _ in range(16)])
        self.assertEqual(sorted(r.status for r in rs), [200] * 15 + [201], rs)
        self.assertEqual(len({json.dumps(r.body, sort_keys=True) for r in rs}), 1)
        self.assertEqual((bal("ada"), bal("bob"), bal("cy")), (9700, 2760, 1040))
        self.conserved()

    def test_batch_versus_single_correction_and_refund(self):
        "[BC-29] a batch racing a single correction or a refund of the same payment: never two revisions from the same expected revision; refunds never exceed the final amount; invariants hold"
        for rnd in range(6):
            reset()
            p = self.mk("ada", "bob", 1000)
            e = iso6(ago(seconds=2))
            b, c, r = parallel([lambda: call("POST", "/correction-batches", {"corrections": [citem(p, 1, 500, e)]}, token=tok("op"), key=k()),
                                lambda: correct("ada", p, 1, 400, ago(seconds=2)),
                                lambda: refund("bob", p, 450)])
            self.assertFalse(b.status == 201 and c.status == 201, (b, c))
            revs = rev_list("ada", p)
            self.assertEqual(len(revs), 1 + (b.status == 201) + (c.status == 201))
            amt = revs[-1]["amount"]
            refunded = 450 if r.status == 201 else 0
            self.assertLessEqual(refunded, amt)
            for x in (b, c):
                self.assertIn(x.status, (201, 409, 422), x)
            self.assertIn(r.status, (201, 422), r)
            self.conserved()
            self.assertEqual(bal("ada"), 10000 - amt + refunded)

    def test_mixed_storm(self):
        "[BC-30] 50-way mix of payments, refunds, single and batch corrections, settlements: no 5xx, sums conserved, nothing negative, every revision list consistent"
        pids = [self.mk("ada", "bob", 300 + i) for i in range(8)] + [self.mk("bob", "cy", 200 + i) for i in range(8)]
        e = iso6(ago(seconds=2))
        fns = []
        for i in range(48):
            kind = i % 6
            p = pids[i % 16]
            if kind == 0:
                fns.append(lambda p=p, i=i: call("POST", "/correction-batches", {"corrections": [citem(p, 1 + (i // 16), 100 + i, e)]}, token=tok("op"), key=k()))
            elif kind == 1:
                fns.append(lambda p=p, i=i: call("POST", "/payments/%s/corrections" % p, {"expected_revision": 1 + (i // 16), "amount": 90 + i, "effective_at": e, "reason": "s"},
                                                 token=tok("ada" if pids.index(p) < 8 else "bob"), key=k()))
            elif kind == 2:
                fns.append(lambda p=p: call("POST", "/payments/%s/refunds" % p, {"amount": 20}, token=tok("bob" if pids.index(p) < 8 else "cy"), key=k()))
            elif kind == 3:
                fns.append(lambda: call("POST", "/payments", {"to_handle": "dee", "amount": 3}, token=tok("eve"), key=k()))
            elif kind == 4:
                fns.append(lambda: call("POST", "/settlements", {"transfers": [T("eve", "cy", 5), T("cy", "dee", 2)]}, token=tok("op"), key=k()))
            else:
                fns.append(lambda: call("GET", "/statement?limit=20", token=tok("ada")))
        rs = parallel(fns)
        for r in rs:
            self.assertTrue(r.error is None and r.status < 500, r)
        self.conserved()
        for n in NAMES:
            self.assertGreaterEqual(me(n)["available"], 0)
        for p in pids:
            for w in ("ada", "bob", "cy"):
                rv = revisions(w, p)
                if rv.status == 200:
                    self.assertEqual([x["revision"] for x in rv.body["revisions"]], list(range(1, len(rv.body["revisions"]) + 1)))
                    rec = [dt(x["recorded_at"]) for x in rv.body["revisions"]]
                    self.assertTrue(all(b > a for a, b in zip(rec, rec[1:])), rec)
                    break
        self.assertEqual(sum(me_at(n, iso6(now_utc() + timedelta(days=1))).body["balance"] for n in NAMES), BASE_TOTAL)


if __name__ == "__main__":
    unittest.main()
