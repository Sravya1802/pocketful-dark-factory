"""Stage 4: refunds."""
import json
import threading
import time
import unittest

from lib import *


def T(f, t, a, **kw):
    d = {"from_handle": f, "to_handle": t, "amount": a}
    d.update(kw)
    return d


NAMES = ["ada", "bob", "cy", "dee", "eve", "op", "op2"]


class RefundBase(Base):
    def mk(self, frm="ada", to="bob", amount=1000, **kw):
        r = pay(frm, to, amount, **kw)
        self.assertEqual(r.status, 201, r)
        return r.body["payment_id"], r.body

    def conserved(self):
        self.assertEqual(sum(bal(n) for n in NAMES), BASE_TOTAL)
        self.assertTrue(all(bal(n) >= 0 for n in NAMES))


class Refunds(RefundBase):
    def test_refund_is_a_linked_reverse_payment(self):
        "[RF-01] refund -> 201 payment in the opposite direction with refund_of, request_id null, authorization_id null, original note/visibility; money moves receiver -> sender"
        pid, orig = self.mk("ada", "bob", 1000, note="dinner", visibility="private")
        r = refund("bob", pid, 300)
        self.assertEqual(r.status, 201, r)
        b = r.body
        self.payment_shape(b, "bob", "ada", 300, "dinner", "private", request_id=None)
        self.assertEqual(b["refund_of"], pid)
        self.assertIsNone(b["authorization_id"])
        self.assertIsNone(b.get("settlement_id"))
        self.assertNotEqual(b["payment_id"], pid)
        self.assertEqual((bal("ada"), bal("bob")), (10000 - 1000 + 300, 2500 + 1000 - 300))
        self.conserved()
        # the original is untouched
        item = [p for p in activity("ada", limit=200)["payments"] if p["payment_id"] == pid][0]
        self.assertEqual((item["amount"], item["refund_of"], item["note"]), (1000, None, "dinner"))
        self.assertEqual(rev_list("ada", pid)[-1]["amount"], 1000)

    def test_other_payments_carry_refund_of_null(self):
        "[RF-02] every payment that is not a refund has refund_of: null (create, pay-request, capture, settlement members, feed, statements)"
        p = pay("ada", "bob", 100).body
        rid = req("bob", "ada", 50).body["request_id"]
        pr = pay_req("ada", rid).body
        a = authorize("ada", "cy", 200).body["authorization_id"]
        cp = capture("cy", a).body
        st = settle("op", [T("ada", "dee", 10), T("bob", "dee", 5)]).body
        for o in [p, pr, cp] + st["payments"]:
            self.assertIn("refund_of", o)
            self.assertIsNone(o["refund_of"])
        rf = refund("bob", p["payment_id"], 10).body
        for who in ("ada", "bob"):
            for x in activity(who, limit=200)["payments"]:
                self.assertIn("refund_of", x)
                if x["payment_id"] != rf["payment_id"]:
                    self.assertIsNone(x["refund_of"], x)
            for e in all_statement(who)[1]:
                self.assertIn("refund_of", e["payment"])
                self.assertEqual(e["payment"]["refund_of"], p["payment_id"] if e["payment"]["payment_id"] == rf["payment_id"] else None)

    def test_permissions_and_unknown(self):
        "[RF-03] only the original receiver may refund: sender, strangers, operators 403; no token 401; unknown payment 404"
        pid, _ = self.mk()
        for who in ("ada", "cy", "eve", "op"):
            self.err(refund(who, pid, 10), 403, "forbidden")
        self.err(call("POST", "/payments/%s/refunds" % pid, {"amount": 10}, key=k()), 401, "unauthenticated")
        self.err(call("POST", "/payments/%s/refunds" % pid, {"amount": 10}, key=k(), token="bogus"), 401, "unauthenticated")
        for bad in ("p_nope", "x" * 200, "%00", "..%2Fme"):
            self.err(call("POST", "/payments/%s/refunds" % bad, {"amount": 10}, token=tok("bob"), key=k()), 404, "not_found")
        self.assertEqual((bal("ada"), bal("bob")), (9000, 3500))

    def test_idempotency(self):
        "[RF-04] key required (400/422), replay -> 200 with the original body and no money moved, different body 409, claimed key resolved before validation, per-user scope, failed attempts do not claim the key"
        pid, _ = self.mk()
        t = tok("bob")
        self.err(call("POST", "/payments/%s/refunds" % pid, {"amount": 10}, token=t), 400, "missing_idempotency_key")
        self.err(call("POST", "/payments/%s/refunds" % pid, {"amount": 10}, token=t, key=""), 400, "missing_idempotency_key")
        self.err(call("POST", "/payments/%s/refunds" % pid, {"amount": 10}, token=t, key="z" * 256), 422, "validation_failed")
        key = k()
        a = refund("bob", pid, 100, key=key)
        after = (bal("ada"), bal("bob"))
        raw = ' { "amount" : 100 } '
        b = call("POST", "/payments/%s/refunds" % pid, raw=raw, token=t, key=key)
        c = refund("bob", pid, 100, key=key)
        self.assertEqual((a.status, b.status, c.status), (201, 200, 200))
        self.assertEqual((b.body, c.body), (a.body, a.body))
        self.assertEqual((bal("ada"), bal("bob")), after)
        self.err(refund("bob", pid, 101, key=key), 409, "idempotency_key_reuse")
        self.err(refund("bob", pid, "bad", key=key), 409, "idempotency_key_reuse")
        self.err(refund("bob", pid, 0, key=key), 409, "idempotency_key_reuse")
        # the same key on another payment is a different path
        pid2, _ = self.mk("cy", "bob", 50)
        self.assertEqual(refund("bob", pid2, 10, key=key).status, 201)
        # failed first attempt does not claim the key
        key2 = k()
        self.err(refund("bob", pid, 5000, key=key2), 422, "refund_exceeds_payment")
        self.assertEqual(refund("bob", pid, 5, key=key2).status, 201)
        # same key by the receiver of another payment: independent scope
        pid3, _ = self.mk("ada", "cy", 40)
        self.assertEqual(refund("cy", pid3, 5, key=key).status, 201)

    def test_amount_validation(self):
        "[RF-05] amount must be an integer 1..1000000000: 0, negatives, fractions, strings, booleans, missing -> 422; integral forms accepted; unknown fields ignored"
        pid, _ = self.mk(amount=1000)
        t = tok("bob")
        for form in ("0", "-1", "1.5", '"5"', "true", "false", "1e-1", "1000000001", "1e10"):
            self.err(call("POST", "/payments/%s/refunds" % pid, raw='{"amount": %s}' % form, token=t, key=k()), 422, "validation_failed" if form not in ("1000000001", "1e10") else "validation_failed")
        self.err(call("POST", "/payments/%s/refunds" % pid, {}, token=t, key=k()), 422, "validation_failed")
        for form in ("null", "[]", "{}"):
            self.err4xx(call("POST", "/payments/%s/refunds" % pid, raw='{"amount": %s}' % form, token=t, key=k()), {"validation_failed", "malformed_request"})
        self.err(call("POST", "/payments/%s/refunds" % pid, raw=b"{", token=t, key=k()), 400, "malformed_request")
        for form in ("100.0", "1e2"):
            r = call("POST", "/payments/%s/refunds" % pid, raw='{"amount": %s}' % form, token=t, key=k())
            self.assertEqual(r.status, 201, (form, r))
            self.assertEqual(r.body["amount"], 100)
            self.is_int(r.body["amount"])
        r = call("POST", "/payments/%s/refunds" % pid, {"amount": 5, "note": "ignored", "visibility": "private", "from_user_id": "u_eve", "refund_of": "p_x"}, token=t, key=k())
        self.assertEqual(r.status, 201, r)
        self.assertEqual((r.body["note"], r.body["visibility"], r.body["refund_of"], r.body["from_user_id"]), ("", "public", pid, "u_bob"))
        self.assertEqual(bal("ada"), 9000 + 205)

    def test_cumulative_cap(self):
        "[RF-06] refunds may not cumulatively exceed the payment: exactly the amount is fine, one more is 422 refund_exceeds_payment (state untouched)"
        pid, _ = self.mk(amount=1000)
        self.assertEqual(refund("bob", pid, 400).status, 201)
        self.err(refund("bob", pid, 601), 422, "refund_exceeds_payment")
        before = (bal("ada"), bal("bob"), len(activity("ada", limit=200)["payments"]))
        self.assertEqual(refund("bob", pid, 600).status, 201)
        self.err(refund("bob", pid, 1), 422, "refund_exceeds_payment")
        self.err(refund("bob", pid, 1000000000), 422, "refund_exceeds_payment")
        self.assertEqual((bal("ada"), bal("bob")), (10000, 2500))
        self.assertEqual(len(activity("ada", limit=200)["payments"]), before[2] + 1)
        self.conserved()

    def test_cap_follows_the_current_corrected_amount(self):
        "[RF-07] the cap is the payment's CURRENT corrected amount; a correction cannot go below what was already refunded"
        pid, _ = self.mk(amount=1000)
        self.assertEqual(correct("ada", pid, 1, 600, ago(seconds=2)).status, 201)          # now 600
        self.err(refund("bob", pid, 601), 422, "refund_exceeds_payment")
        self.assertEqual(refund("bob", pid, 600).status, 201)
        self.err(refund("bob", pid, 1), 422, "refund_exceeds_payment")
        self.assertEqual(correct("ada", pid, 2, 800, ago(seconds=1)).status, 201)          # raised to 800: 200 of room
        self.err(refund("bob", pid, 201), 422, "refund_exceeds_payment")
        self.assertEqual(refund("bob", pid, 200).status, 201)
        self.err(correct("ada", pid, 3, 799, ago(seconds=1)), 422, "refund_exceeds_payment")   # 800 refunded
        self.assertEqual(len(rev_list("ada", pid)), 3)
        self.assertEqual(correct("ada", pid, 3, 800, ago(seconds=1)).status, 201)            # exactly the refunded amount is fine
        self.conserved()

    def test_refund_of_a_refund(self):
        "[RF-08] refunds of refunds are 422 invalid_refund_target"
        pid, _ = self.mk()
        rf = refund("bob", pid, 100).body
        self.err(refund("ada", rf["payment_id"], 10), 422, "invalid_refund_target")
        self.err(refund("ada", rf["payment_id"], 100), 422, "invalid_refund_target")
        self.assertEqual((bal("ada"), bal("bob")), (9100, 3400))

    def test_every_payment_kind_can_be_refunded(self):
        "[RF-09] direct, request, capture and settlement payments are valid targets; requests, authorizations and holds are not reopened or restored"
        d, _ = self.mk("ada", "bob", 500)
        rid = req("bob", "ada", 300, note="taxi").body["request_id"]
        rp = pay_req("ada", rid).body
        a = authorize("ada", "cy", 700, note="dep").body["authorization_id"]
        cp = capture("cy", a, 400).body              # final: remainder 300 released
        sk = k()
        st = settle("op", [T("ada", "dee", 100, note="s1"), T("bob", "dee", 40, note="s2")], key=sk)
        held_before, avail_before = me("ada")["held"], me("ada")["available"]
        r1 = refund("bob", d, 100)
        r2 = refund("ada", rp["payment_id"], 150)        # request payer is the sender (ada); refund by the RECEIVER (bob)
        self.err(r2, 403, "forbidden")
        r2 = refund("bob", rp["payment_id"], 150)
        r3 = refund("cy", cp["payment_id"], 100)
        m1 = st.body["payments"][0]
        r4 = refund("dee", m1["payment_id"], 30)
        for r, tgt in ((r1, d), (r2, rp["payment_id"]), (r3, cp["payment_id"]), (r4, m1["payment_id"])):
            self.assertEqual(r.status, 201, r)
            self.assertEqual(r.body["refund_of"], tgt)
            self.assertIsNone(r.body["request_id"])
            self.assertIsNone(r.body["authorization_id"])
            self.assertIsNone(r.body.get("settlement_id"))
        # nothing reopened or restored
        q = [x for x in requests_of("bob", limit=200)["requests"] if x["request_id"] == rid][0]
        self.assertEqual((q["status"], q["payment_id"]), ("paid", rp["payment_id"]))
        row = az_get("ada", a)
        self.assertEqual((row["status"], row["remaining_amount"], row["captured_amount"]), ("captured", 0, 400))
        self.assertEqual(me("ada")["held"], held_before)
        self.assertEqual(auths_of("ada", status="open")["authorizations"], [])
        # settlement membership and receipts unchanged
        again = settle("op", [T("ada", "dee", 100, note="s1"), T("bob", "dee", 40, note="s2")], key=sk)
        self.assertEqual((again.status, again.body), (200, st.body))
        feed = {x["payment_id"]: x for x in activity("dee", limit=200)["payments"]}
        for m in st.body["payments"]:
            self.assertEqual(feed[m["payment_id"]]["settlement_id"], st.body["settlement_id"])
        self.assertEqual(sum(1 for x in feed.values() if x["settlement_id"] == st.body["settlement_id"]), 2)
        self.conserved()

    def test_funds_come_from_available(self):
        "[RF-10] the receiver's AVAILABLE funds pay: spent or held money -> 409 insufficient_funds, nothing changes, the key stays reusable"
        pid, _ = self.mk("ada", "dee", 500)           # dee: 500
        authorize("dee", "cy", 400)                  # dee available 100
        before = (bal("ada"), bal("dee"), bal("cy"), feed_ids("ada"))
        key = k()
        self.err(refund("dee", pid, 101, key=key), 409, "insufficient_funds")
        self.assertEqual((bal("ada"), bal("dee"), bal("cy"), feed_ids("ada")), before)
        self.assertEqual(refund("dee", pid, 100, key=key).status, 201)                  # first use of the key (different body)
        self.assertEqual(me("dee")["available"], 0)
        self.err(refund("dee", pid, 1), 409, "insufficient_funds")
        pid2, _ = self.mk("ada", "dee", 300)
        pay("dee", "cy", 300)
        self.err(refund("dee", pid2, 300), 409, "insufficient_funds")
        self.conserved()

    def test_refunds_in_feed_and_statements(self):
        "[RF-11] refund payments obey the ordinary feed rules (visibility copied) and appear in both parties' statements as revision-1 entries"
        pub, _ = self.mk("ada", "bob", 400, visibility="public", note="pub")
        prv, _ = self.mk("ada", "bob", 400, visibility="private", note="prv")
        r1 = refund("bob", pub, 100).body
        r2 = refund("bob", prv, 50).body
        self.assertEqual((r1["visibility"], r2["visibility"]), ("public", "private"))
        self.assertIn(r1["payment_id"], feed_ids("cy"))
        self.assertNotIn(r2["payment_id"], feed_ids("cy"))
        for who in ("ada", "bob"):
            self.assertIn(r2["payment_id"], feed_ids(who))
        for who, sign in (("ada", 1), ("bob", -1)):
            ents = {e["payment"]["payment_id"]: e for e in all_statement(who)[1]}
            for r, amt in ((r1, 100), (r2, 50)):
                e = ents[r["payment_id"]]
                self.assertEqual((e["delta"], e["revision"], e["payment"]["amount"]), (sign * amt, 1, amt))
                self.assertEqual(dt(e["effective_at"]), dt(e["recorded_at"]))
                self.assertEqual(dt(e["effective_at"]), dt(r["created_at"]))
        self.assertNotIn(r1["payment_id"], [e["payment"]["payment_id"] for e in all_statement("cy")[1]])
        b, ents = all_statement("ada")
        self.assertEqual(b["opening_balance"] + sum(e["delta"] for e in ents), b["closing_balance"])
        self.assertEqual(b["closing_balance"], bal("ada"))

    def test_refund_payments_are_immutable(self):
        "[RF-12] a refund payment cannot be corrected (422 linked_payment_immutable, after the permission check); its revisions are readable by the two parties"
        pid, _ = self.mk()
        rf = refund("bob", pid, 100).body
        self.err(correct("bob", rf["payment_id"], 1, 50, ago(seconds=1)), 422, "linked_payment_immutable")   # bob is the refund's sender
        self.err(correct("ada", rf["payment_id"], 1, 50, ago(seconds=1)), 403, "forbidden")
        self.assertEqual(len(rev_list("bob", rf["payment_id"])), 1)
        r1 = rev_list("ada", rf["payment_id"])[0]
        self.assertEqual((r1["revision"], r1["amount"], r1["reason"]), (1, 100, ""))
        self.assertEqual((dt(r1["effective_at"]), dt(r1["recorded_at"])), (dt(rf["created_at"]), dt(rf["created_at"])))
        self.err(revisions("cy", rf["payment_id"]), 404, "not_found")

    def test_correction_debit_uses_available_after_refunds(self):
        "[RF-13] correction debits are checked against available funds (refunds and holds count)"
        pid, _ = self.mk("ada", "dee", 500)
        refund("dee", pid, 200)
        authorize("dee", "cy", 250)             # dee: 300 total, 250 held, 50 available
        self.err(correct("ada", pid, 1, 280, ago(seconds=1)), 409, "insufficient_funds")   # needs 220 back from dee, has 50 (and 200 already refunded)
        self.assertEqual(correct("ada", pid, 1, 450, ago(seconds=1)).status, 201)         # 50 back
        self.assertEqual(me("dee")["available"], 0)

    def test_zero_corrected_payment_cannot_be_refunded(self):
        "[RF-14] a payment corrected to 0 has nothing left to refund"
        pid, _ = self.mk(amount=300)
        self.assertEqual(correct("ada", pid, 1, 0, ago(seconds=1)).status, 201)
        self.err(refund("bob", pid, 1), 422, "refund_exceeds_payment")

    def test_historical_views_with_refunds(self):
        "[RF-15] a refund is an ordinary payment in history: as_of/known_at views include it exactly at its created_at; money conserved in every view"
        fx, led, n0 = hist(H_SPECS)
        reset(fx)
        time.sleep(1.1)
        rf = refund("cy", "p_002", 150)            # bob -> cy 200 (seeded, receiver cy)
        self.assertEqual(rf.status, 201, rf)
        c = dt(rf.body["created_at"])
        names = ["ada", "bob", "cy", "dee", "eve", "op", "op2"]
        for T_ in (c - timedelta(seconds=1), c - timedelta(microseconds=1), c, c + timedelta(seconds=1)):
            vals = {n: me_at(n, iso6(T_)).body["balance"] for n in names}
            self.assertEqual(sum(vals.values()), H_TOTAL)
        self.assertEqual(me_at("cy", iso6(c - timedelta(microseconds=1))).body["balance"], 1100)
        self.assertEqual(me_at("cy", iso6(c)).body["balance"], 950)
        self.assertEqual(me_at("bob", iso6(c)).body["balance"], 2450)
        self.assertEqual(me_at("cy", iso6(c), iso6(c - timedelta(microseconds=1))).body["balance"], 1100)   # refund not yet recorded

    def test_concurrent_refunds_respect_the_cap(self):
        "[RF-16] 20 concurrent refunds of 100 on a 1000 payment (distinct keys): exactly 10 succeed, the rest refund_exceeds_payment; money consistent"
        pid, _ = self.mk(amount=1000)
        t = tok("bob")
        rs = parallel([lambda: call("POST", "/payments/%s/refunds" % pid, {"amount": 100}, token=t, key=k()) for _ in range(20)])
        self.assertEqual(sum(r.status == 201 for r in rs), 10, rs)
        self.assertTrue(all(r.status == 422 and r.code == "refund_exceeds_payment" for r in rs if r.status != 201), rs)
        self.assertEqual((bal("ada"), bal("bob")), (10000, 2500))
        self.conserved()

    def test_concurrent_identical_refund(self):
        "[RF-17] 20 concurrent identical refunds with one fresh key: exactly one 201, the rest 200 with the same body, money once"
        pid, _ = self.mk(amount=1000)
        t, key = tok("bob"), k()
        rs = parallel([lambda: call("POST", "/payments/%s/refunds" % pid, {"amount": 250}, token=t, key=key) for _ in range(20)])
        self.assertEqual(sorted(r.status for r in rs), [200] * 19 + [201], rs)
        self.assertEqual(len({json.dumps(r.body, sort_keys=True) for r in rs}), 1)
        self.assertEqual((bal("ada"), bal("bob")), (9250, 3250))

    def test_refund_versus_correction_race(self):
        "[RF-18] a refund racing a correction that would go below it: never both; invariants hold"
        for _ in range(6):
            reset()
            pid, _ = self.mk(amount=1000)
            rf, cr = parallel([lambda: refund("bob", pid, 800), lambda: correct("ada", pid, 1, 300, ago(seconds=1))])
            self.assertTrue(rf.status in (201, 422) and cr.status in (201, 409, 422), (rf, cr))
            amt = rev_list("ada", pid)[-1]["amount"]
            refunded = 800 if rf.status == 201 else 0
            self.assertLessEqual(refunded, amt)
            self.assertFalse(rf.status == 201 and cr.status == 201)
            self.conserved()
            self.assertEqual(bal("ada"), 10000 - amt + refunded)

    def test_hostile_inputs_never_5xx(self):
        "[RF-19] hostile bodies and ids on refunds are 4xx with the error body, never 5xx"
        pid, _ = self.mk()
        for raw in (b"", b"null", b"[]", b"{", b'{"amount":1e999}', b'{"amount":NaN}', b"\xff\xfe", b"[" * 20000, b'{"amount":' + b"9" * 400 + b"}"):
            for path in ("/payments/%s/refunds" % pid, "/payments/%00/refunds", "/payments/" + "a" * 4000 + "/refunds"):
                r = safe_call("POST", path, raw=raw, token=tok("bob"), headers={"Idempotency-Key": k()})
                self.assertTrue(r.error is None, (path[:25], raw[:20], r.error))
                self.assertLess(r.status, 500, (path[:25], raw[:20], r))
                if r.status >= 400:
                    self.assertIsInstance(r.body["error"]["code"], str)
        self.assertEqual(call("GET", "/health").status, 200)

    def test_settlement_member_refund_and_sender(self):
        "[RF-20] a settlement member is refundable only by its receiver; refunding never changes membership (the member keeps its settlement_id and revisions)"
        st = settle("op", [T("ada", "cy", 100), T("ada", "dee", 60)]).body
        m = st["payments"][0]
        self.err(refund("ada", m["payment_id"], 10), 403, "forbidden")
        self.err(refund("op", m["payment_id"], 10), 403, "forbidden")
        r = refund("cy", m["payment_id"], 40)
        self.assertEqual(r.status, 201, r)
        got = [x for x in activity("ada", limit=200)["payments"] if x["payment_id"] == m["payment_id"]][0]
        self.assertEqual((got["settlement_id"], got["amount"]), (st["settlement_id"], 100))
        self.assertEqual(len(rev_list("ada", m["payment_id"])), 1)
        self.assertEqual(len(rev_list("ada", r.body["payment_id"])), 1)
        self.err(correct("ada", m["payment_id"], 1, 5, ago(seconds=1)), 422, "linked_payment_immutable")   # still a settlement member


if __name__ == "__main__":
    unittest.main()
