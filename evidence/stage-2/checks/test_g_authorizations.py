"""Stage 2 API: /me fields, authorizations (create / capture / void / list), holds vs available."""
import json
import time
import unittest

from lib import *


def T(f, t, a, **kw):
    d = {"from_handle": f, "to_handle": t, "amount": a}
    d.update(kw)
    return d


class Me(AzBase):
    def test_me_fields_no_holds(self):
        "[ME-01] /me carries balance, total, available, held; with no holds all agree and held is 0"
        m = call("GET", "/me", token=tok("bob")).body
        self.assertEqual(m, {"user_id": "u_bob", "display_name": "Bob", "handle": "bob", "balance": 2500, "total": 2500,
                             "available": 2500, "held": 0, "currency": "EUR", "minor_units": 2})

    def test_me_with_hold(self):
        "[ME-02] a hold: balance == total unchanged, held = hold, available = total - held (spec example 10000/8000/2000)"
        a = authorize("ada", "bob", 2000)
        self.assertEqual(a.status, 201, a)
        m = call("GET", "/me", token=tok("ada")).body
        self.assertEqual((m["balance"], m["total"], m["available"], m["held"]), (10000, 10000, 8000, 2000))
        self.me_inv("bob", held=0, available=2500, total_=2500)  # receiver untouched until capture

    def test_stage1_behaviour_unchanged_without_holds(self):
        "[ME-03] payments keep working exactly as in stage 1; payment objects carry authorization_id: null"
        p = pay("ada", "bob", 100)
        self.assertEqual(p.status, 201)
        self.assertIn("authorization_id", p.body)
        self.assertIsNone(p.body["authorization_id"])
        self.assertIsNone(p.body["request_id"])
        rid = req("bob", "ada", 50).body["request_id"]
        pr = pay_req("ada", rid)
        self.assertIsNone(pr.body["authorization_id"])
        self.assertEqual(pr.body["request_id"], rid)
        for x in activity("ada")["payments"]:
            self.assertIn("authorization_id", x)
            self.assertIsNone(x["authorization_id"])
        self.me_inv("ada", held=0, available=10000 - 150, total_=10000 - 150)

    def test_payment_has_no_intermediate_hold(self):
        "[ME-04] POST /payments is an immediate transfer: no hold, no authorization record"
        pay("ada", "bob", 500)
        self.me_inv("ada", held=0, available=9500)
        self.assertEqual(auths_of("ada")["authorizations"], [])
        self.assertEqual(auths_of("bob")["authorizations"], [])


class Create(AzBase):
    def test_create_shape_and_defaults(self):
        "[AZ-01] 201 body per spec; defaults note '' / visibility public; expires_at = created_at + 600 s"
        r = authorize("ada", "bob", 2000)
        self.assertEqual(r.status, 201, r)
        self.az_shape(r.body, "ada", "bob", 2000, "open", 0, 2000, "", "public")
        self.assertIsNone(r.body["payment_id"])
        self.assertEqual(r.body["payment_ids"], [])
        d = parse_ts(r.body["expires_at"]) - parse_ts(r.body["created_at"])
        self.assertEqual(d.total_seconds(), 600)
        r2 = authorize("ada", "bob", 100, note="deposit", visibility="private")
        self.az_shape(r2.body, note="deposit", vis="private")

    def test_create_validation(self):
        "[AZ-02] amount range/type, self_payment, note, visibility, unknown handle, insufficient available"
        for a in (0, -1, 1000000001):
            self.err(authorize("ada", "bob", a), 422, "validation_failed")
        for form in ("true", '"100"', "10.5", "1e-1"):
            r = call("POST", "/authorizations", raw='{"to_handle":"bob","amount":%s}' % form, token=tok("ada"), key=k())
            self.err(r, 422, "validation_failed")
        self.err(authorize("ada", "ada", 5), 422, "self_payment")
        self.err(authorize("ada", "bob", 5, note="n" * 201), 422, "validation_failed")
        self.assertEqual(authorize("ada", "bob", 5, note="n" * 200).status, 201)
        for v in ("friends", "PUBLIC", None, 1, ""):
            self.err(authorize("ada", "bob", 5, visibility=v), 422, "validation_failed")
        self.err(authorize("ada", "nobody", 5), 404, "not_found")
        self.err(authorize("ada", "bob", 10000 + 1 - 5), 409, "insufficient_funds")  # 5 already held
        self.err(call("POST", "/authorizations", {"amount": 5}, token=tok("ada"), key=k()), 422, "validation_failed")
        self.err(call("POST", "/authorizations", {"to_handle": "bob"}, token=tok("ada"), key=k()), 422, "validation_failed")
        self.err(call("POST", "/authorizations", {"to_handle": 7, "amount": 5}, token=tok("ada"), key=k()), 400, "malformed_request")
        self.err(call("POST", "/authorizations", raw=b"{", token=tok("ada"), key=k()), 400, "malformed_request")
        self.assertEqual(authorize("ada", "bob", 1, junk="ignored").status, 201)  # unknown fields ignored
        self.err(authorize("dee", "bob", 1), 409, "insufficient_funds")

    def test_create_max_amount(self):
        "[AZ-03] 1000000000 is valid; 1000000001 is not"
        reset(azfixture(users=[user("ada", 3 * 10**9), user("bob", 0)], payments=[], requests=[]))
        self.assertEqual(authorize("ada", "bob", 10**9).status, 201)
        self.err(authorize("ada", "bob", 10**9 + 1), 422, "validation_failed")

    def test_insufficient_against_available_not_total(self):
        "[AZ-04] authorizing is checked against available: holds count, exact available ok, one more unit refused"
        self.assertEqual(authorize("ada", "bob", 6000).status, 201)
        self.err(authorize("ada", "cy", 4001), 409, "insufficient_funds")
        self.assertEqual(authorize("ada", "cy", 4000).status, 201)
        self.me_inv("ada", held=10000, available=0, total_=10000)
        self.err(authorize("ada", "cy", 1), 409, "insufficient_funds")
        self.err(authorize("dee", "bob", 1), 409, "insufficient_funds")

    def test_hold_not_a_feed_item(self):
        "[AZ-05] an open authorization never appears in GET /activity or GET /requests"
        a = authorize("ada", "bob", 700, visibility="public", note="hold-note").body
        for n in ("ada", "bob", "cy", "op"):
            body = activity(n, limit=200)
            self.assertFalse([p for p in body["payments"] if p["note"] == "hold-note" or p["payment_id"] == a["authorization_id"]], n)
            self.assertEqual(len(body["payments"]), len(feed_ids(n)))
        self.assertEqual({q["request_id"] for q in requests_of("ada", limit=200)["requests"]} & {a["authorization_id"]}, set())
        self.assertNotIn(a["authorization_id"], feed_ids("bob"))

    def test_hold_moves_no_money(self):
        "[AZ-06] a hold moves no money: totals unchanged for both parties and the sum"
        authorize("ada", "bob", 4000)
        self.assertEqual((bal("ada"), bal("bob")), (10000, 2500))
        self.assertEqual(total(), BASE_TOTAL)

    def test_held_funds_cannot_fund_payments(self):
        "[AZ-07] held funds cannot fund payments, request payments, settlement net debits or other authorizations"
        authorize("ada", "bob", 6000)  # ada: total 10000, available 4000
        self.err(pay("ada", "cy", 4001), 409, "insufficient_funds")
        rid = req("cy", "ada", 4001).body["request_id"]
        self.err(pay_req("ada", rid), 409, "insufficient_funds")
        self.err(settle("op", [T("ada", "bob", 4001)]), 409, "insufficient_funds")
        self.err(authorize("ada", "cy", 4001), 409, "insufficient_funds")
        # exactly available works on each path
        self.assertEqual(pay("ada", "cy", 1000).status, 201)
        rid2 = req("cy", "ada", 1000).body["request_id"]
        self.assertEqual(pay_req("ada", rid2).status, 201)
        self.assertEqual(settle("op", [T("ada", "bob", 1000)]).status, 201)
        self.assertEqual(authorize("ada", "cy", 1000).status, 201)
        self.me_inv("ada", held=7000, available=0, total_=7000)
        # pending request left stays payable only when funded; it was refused without state change
        q = [x for x in requests_of("ada")["requests"] if x["request_id"] == rid][0]
        self.assertEqual(q["status"], "pending")

    def test_settlement_net_debit_uses_available(self):
        "[AZ-08] settlement affordability: available after all transfers must be >= 0 (pass-through netting still works)"
        authorize("ada", "bob", 9000)  # ada available 1000
        self.err(settle("op", [T("ada", "dee", 1001)]), 409, "insufficient_funds")
        # ada nets to -1000 + 500 incoming from eve -> available 1000 - 1500 + 500 = 0 ok
        self.assertEqual(settle("op", [T("ada", "dee", 1500), T("eve", "ada", 500)]).status, 201)
        self.me_inv("ada", held=9000, available=0)
        self.err(settle("op", [T("ada", "dee", 1)]), 409, "insufficient_funds")

    def test_receiver_can_spend_only_available(self):
        "[AZ-09] the receiver's own holds reduce the receiver's spendable money, not the payer's"
        authorize("bob", "cy", 2500)
        self.me_inv("bob", held=2500, available=0)
        self.err(pay("bob", "ada", 1), 409, "insufficient_funds")
        self.assertEqual(pay("ada", "bob", 100).status, 201)
        self.me_inv("bob", held=2500, available=100, total_=2600)

    def test_idempotency_basics(self):
        "[AZ-10] POST /authorizations is idempotent: 201 then 200 identical, one hold; different body 409; per user; missing key 400"
        t = tok("ada")
        key = k()
        body = {"to_handle": "bob", "amount": 300, "note": "x"}
        a = call("POST", "/authorizations", body, token=t, key=key)
        b = call("POST", "/authorizations", body, token=t, key=key)
        c = call("POST", "/authorizations", raw='{ "note":"x", "amount":300, "to_handle":"bob" }', token=t, key=key)
        self.assertEqual((a.status, b.status, c.status), (201, 200, 200))
        self.assertEqual(a.body, b.body)
        self.assertEqual(a.body, c.body)
        self.me_inv("ada", held=300)
        self.assertEqual(len(auths_of("ada")["authorizations"]), 1)
        self.err(call("POST", "/authorizations", {**body, "amount": 301}, token=t, key=key), 409, "idempotency_key_reuse")
        self.err(call("POST", "/authorizations", {"to_handle": "bob", "amount": "bad"}, token=t, key=key), 409, "idempotency_key_reuse")
        self.err(call("POST", "/authorizations", body, token=t), 400, "missing_idempotency_key")
        self.err(call("POST", "/authorizations", body, token=t, key=""), 400, "missing_idempotency_key")
        self.err(call("POST", "/authorizations", body, token=t, key="z" * 256), 422, "validation_failed")
        self.assertEqual(call("POST", "/authorizations", body, token=t, key="z" * 255).status, 201)
        # same key, other user: independent
        x = call("POST", "/authorizations", {"to_handle": "cy", "amount": 5}, token=tok("bob"), key=key)
        self.assertEqual(x.status, 201)
        # same key on a different path (payment) is not a replay
        self.assertEqual(call("POST", "/payments", {"to_handle": "bob", "amount": 1}, token=t, key=key).status, 201)

    def test_failed_create_key_reusable(self):
        "[AZ-11] a key whose authorization failed with 4xx is a first use next time; replay is the original even if funds changed"
        key = k()
        self.err(authorize("ada", "bob", 20000, key=key), 409, "insufficient_funds")
        r = authorize("ada", "bob", 10000, key=key)
        self.assertEqual(r.status, 201)
        pay_all = pay("bob", "ada", 1)  # unrelated change
        again = authorize("ada", "bob", 10000, key=key)  # would be insufficient if re-evaluated
        self.assertEqual((again.status, again.body), (200, r.body))

    def test_requires_authentication(self):
        "[AZ-12] 401 on every authorization endpoint without a valid token"
        for method, path, body in (("POST", "/authorizations", {"to_handle": "bob", "amount": 1}), ("GET", "/authorizations", None),
                                   ("POST", "/authorizations/x/capture", {}), ("POST", "/authorizations/x/void", None)):
            for hdr in (None, {"Authorization": "Bearer nonsense"}):
                h = {"Accept": "application/json"}
                h.update(hdr or {})
                self.err(call(method, path, body, key=k(), headers=h), 401, "unauthenticated")


class Capture(AzBase):
    def make(self, amount=2000, **kw):
        a = authorize("ada", "bob", amount, **kw)
        self.assertEqual(a.status, 201, a)
        return a.body["authorization_id"]

    def test_full_capture_default(self):
        "[AZ-20] capture with {} takes the remaining amount: 201 payment with authorization_id, request_id null, note/visibility copied"
        aid = self.make(2000, note="deposit", visibility="private")
        r = capture("bob", aid)
        self.assertEqual(r.status, 201, r)
        self.payment_shape(r.body, "ada", "bob", 2000, "deposit", "private", request_id=None)
        self.assertEqual(r.body["authorization_id"], aid)
        self.assertEqual((bal("ada"), bal("bob")), (8000, 4500))
        a = az_get("bob", aid)
        self.az_shape(a, status="captured", captured=2000, remaining=0)
        self.assertEqual((a["payment_id"], a["payment_ids"]), (r.body["payment_id"], [r.body["payment_id"]]))
        self.me_inv("ada", held=0, available=8000, total_=8000)
        self.me_inv("bob", held=0, available=4500, total_=4500)
        self.assertEqual(total(), BASE_TOTAL)

    def test_capture_empty_body_and_explicit_amount_equal_effect(self):
        "[AZ-21] explicit full amount behaves like the default"
        aid = self.make(2000)
        r = capture("bob", aid, 2000)
        self.assertEqual(r.status, 201)
        self.assertEqual(az_get("ada", aid)["status"], "captured")

    def test_partial_capture_releases_remainder_immediately(self):
        "[AZ-22] capturing 1500 of 2000 (default final) returns 500 to the payer's available in the same step"
        aid = self.make(2000)
        self.me_inv("ada", held=2000, available=8000)
        r = capture("bob", aid, 1500)
        self.assertEqual(r.status, 201)
        self.assertEqual(r.body["amount"], 1500)
        self.me_inv("ada", held=0, available=8500, total_=8500)
        self.me_inv("bob", total_=4000)
        a = az_get("ada", aid)
        self.az_shape(a, status="captured", captured=1500, remaining=0)
        self.assertEqual(total(), BASE_TOTAL)

    def test_second_capture_after_final(self):
        "[AZ-23] a second capture after a final capture is 409 authorization_not_open (fresh key)"
        aid = self.make(2000)
        self.assertEqual(capture("bob", aid, 1500).status, 201)
        self.err(capture("bob", aid, 100), 409, "authorization_not_open")
        self.err(capture("bob", aid), 409, "authorization_not_open")
        self.assertEqual((bal("ada"), bal("bob")), (8500, 4000))

    def test_extended_capture(self):
        "[AZ-24] final:false keeps the remainder held; captures accumulate; capturing the whole remainder closes it"
        aid = self.make(2000)
        r1 = capture("bob", aid, 700, final=False)
        self.assertEqual(r1.status, 201, r1)
        a = az_get("ada", aid)
        self.az_shape(a, status="open", captured=700, remaining=1300)
        self.assertEqual(a["payment_ids"], [r1.body["payment_id"]])
        self.me_inv("ada", held=1300, available=10000 - 700 - 1300, total_=9300)
        r2 = capture("bob", aid, 500, final=False)
        self.assertEqual(r2.status, 201)
        a = az_get("bob", aid)
        self.az_shape(a, status="open", captured=1200, remaining=800)
        self.assertEqual(a["payment_ids"], [r1.body["payment_id"], r2.body["payment_id"]])
        self.assertEqual(a["payment_id"], r2.body["payment_id"])
        self.me_inv("ada", held=800, available=8800 - 800, total_=8800)
        r3 = capture("bob", aid, 800, final=False)  # whole remainder closes it even with final:false
        self.assertEqual(r3.status, 201)
        a = az_get("bob", aid)
        self.az_shape(a, status="captured", captured=2000, remaining=0)
        self.assertEqual(a["payment_ids"], [r1.body["payment_id"], r2.body["payment_id"], r3.body["payment_id"]])
        self.me_inv("ada", held=0, available=8000)
        self.err(capture("bob", aid, 1), 409, "authorization_not_open")
        self.assertEqual(total(), BASE_TOTAL)

    def test_extended_then_final_releases_remainder(self):
        "[AZ-25] a final capture after nonfinal ones closes and releases whatever is left"
        aid = self.make(2000)
        capture("bob", aid, 600, final=False)
        r = capture("bob", aid, 400)  # final (default)
        self.assertEqual(r.status, 201)
        a = az_get("ada", aid)
        self.az_shape(a, status="captured", captured=1000, remaining=0)
        self.me_inv("ada", held=0, available=9000, total_=9000)
        explicit = self.make(1000)
        capture("bob", explicit, 300, final=False)
        capture("bob", explicit, 100, final=True)
        self.az_shape(az_get("ada", explicit), status="captured", captured=400, remaining=0)

    def test_extended_omitted_amount_is_remainder(self):
        "[AZ-26] with omitted amount the capture takes the remaining amount, closing it (even with final:false)"
        aid = self.make(2000)
        capture("bob", aid, 700, final=False)
        r = capture("bob", aid, final=False)
        self.assertEqual(r.status, 201)
        self.assertEqual(r.body["amount"], 1300)
        self.az_shape(az_get("ada", aid), status="captured", captured=2000, remaining=0)

    def test_capture_exceeds_remaining(self):
        "[AZ-27] amount above the uncaptured remainder -> 422 capture_exceeds_authorization; compared with the REMAINING amount"
        aid = self.make(2000)
        self.err(capture("bob", aid, 2001), 422, "capture_exceeds_authorization")
        capture("bob", aid, 700, final=False)
        self.err(capture("bob", aid, 1301), 422, "capture_exceeds_authorization")
        self.err(capture("bob", aid, 2000), 422, "capture_exceeds_authorization")
        self.assertEqual(capture("bob", aid, 1300, final=False).status, 201)
        self.az_shape(az_get("ada", aid), status="captured", captured=2000)
        self.me_inv("ada", held=0, total_=8000)

    def test_capture_validation(self):
        "[AZ-28] amount below 1 or not an integer -> 422; wrong-typed final is rejected (400/422); unknown fields ignored"
        aid = self.make(2000)
        for form in ("0", "-1", "1.5", "true", '"100"', "1e-1", "null"):
            r = call("POST", "/authorizations/%s/capture" % aid, raw='{"amount":%s}' % form, token=tok("bob"), key=k())
            if form == "null":
                self.err4xx(r, {"validation_failed", "malformed_request"})
            else:
                self.err(r, 422, "validation_failed")
        for v in ('"yes"', "1", "0", "null", "[]"):
            r = call("POST", "/authorizations/%s/capture" % aid, raw='{"amount":100,"final":%s}' % v, token=tok("bob"), key=k())
            self.err4xx(r, {"validation_failed", "malformed_request"})
        r = call("POST", "/authorizations/%s/capture" % aid, {"amount": 100, "zzz": 1}, token=tok("bob"), key=k())
        self.assertEqual(r.status, 201)
        self.assertEqual(az_get("ada", aid)["captured_amount"], 100)

    def test_capture_float_integral_forms(self):
        "[AZ-29] integral numeric forms 1000.0 / 1e3 are valid capture amounts"
        aid = self.make(5000)
        r = call("POST", "/authorizations/%s/capture" % aid, raw='{"amount":1000.0,"final":false}', token=tok("bob"), key=k())
        self.assertEqual(r.status, 201, r)
        r = call("POST", "/authorizations/%s/capture" % aid, raw='{"amount":1e3,"final":false}', token=tok("bob"), key=k())
        self.assertEqual(r.status, 201, r)
        self.assertEqual(az_get("ada", aid)["captured_amount"], 2000)

    def test_capture_permissions(self):
        "[AZ-30] only the receiver may capture: payer, strangers and operators get 403; unknown id 404"
        aid = self.make(2000)
        for who in ("ada", "cy", "op", "eve"):
            self.err(capture(who, aid), 403, "forbidden")
        self.err(capture("bob", "a_nope"), 404, "not_found")
        self.err(capture("bob", "x" * 200), 404, "not_found")
        self.az_shape(az_get("ada", aid), status="open", captured=0, remaining=2000)
        self.assertEqual(bal("ada"), 10000)

    def test_capture_closed_states(self):
        "[AZ-31] capturing a voided / captured authorization is 409 authorization_not_open"
        a = self.make(500)
        void("ada", a)
        self.err(capture("bob", a), 409, "authorization_not_open")
        b = self.make(500)
        capture("bob", b)
        self.err(capture("bob", b), 409, "authorization_not_open")

    def test_capture_appears_in_feed_by_visibility(self):
        "[AZ-32] the capture payment is a normal feed item (visibility copied); private is hidden from third parties"
        pub = capture("bob", self.make(100, visibility="public", note="pubnote")).body["payment_id"]
        prv = capture("bob", self.make(200, visibility="private", note="prvnote")).body["payment_id"]
        for who, sees_pub, sees_prv in (("ada", 1, 1), ("bob", 1, 1), ("cy", 1, 0), ("op", 1, 0)):
            ids = feed_ids(who)
            self.assertEqual(pub in ids, bool(sees_pub), who)
            self.assertEqual(prv in ids, bool(sees_prv), who)
        item = [p for p in activity("bob", limit=200)["payments"] if p["payment_id"] == pub][0]
        self.assertEqual(item["authorization_id"] is not None, True)
        self.assertIsNone(item["request_id"])
        self.assertIsNone(item["settlement_id"])

    def test_capture_may_spend_reserved_money(self):
        "[AZ-33] a capture spends the reserved money even when available is 0"
        reset(azfixture(users=[user("ada", 2000), user("bob", 0)], payments=[], requests=[]))
        a = authorize("ada", "bob", 2000).body["authorization_id"]
        self.me_inv("ada", held=2000, available=0)
        self.err(pay("ada", "bob", 1), 409, "insufficient_funds")
        self.assertEqual(capture("bob", a).status, 201)
        self.me_inv("ada", held=0, available=0, total_=0)
        self.me_inv("bob", total_=2000)

    def test_two_holds_independent(self):
        "[AZ-34] capturing one hold never touches another hold's reservation"
        a1, a2 = self.make(3000), self.make(4000)
        capture("bob", a1, 1000)
        self.me_inv("ada", held=4000, available=10000 - 1000 - 4000, total_=9000)
        self.az_shape(az_get("ada", a2), status="open", captured=0, remaining=4000)

    def test_capture_idempotency(self):
        "[AZ-35] capture replay: 200 identical, money once; {} vs {amount:2000} are different bodies -> 409; per path/user"
        aid = self.make(2000)
        key = k()
        a = capture("bob", aid, key=key, body={})
        b = capture("bob", aid, key=key, body={})
        self.assertEqual((a.status, b.status), (201, 200))
        self.assertEqual(a.body, b.body)
        self.err(capture("bob", aid, key=key, body={"amount": 2000}), 409, "idempotency_key_reuse")
        self.err(capture("bob", aid, key=key, body={"amount": 1}), 409, "idempotency_key_reuse")
        self.err(capture("bob", aid, key=key, body={"final": True}), 409, "idempotency_key_reuse")
        self.assertEqual((bal("ada"), bal("bob")), (8000, 4500))
        aid2 = self.make(2000)
        key2 = k()
        self.assertEqual(capture("bob", aid2, key=key2, body={"amount": 2000}).status, 201)
        self.err(capture("bob", aid2, key=key2, body={}), 409, "idempotency_key_reuse")
        # same key different authorization = different path
        aid3, aid4 = self.make(100), self.make(100)
        key3 = k()
        self.assertEqual(capture("bob", aid3, key=key3).status, 201)
        self.assertEqual(capture("bob", aid4, key=key3).status, 201)

    def test_capture_replay_after_state_changes(self):
        "[AZ-36] a successful capture replay returns the original response although the authorization has since closed or been voided"
        aid = self.make(2000)
        key = k()
        a = capture("bob", aid, 700, key=key, final=False)
        capture("bob", aid, 1300, final=False)  # closes it
        b = capture("bob", aid, 700, key=key, final=False)
        self.assertEqual((b.status, b.body), (200, a.body))
        self.assertEqual(bal("bob"), 4500)
        aid2 = self.make(100)
        k2 = k()
        x = capture("bob", aid2, 100, key=k2)
        y = capture("bob", aid2, 100, key=k2)
        self.assertEqual((x.status, y.status), (201, 200))
        # key outranks validation and state
        self.err(capture("bob", aid2, 5, key=k2), 409, "idempotency_key_reuse")
        self.err(capture("bob", aid2, key=k2, body={"amount": "bad"}), 409, "idempotency_key_reuse")

    def test_capture_failed_key_reusable(self):
        "[AZ-37] a capture refused with 4xx does not claim the key"
        aid = self.make(500)
        key = k()
        self.err(capture("bob", aid, 501, key=key), 422, "capture_exceeds_authorization")
        self.assertEqual(capture("bob", aid, 500, key=key).status, 201)
        key2 = k()
        self.err(capture("cy", aid, key=key2), 403, "forbidden")
        aid2 = self.make(500)
        self.assertEqual(capture("bob", aid2, key=key2).status, 201)

    def test_capture_missing_key(self):
        "[AZ-38] capture without Idempotency-Key -> 400 missing_idempotency_key; no money moves"
        aid = self.make(500)
        self.err(call("POST", "/authorizations/%s/capture" % aid, {}, token=tok("bob")), 400, "missing_idempotency_key")
        self.err(call("POST", "/authorizations/%s/capture" % aid, {}, token=tok("bob"), key=""), 400, "missing_idempotency_key")
        self.err(call("POST", "/authorizations/%s/capture" % aid, {}, token=tok("bob"), key="k" * 256), 422, "validation_failed")
        self.assertEqual(bal("bob"), 2500)


class Void(AzBase):
    def make(self, amount=2000):
        return authorize("ada", "bob", amount).body["authorization_id"]

    def test_void(self):
        "[AZ-40] payer voids: 200, status voided, hold released; no money moved; no key needed"
        aid = self.make(2000)
        r = void("ada", aid)
        self.assertEqual(r.status, 200, r)
        self.az_shape(r.body, status="voided", captured=0, remaining=0)
        self.me_inv("ada", held=0, available=10000, total_=10000)
        self.assertEqual(bal("bob"), 2500)
        self.assertEqual(self.az_status("bob", aid), "voided")

    def az_status(self, who, aid):
        return az_get(who, aid)["status"]

    def test_void_twice_is_200(self):
        "[AZ-41] voiding an already-voided authorization is 200 with the current state"
        aid = self.make()
        void("ada", aid)
        r = void("ada", aid)
        self.assertEqual(r.status, 200)
        self.assertEqual(r.body["status"], "voided")
        self.me_inv("ada", held=0)

    def test_void_captured_conflict(self):
        "[AZ-42] void on a captured authorization -> 409 authorization_not_open"
        aid = self.make()
        capture("bob", aid)
        self.err(void("ada", aid), 409, "authorization_not_open")
        self.assertEqual(bal("bob"), 4500)

    def test_void_permissions(self):
        "[AZ-43] only the payer may void: receiver, strangers, operators 403; unknown 404"
        aid = self.make()
        for who in ("bob", "cy", "op"):
            self.err(void(who, aid), 403, "forbidden")
        self.err(void("ada", "a_nope"), 404, "not_found")
        self.az_shape(az_get("ada", aid), status="open")
        self.assertEqual(call("POST", "/authorizations/%s/void" % aid, token=tok("ada"), key="ignored").status, 200)  # stray key is harmless

    def test_void_after_partial_capture(self):
        "[AZ-44] void after nonfinal captures releases only the remainder and preserves the capture records"
        aid = self.make(2000)
        r1 = capture("bob", aid, 600, final=False)
        r = void("ada", aid)
        self.assertEqual(r.status, 200)
        self.az_shape(r.body, status="voided", captured=600, remaining=0)
        self.assertEqual(r.body["payment_ids"], [r1.body["payment_id"]])
        self.me_inv("ada", held=0, available=9400, total_=9400)
        self.me_inv("bob", total_=3100)
        self.err(capture("bob", aid, 1), 409, "authorization_not_open")
        self.assertEqual(void("ada", aid).status, 200)
        self.assertEqual(total(), BASE_TOTAL)

    def test_void_then_pay_with_released_funds(self):
        "[AZ-45] after void the released funds can be spent"
        aid = authorize("ada", "bob", 10000).body["authorization_id"]
        self.err(pay("ada", "cy", 1), 409, "insufficient_funds")
        void("ada", aid)
        self.assertEqual(pay("ada", "cy", 10000).status, 201)


class Listing(AzBase):
    def test_scoping_filters(self):
        "[AZ-50] GET /authorizations: only the caller's; direction + status filters; envelope"
        a = authorize("ada", "bob", 100).body["authorization_id"]
        b = authorize("bob", "ada", 200).body["authorization_id"]
        c = authorize("cy", "eve", 300).body["authorization_id"]
        capture("ada", b)
        void("ada", a)
        ids = lambda who, **q: {x["authorization_id"] for x in auths_of(who, **q)["authorizations"]}
        self.assertEqual(ids("ada"), {a, b})
        self.assertEqual(ids("bob"), {a, b})
        self.assertEqual(ids("cy"), {c})
        self.assertEqual(ids("dee"), set())
        self.assertEqual(ids("op"), set())
        self.assertEqual(ids("ada", direction="outgoing"), {a})
        self.assertEqual(ids("ada", direction="incoming"), {b})
        self.assertEqual(ids("ada", status="voided"), {a})
        self.assertEqual(ids("ada", status="captured"), {b})
        self.assertEqual(ids("ada", status="open"), set())
        self.assertEqual(ids("ada", direction="outgoing", status="voided"), {a})
        self.assertEqual(ids("ada", direction="incoming", status="voided"), set())
        self.assertEqual(ids("ada", unknown="x"), {a, b})
        body = auths_of("ada")
        self.assertIsInstance(body["has_more"], bool)
        for x in body["authorizations"]:
            self.assertIn(x["status"], ("open", "captured", "voided", "expired"))

    def test_param_validation(self):
        "[AZ-51] unknown direction/status, limit 1..200, offset >= 0, plain digits only -> 422"
        t = tok("ada")
        h = {"Accept": "application/json"}
        for qs in ("direction=sideways", "direction=INCOMING", "status=done", "status=OPEN", "status=pending", "direction=", "limit=0",
                   "limit=201", "limit=-1", "limit=abc", "limit=1e2", "limit=4.0", "limit=+4", "limit=", "offset=-1", "offset=x", "offset=1.0",
                   "offset="):
            self.err(call("GET", "/authorizations?" + qs, token=t, headers=h), 422, "validation_failed")
        for qs in ("limit=1", "limit=200", "offset=0", "offset=99999"):
            self.assertEqual(call("GET", "/authorizations?" + qs, token=t, headers=h).status, 200, qs)

    def test_order_and_paging(self):
        "[AZ-52] newest first by created_at; limit/offset/has_more"
        ids = []
        for i in range(3):
            ids.append(authorize("ada", "bob", 10 + i).body["authorization_id"])
            time.sleep(1.1)
        got = [x["authorization_id"] for x in auths_of("ada", direction="outgoing")["authorizations"]]
        self.assertEqual(got, ids[::-1])
        r = auths_of("ada", limit=2)
        self.assertEqual(([x["authorization_id"] for x in r["authorizations"]], r["has_more"]), (ids[::-1][:2], True))
        r = auths_of("ada", limit=2, offset=2)
        self.assertEqual(([x["authorization_id"] for x in r["authorizations"]], r["has_more"]), (ids[::-1][2:], False))
        r = auths_of("ada", limit=3)
        self.assertEqual((len(r["authorizations"]), r["has_more"]), (3, False))
        r = auths_of("ada", limit=1, offset=9)
        self.assertEqual((r["authorizations"], r["has_more"]), ([], False))

    def test_remaining_amount_matches_held(self):
        "[AZ-53] invariant: held == sum(remaining_amount of open outgoing); captured_amount == sum of capture payments"
        a = authorize("ada", "bob", 3000).body["authorization_id"]
        b = authorize("ada", "cy", 2000).body["authorization_id"]
        p = capture("bob", a, 1000, final=False).body
        q = capture("bob", a, 500, final=False).body
        listed = auths_of("ada", direction="outgoing", status="open")["authorizations"]
        self.assertEqual(sum(x["remaining_amount"] for x in listed), me("ada")["held"])
        row = az_get("ada", a)
        self.assertEqual(row["captured_amount"], p["amount"] + q["amount"])
        self.assertEqual(row["captured_amount"] + row["remaining_amount"], row["amount"])
        void("ada", b)
        self.assertEqual(sum(x["remaining_amount"] for x in auths_of("ada", status="open")["authorizations"]), me("ada")["held"])


class ResetValidation(AzBase):
    def test_reset_with_seeded_holds(self):
        "[AX-01] seeded open holds: available derived (balance is total); authorizations listed with their ids; omission = empty"
        fx = azfixture([seed_auth("a_1", "ada", "bob", 2000, note="deposit"), seed_auth("a_2", "ada", "cy", 500, visibility="private")])
        reset(fx)
        self.me_inv("ada", held=2500, available=7500, total_=10000)
        self.me_inv("bob", held=0, available=2500)
        rows = {a["authorization_id"]: a for a in auths_of("ada", limit=200)["authorizations"]}
        self.assertEqual(set(rows), {"a_1", "a_2"})
        self.az_shape(rows["a_1"], "ada", "bob", 2000, "open", 0, 2000, "deposit", "public")
        self.assertEqual({a["authorization_id"] for a in auths_of("bob")["authorizations"]}, {"a_1"})
        self.assertEqual(auths_of("eve")["authorizations"], [])
        self.assertNotIn("a_1", feed_ids("ada"))
        reset(base_fixture())  # authorizations omitted
        self.assertEqual(auths_of("ada")["authorizations"], [])
        self.me_inv("ada", held=0, available=10000)

    def test_seeded_hold_fully_usable(self):
        "[AX-02] a seeded open hold can be captured (full / partial) and voided"
        reset(azfixture([seed_auth("a_1", "ada", "bob", 2000, visibility="private", note="d"), seed_auth("a_2", "ada", "cy", 1000)]))
        r = capture("bob", "a_1", 1500)
        self.assertEqual(r.status, 201, r)
        self.payment_shape(r.body, "ada", "bob", 1500, "d", "private", request_id=None)
        self.assertEqual(r.body["authorization_id"], "a_1")
        self.me_inv("ada", held=1000, available=10000 - 1500 - 1000, total_=8500)
        self.assertEqual(void("ada", "a_2").status, 200)
        self.me_inv("ada", held=0, available=8500)

    def test_oversubscribed_holds_rejected(self):
        "[AX-03] unexpired open holds summing above the payer's balance -> 422 validation_failed, nothing changed; equal is fine"
        self.assertEqual(pay("ada", "bob", 1).status, 201)
        before = (bal("ada"), bal("bob"))
        bad = azfixture([seed_auth("a_1", "dee", "bob", 1)])  # dee has 0
        self.err(call("POST", "/_test/reset", bad, timeout=10), 422, "validation_failed")
        bad = azfixture([seed_auth("a_1", "ada", "bob", 6000), seed_auth("a_2", "ada", "cy", 4001)])
        self.err(call("POST", "/_test/reset", bad, timeout=10), 422, "validation_failed")
        self.assertEqual((bal("ada"), bal("bob")), before)  # untouched world, token still valid
        self.assertEqual(auths_of("ada")["authorizations"], [])
        ok = azfixture([seed_auth("a_1", "ada", "bob", 6000), seed_auth("a_2", "ada", "cy", 4000)])
        reset(ok)
        self.me_inv("ada", held=10000, available=0, total_=10000)

    def test_expired_and_closed_seeds_do_not_count(self):
        "[AX-04] seeded expired / captured / voided / past-expiry holds hold nothing and are not oversubscription"
        fx = azfixture([seed_auth("a_1", "ada", "bob", 9000, status="expired", expires_in=-7200),
                        seed_auth("a_2", "ada", "bob", 9000, status="captured", expires_in=7200),
                        seed_auth("a_3", "ada", "bob", 9000, status="voided", expires_in=7200),
                        seed_auth("a_4", "ada", "bob", 9000, status="open", expires_in=-7200),  # open but already past
                        seed_auth("a_5", "ada", "bob", 4000, status="open", expires_in=7200)])
        reset(fx)
        self.me_inv("ada", held=4000, available=6000, total_=10000)
        rows = {a["authorization_id"]: a["status"] for a in auths_of("ada", limit=200)["authorizations"]}
        self.assertEqual(rows, {"a_1": "expired", "a_2": "captured", "a_3": "voided", "a_4": "expired", "a_5": "open"})
        self.assertEqual({a["authorization_id"] for a in auths_of("ada", status="expired")["authorizations"]}, {"a_1", "a_4"})
        self.assertEqual({a["authorization_id"] for a in auths_of("ada", status="open")["authorizations"]}, {"a_5"})
        for aid in ("a_1", "a_4"):
            self.err4xx(capture("bob", aid), {"authorization_expired", "authorization_not_open"})
            self.assertEqual(self.status_of(capture("bob", aid)), 409)
        for aid in ("a_2", "a_3"):
            self.err(capture("bob", aid), 409, "authorization_not_open")
        self.err(void("ada", "a_1"), 409, "authorization_not_open")
        self.err(void("ada", "a_4"), 409, "authorization_not_open")
        self.assertEqual(void("ada", "a_3").status, 200)  # already voided -> 200

    def status_of(self, r):
        return r.status

    def test_reset_ttl_validation(self):
        "[AX-05] authorization_ttl_seconds: positive integer when supplied (0, negatives, fractions, strings, booleans -> 422); omitted = 600"
        for bad in (0, -1, -600, 1.5, "600", True, False, [], {}):
            self.err(call("POST", "/_test/reset", azfixture(ttl=bad), timeout=10), 422, "validation_failed")
        self.assertEqual(bal("ada"), 10000)
        reset(azfixture(ttl=3600))
        a = authorize("ada", "bob", 5).body
        self.assertEqual((parse_ts(a["expires_at"]) - parse_ts(a["created_at"])).total_seconds(), 3600)
        reset()
        a = authorize("ada", "bob", 5).body
        self.assertEqual((parse_ts(a["expires_at"]) - parse_ts(a["created_at"])).total_seconds(), 600)  # default

    def test_ttl_applies_per_new_authorization_not_seeded(self):
        "[AX-06] ttl applies to API-created authorizations; seeded ones keep their absolute expires_at"
        reset(azfixture([seed_auth("a_1", "ada", "bob", 100, expires_in=5400)], ttl=900))
        seeded = az_get("ada", "a_1")
        self.assertLess(abs(parse_ts(seeded["expires_at"]).timestamp() - (time.time() + 5400)), 30)
        a = authorize("ada", "bob", 5).body
        self.assertEqual((parse_ts(a["expires_at"]) - parse_ts(a["created_at"])).total_seconds(), 900)

    def test_reset_clears_authorizations_and_keys(self):
        "[AX-07] reset removes holds and authorization idempotency keys"
        key = k()
        authorize("ada", "bob", 100, key=key)
        reset()
        self.me_inv("ada", held=0, available=10000)
        r = authorize("ada", "cy", 55, key=key)  # different body, same key: first use in the new world
        self.assertEqual(r.status, 201)


class Expiry(AzBase):
    def test_clock_expiry_without_any_request_at_the_deadline(self):
        "[AX-10] short TTL: after the deadline, with no request in between, list shows expired, /me available is restored, capture refused"
        reset(azfixture(ttl=2))
        a = authorize("ada", "bob", 3000).body
        aid = a["authorization_id"]
        self.assertEqual((parse_ts(a["expires_at"]) - parse_ts(a["created_at"])).total_seconds(), 2)
        self.me_inv("ada", held=3000, available=7000)
        left = parse_ts(a["expires_at"]).timestamp() - time.time()
        time.sleep(max(0, left) + 1.2)  # no requests during this sleep
        m = self.me_inv("ada", held=0, available=10000, total_=10000)
        row = az_get("ada", aid)
        self.assertEqual(row["status"], "expired")
        self.assertEqual(row["remaining_amount"], 0)
        self.assertEqual(az_get("bob", aid)["status"], "expired")
        self.assertEqual({x["authorization_id"] for x in auths_of("ada", status="expired")["authorizations"]}, {aid})
        self.assertEqual(auths_of("ada", status="open")["authorizations"], [])
        self.err4xx(capture("bob", aid), {"authorization_expired"})
        self.assertEqual(capture("bob", aid).status, 409)
        self.err(void("ada", aid), 409, "authorization_not_open")
        self.assertEqual((bal("ada"), bal("bob")), (10000, 2500))
        self.assertEqual(total(), BASE_TOTAL)

    def test_expired_hold_funds_released_for_payments(self):
        "[AX-11] the released remainder is spendable after expiry (first write after the deadline sees it)"
        reset(azfixture(ttl=2))
        authorize("ada", "bob", 10000)
        self.err(pay("ada", "cy", 1), 409, "insufficient_funds")
        time.sleep(3.2)
        self.assertEqual(pay("ada", "cy", 10000).status, 201)
        self.me_inv("ada", held=0, available=0, total_=0)

    def test_expiry_preserves_partial_captures(self):
        "[AX-12] expiry of a partially captured authorization releases only the remainder and keeps the capture records"
        reset(azfixture(ttl=3))
        aid = authorize("ada", "bob", 2000).body["authorization_id"]
        r = capture("bob", aid, 500, final=False)
        self.assertEqual(r.status, 201)
        self.me_inv("ada", held=1500, available=8000, total_=9500)
        time.sleep(3.5)
        row = az_get("ada", aid)
        self.az_shape(row, status="expired", captured=500, remaining=0)
        self.assertEqual(row["payment_ids"], [r.body["payment_id"]])
        self.me_inv("ada", held=0, available=9500, total_=9500)
        self.me_inv("bob", total_=3000)
        self.assertEqual(total(), BASE_TOTAL)

    def test_expiry_on_every_read_surface(self):
        "[AX-13] expiry is reflected by /me, /authorizations (both parties, filters) and payments, independently of who reads first"
        reset(azfixture(ttl=2))
        aid = authorize("ada", "bob", 1000).body["authorization_id"]
        time.sleep(3.2)
        # the very first read after the deadline is by the RECEIVER's list: must already be expired
        self.assertEqual(auths_of("bob", status="open")["authorizations"], [])
        self.assertEqual(az_get("bob", aid)["status"], "expired")
        reset(azfixture(ttl=2))
        aid = authorize("ada", "bob", 1000).body["authorization_id"]
        time.sleep(3.2)
        self.assertEqual(me("ada")["available"], 10000)  # first read is /me
        reset(azfixture(ttl=2))
        authorize("ada", "bob", 1000)
        time.sleep(3.2)
        self.assertEqual(authorize("ada", "cy", 10000).status, 201)  # first touch is a write that needs the released funds

    def test_capture_before_deadline_succeeds(self):
        "[AX-14] captures right before expiry still work; nothing is lost to a short ttl"
        reset(azfixture(ttl=5))
        aid = authorize("ada", "bob", 1000).body["authorization_id"]
        self.assertEqual(capture("bob", aid, 400, final=False).status, 201)
        self.az_shape(az_get("ada", aid), status="open", captured=400, remaining=600)

    def test_seeded_future_expiry_expires_on_the_clock(self):
        "[AX-15] a seeded hold expiring in the near future (>= 1 h is the contract; the clock logic still applies) is open now"
        reset(azfixture([seed_auth("a_1", "ada", "bob", 1000, expires_in=3600)]))
        row = az_get("ada", "a_1")
        self.assertEqual(row["status"], "open")
        self.assertLess(abs(parse_ts(row["expires_at"]).timestamp() - (time.time() + 3600)), 30)
        self.assertEqual(parse_ts(row["expires_at"]).utcoffset().total_seconds() % 60, 0)


if __name__ == "__main__":
    unittest.main()
