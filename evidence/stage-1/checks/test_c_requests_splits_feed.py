"""Requests, splits, activity feed."""
import time
import unittest

from lib import *


def post(path, name, body=None, key=None):
    return call("POST", path, body, token=tok(name), key=key)


class Requests(Base):
    def test_create_shape(self):
        "[REQ-01] 201 request shape; pending; payment_id null; no visibility field"
        r = req("bob", "ada", 1200, note="taxi")
        self.assertEqual(r.status, 201, r)
        self.request_shape(r.body, "bob", "ada", 1200, "pending", "taxi")
        self.assertEqual(r.body["currency"], "EUR")
        self.assertEqual(req("bob", "ada", 5).body["note"], "")

    def test_request_may_exceed_payer_balance(self):
        "[REQ-02] request above the payer's balance is legal; pending; no money moves"
        r = req("bob", "dee", 999999)
        self.assertEqual(r.status, 201)
        self.assertEqual(r.body["status"], "pending")
        self.assertEqual((bal("bob"), bal("dee")), (2500, 0))

    def test_create_validation(self):
        "[REQ-03] amount range, self_request, note length, unknown handle, missing fields"
        self.err(req("bob", "bob", 5), 422, "self_request")
        for a in (0, -5, 1000000001):
            self.err(req("bob", "ada", a), 422, "validation_failed")
        for form in ("true", '"5"', "5.5", "null", "[]"):
            r = call("POST", "/requests", raw='{"payer_handle":"ada","amount":%s}' % form, token=tok("bob"), key=k())
            self.err4xx(r, {"validation_failed", "malformed_request"})
            if form in ("true", '"5"', "5.5"):
                self.err(r, 422, "validation_failed")
        self.assertEqual(req("bob", "ada", 1, note="n" * 200).status, 201)
        self.err(req("bob", "ada", 1, note="n" * 201), 422, "validation_failed")
        self.err(req("bob", "nobody", 5), 404, "not_found")
        self.err(post("/requests", "bob", {"amount": 5}, k()), 422, "validation_failed")
        self.err(post("/requests", "bob", {"payer_handle": "ada"}, k()), 422, "validation_failed")
        self.err(post("/requests", "bob", {"payer_handle": 5, "amount": 5}, k()), 400, "malformed_request")
        self.assertEqual(req("bob", "ada", 1000000000).status, 201)
        self.assertEqual(req("bob", "ada", 1, extra="ignored").status, 201)

    def test_new_user_can_be_asked_immediately(self):
        "[REQ-04] a freshly signed-up user can be requested from at once"
        signup("new.payer@example.com")
        r = req("bob", "new_payer", 50)
        self.assertEqual(r.status, 201)
        self.assertEqual(r.body["payer_handle"], "new_payer")

    def test_pay_moves_money_and_marks_paid(self):
        "[REQ-05] pay: 201 payment with request_id; request becomes paid with payment_id; money moves once"
        rid = req("bob", "ada", 1200, note="taxi").body["request_id"]
        r = pay_req("ada", rid)
        self.assertEqual(r.status, 201, r)
        self.payment_shape(r.body, "ada", "bob", 1200, None, "public", request_id=rid)
        self.assertEqual((bal("ada"), bal("bob")), (8800, 3700))
        q = [x for x in requests_of("bob")["requests"] if x["request_id"] == rid][0]
        self.request_shape(q, status="paid")
        self.assertEqual(q["payment_id"], r.body["payment_id"])
        self.assertEqual(total(), BASE_TOTAL)

    def test_pay_seeded_request(self):
        "[REQ-06] a seeded pending request (rq_1) can be paid by its payer; seeded non-pending cannot"
        r = pay_req("ada", "rq_1")
        self.assertEqual(r.status, 201, r)
        self.assertEqual(r.body["request_id"], "rq_1")
        self.assertEqual(r.body["amount"], 1200)
        self.err(pay_req("ada", "rq_4"), 409, "request_not_pending")
        self.err(pay_req("ada", "rq_5"), 409, "request_not_pending")

    def test_pay_visibility_is_the_payers_choice(self):
        "[REQ-07] visibility belongs to the payment, chosen by the payer, default public"
        r1 = req("bob", "ada", 10).body["request_id"]
        r2 = req("bob", "ada", 11).body["request_id"]
        p1 = pay_req("ada", r1, body={"visibility": "private"}).body
        p2 = pay_req("ada", r2).body
        self.assertEqual((p1["visibility"], p2["visibility"]), ("private", "public"))
        self.assertNotIn(p1["payment_id"], feed_ids("cy"))
        self.assertIn(p2["payment_id"], feed_ids("cy"))
        for n in ("ada", "bob"):
            self.assertIn(p1["payment_id"], feed_ids(n))  # private is hidden from third parties, not from its receiver
        self.err(pay_req("ada", req("bob", "ada", 1).body["request_id"], body={"visibility": "secret"}), 422, "validation_failed")
        self.err(pay_req("ada", req("bob", "ada", 1).body["request_id"], body={"visibility": None}), 422, "validation_failed")

    def test_requester_cannot_set_visibility(self):
        "[REQ-08] request creation ignores a visibility field and exposes none"
        r = req("bob", "ada", 5, visibility="private")
        self.assertEqual(r.status, 201)
        self.assertNotIn("visibility", r.body)

    def test_pay_insufficient_then_funded(self):
        "[REQ-09] paying while short -> 409 insufficient_funds, nothing changes; request stays pending; becomes payable once funded"
        rid = req("bob", "dee", 300).body["request_id"]
        before = (bal("dee"), bal("bob"), feed_ids("bob"))
        self.err(pay_req("dee", rid), 409, "insufficient_funds")
        self.assertEqual((bal("dee"), bal("bob"), feed_ids("bob")), before)
        q = [x for x in requests_of("dee")["requests"] if x["request_id"] == rid][0]
        self.assertEqual((q["status"], q["payment_id"]), ("pending", None))
        pay("ada", "dee", 299)
        self.err(pay_req("dee", rid), 409, "insufficient_funds")
        pay("ada", "dee", 1)
        r = pay_req("dee", rid)
        self.assertEqual(r.status, 201)
        self.assertEqual(bal("dee"), 0)
        self.assertEqual(total(), BASE_TOTAL)

    def test_pay_permissions(self):
        "[REQ-10] only the payer may pay: requester -> 403 forbidden; unrelated user 403/404; unknown 404"
        rid = req("bob", "ada", 10).body["request_id"]
        self.err(pay_req("bob", rid), 403, "forbidden")
        r = pay_req("cy", rid)
        self.assertIn(r.status, (403, 404), r)
        self.err4xx(r, {"forbidden", "not_found"})
        r = pay_req("op", rid)  # settlement operator gains nothing on requests
        self.assertIn(r.status, (403, 404), r)
        self.err(pay_req("ada", "rq_does_not_exist"), 404, "not_found")
        self.err(pay_req("ada", "x" * 200), 404, "not_found")
        self.assertEqual(bal("ada"), 10000)

    def test_pay_non_pending(self):
        "[REQ-11] pay a declined / cancelled / paid request with a fresh key -> 409 request_not_pending"
        a = req("bob", "ada", 10).body["request_id"]
        b = req("bob", "ada", 10).body["request_id"]
        c = req("bob", "ada", 10).body["request_id"]
        post("/requests/%s/decline" % a, "ada")
        post("/requests/%s/cancel" % b, "bob")
        self.assertEqual(pay_req("ada", c).status, 201)
        for rid in (a, b, c):
            self.err(pay_req("ada", rid), 409, "request_not_pending")
        self.assertEqual(bal("ada"), 9990)

    def test_decline(self):
        "[REQ-12] decline: only payer; 200 declined; idempotent; paid/cancelled -> 409; no body or {} accepted"
        a = req("bob", "ada", 10).body["request_id"]
        self.err(post("/requests/%s/decline" % a, "bob"), 403, "forbidden")
        r = post("/requests/%s/decline" % a, "ada")
        self.assertEqual(r.status, 200, r)
        self.request_shape(r.body, status="declined")
        self.assertEqual(r.body["request_id"], a)
        r = post("/requests/%s/decline" % a, "ada", {})
        self.assertEqual((r.status, r.body["status"]), (200, "declined"))
        self.err(post("/requests/%s/cancel" % a, "bob"), 409, "request_not_pending")
        b = req("bob", "ada", 10).body["request_id"]
        pay_req("ada", b)
        self.err(post("/requests/%s/decline" % b, "ada"), 409, "request_not_pending")
        c = req("bob", "ada", 10).body["request_id"]
        post("/requests/%s/cancel" % c, "bob")
        self.err(post("/requests/%s/decline" % c, "ada"), 409, "request_not_pending")
        self.err(post("/requests/nope/decline", "ada"), 404, "not_found")
        self.assertEqual(bal("ada"), 9990)

    def test_cancel(self):
        "[REQ-13] cancel: only requester; 200 cancelled; idempotent; paid/declined -> 409"
        a = req("bob", "ada", 10).body["request_id"]
        self.err(post("/requests/%s/cancel" % a, "ada"), 403, "forbidden")
        r = post("/requests/%s/cancel" % a, "bob")
        self.assertEqual(r.status, 200, r)
        self.request_shape(r.body, status="cancelled")
        r = post("/requests/%s/cancel" % a, "bob", {})
        self.assertEqual((r.status, r.body["status"]), (200, "cancelled"))
        self.err(post("/requests/%s/decline" % a, "ada"), 409, "request_not_pending")
        b = req("bob", "ada", 10).body["request_id"]
        pay_req("ada", b)
        self.err(post("/requests/%s/cancel" % b, "bob"), 409, "request_not_pending")
        c = req("bob", "ada", 10).body["request_id"]
        post("/requests/%s/decline" % c, "ada")
        self.err(post("/requests/%s/cancel" % c, "bob"), 409, "request_not_pending")
        self.err(post("/requests/nope/cancel", "bob"), 404, "not_found")

    def test_third_party_and_operator_cannot_decline_cancel(self):
        "[REQ-14] unrelated users and operators cannot decline or cancel (403/404); the request is unchanged"
        a = req("bob", "ada", 10).body["request_id"]
        for who in ("cy", "op"):
            for act in ("decline", "cancel"):
                r = post("/requests/%s/%s" % (a, act), who)
                self.assertIn(r.status, (403, 404), r)
                self.err4xx(r, {"forbidden", "not_found"})
        q = [x for x in requests_of("bob")["requests"] if x["request_id"] == a][0]
        self.assertEqual(q["status"], "pending")

    def test_no_key_needed_on_decline_cancel(self):
        "[REQ-15] decline and cancel need no idempotency key, and a stray key does not make repeats errors"
        a = req("bob", "ada", 10).body["request_id"]
        self.assertEqual(call("POST", "/requests/%s/decline" % a, token=tok("ada")).status, 200)
        self.assertEqual(call("POST", "/requests/%s/decline" % a, token=tok("ada"), key="same").status, 200)
        self.assertEqual(call("POST", "/requests/%s/decline" % a, token=tok("ada"), key="same").status, 200)

    def test_list_scoping_and_filters(self):
        "[REQ-16] GET /requests: only own; direction and status filters; unknown values 422"
        a = req("bob", "ada", 10).body["request_id"]
        b = req("ada", "bob", 20).body["request_id"]
        c = req("cy", "eve", 30).body["request_id"]
        pay_req("bob", b)
        post("/requests/%s/decline" % a, "ada")
        ids = lambda **q: {x["request_id"] for x in requests_of("ada", **q)["requests"]}
        allv = ids()
        self.assertEqual(allv, {"rq_1", "rq_4", "rq_5", a, b})
        self.assertNotIn(c, allv)
        self.assertEqual(ids(direction="incoming"), {"rq_1", "rq_4", "rq_5", a})
        self.assertEqual(ids(direction="outgoing"), {b})
        self.assertEqual(ids(status="pending"), {"rq_1"})
        self.assertEqual(ids(status="declined"), {"rq_4", a})
        self.assertEqual(ids(status="paid"), {b})
        self.assertEqual(ids(status="cancelled"), {"rq_5"})
        self.assertEqual(ids(direction="incoming", status="declined"), {"rq_4", a})
        self.assertEqual(ids(direction="outgoing", status="declined"), set())
        self.assertEqual(ids(unknown="x"), allv)
        for q in ({"direction": "sideways"}, {"direction": "INCOMING"}, {"status": "done"}, {"status": "PENDING"}, {"direction": ""}):
            r = call("GET", "/requests?" + "&".join("%s=%s" % kv for kv in q.items()), token=tok("ada"))
            self.err(r, 422, "validation_failed")
        self.assertNotIn(c, {x["request_id"] for x in requests_of("op")["requests"]})
        self.assertEqual(requests_of("op")["requests"], [])

    def test_list_paging_and_order(self):
        "[REQ-17] newest first by created_at; limit/offset/has_more"
        ids = []
        for i in range(3):
            ids.append(req("op", "dee", 10 + i).body["request_id"])
            time.sleep(1.1)
        got = [x["request_id"] for x in requests_of("op", direction="outgoing")["requests"]]
        self.assertEqual(got, ids[::-1])
        self.assertEqual(got[0], ids[2])
        r = requests_of("op", direction="outgoing", limit=2)
        self.assertEqual(([x["request_id"] for x in r["requests"]], r["has_more"]), (ids[::-1][:2], True))
        r = requests_of("op", direction="outgoing", limit=2, offset=2)
        self.assertEqual(([x["request_id"] for x in r["requests"]], r["has_more"]), (ids[::-1][2:], False))
        r = requests_of("op", direction="outgoing", limit=3)
        self.assertEqual((len(r["requests"]), r["has_more"]), (3, False))
        r = requests_of("op", direction="outgoing", limit=1, offset=5)
        self.assertEqual((r["requests"], r["has_more"]), ([], False))
        self.assertEqual(len(requests_of("op", limit=200)["requests"]) >= 3, True)
        self.assertIsInstance(requests_of("op")["has_more"], bool)

    def test_list_param_validation(self):
        "[REQ-18] limit 1..200 / offset >= 0, plain decimal digits only"
        t = tok("ada")
        for qs in ("limit=0", "limit=201", "limit=-1", "limit=abc", "limit=", "limit=1e2", "limit=4.0", "limit=+4", "limit=%2B4",
                   "limit=1000000", "offset=-1", "offset=abc", "offset=1e0", "offset=0.0", "offset=+1", "offset=",
                   "limit=%D9%A1%D9%A2"):
            self.err(call("GET", "/requests?" + qs, token=t), 422, "validation_failed")
        for qs in ("limit=1", "limit=200", "offset=0", "offset=999999"):
            self.assertEqual(call("GET", "/requests?" + qs, token=t).status, 200, qs)

    def test_requests_never_in_feed(self):
        "[REQ-19] requests never appear in the activity feed (any state)"
        q = req("bob", "ada", 10).body
        post("/requests/%s/decline" % q["request_id"], "ada")
        for n in ("ada", "bob", "cy"):
            body = activity(n)
            self.assertEqual({x["payment_id"] for x in body["payments"]} & {q["request_id"]}, set())
            for p in body["payments"]:
                self.assertNotIn("status", p)
                self.assertIn("payment_id", p)

    def test_race_pay_vs_cancel(self):
        "[CON-05] concurrent pay and cancel: exactly one wins, money moves iff paid"
        for _ in range(8):
            reset()
            rid = req("bob", "ada", 700).body["request_id"]
            rs = parallel([lambda: pay_req("ada", rid), lambda: post("/requests/%s/cancel" % rid, "bob")])
            pr, cr = rs
            self.assertFalse(pr.status == 201 and cr.status == 200, rs)
            self.assertTrue(pr.status in (201, 409) and cr.status in (200, 409), rs)
            q = [x for x in requests_of("bob")["requests"] if x["request_id"] == rid][0]
            if q["status"] == "paid":
                self.assertEqual(pr.status, 201)
                self.assertEqual((bal("ada"), bal("bob")), (9300, 3200))
            else:
                self.assertEqual((q["status"], cr.status), ("cancelled", 200))
                self.assertEqual((bal("ada"), bal("bob")), (10000, 2500))

    def test_race_pay_vs_decline(self):
        "[CON-06] concurrent pay and decline: exactly one wins, money moves iff paid"
        for _ in range(8):
            reset()
            rid = req("bob", "ada", 700).body["request_id"]
            pr, dr = parallel([lambda: pay_req("ada", rid), lambda: post("/requests/%s/decline" % rid, "ada")])
            self.assertFalse(pr.status == 201 and dr.status == 200, (pr, dr))
            q = [x for x in requests_of("bob")["requests"] if x["request_id"] == rid][0]
            self.assertIn(q["status"], ("paid", "declined"))
            self.assertEqual(bal("ada"), 9300 if q["status"] == "paid" else 10000)

    def test_concurrent_pay_distinct_keys_moves_money_once(self):
        "[CON-04] same request paid concurrently with many different keys: one 201, the rest request_not_pending, money once"
        rid = req("bob", "ada", 500).body["request_id"]
        rs = parallel([lambda: pay_req("ada", rid) for _ in range(25)])
        self.assertEqual(sorted(r.status for r in rs), [201] + [409] * 24, rs)
        self.assertTrue(all(r.code == "request_not_pending" for r in rs if r.status == 409), rs)
        self.assertEqual((bal("ada"), bal("bob")), (9500, 3000))
        self.assertEqual(len([x for x in activity("ada")["payments"] if x["request_id"] == rid]), 1)

    def test_concurrent_pay_two_requests_one_balance(self):
        "[CON-07] two requests that together exceed the payer's balance, paid concurrently: exactly one succeeds"
        a = req("bob", "ada", 7000).body["request_id"]
        b = req("cy", "ada", 7000).body["request_id"]
        rs = parallel([lambda: pay_req("ada", a), lambda: pay_req("ada", b)])
        self.assertEqual(sorted(r.status for r in rs), [201, 409], rs)
        self.assertEqual(next(r for r in rs if r.status == 409).code, "insufficient_funds")
        self.assertEqual(bal("ada"), 3000)
        self.assertEqual(total(), BASE_TOTAL)


class Splits(Base):
    def split(self, who, amount, handles, key=None, **extra):
        body = {"amount": amount, "participant_handles": handles}
        body.update(extra)
        return call("POST", "/splits", body, token=tok(who), key=key or k())

    def test_shape_and_equal_shares(self):
        "[SPL-01] 201 shape; shares cover everybody in order; requests cover everybody but the caller in order"
        r = self.split("ada", 3000, ["ada", "bob", "cy"], note="dinner")
        self.assertEqual(r.status, 201, r)
        b = r.body
        for f in ("split_id", "amount", "currency", "note", "shares", "requests", "created_at"):
            self.assertIn(f, b)
        self.assertLessEqual(len(b["split_id"]), 64)
        self.assertEqual((b["amount"], b["currency"], b["note"]), (3000, "EUR", "dinner"))
        self.assertEqual(b["shares"], [{"handle": "ada", "amount": 1000}, {"handle": "bob", "amount": 1000}, {"handle": "cy", "amount": 1000}])
        self.assertEqual([q["payer_handle"] for q in b["requests"]], ["bob", "cy"])
        for q in b["requests"]:
            self.request_shape(q, requester="ada", amount=1000, status="pending", note="dinner")
        self.ts(b["created_at"])
        self.assertEqual(total(), BASE_TOTAL)
        self.assertEqual(bal("ada"), 10000)

    def test_rounding_table(self):
        "[SPL-02] spec rounding table; larger shares go to the first participants"
        names = ["bob", "cy", "dee", "eve", "op"]
        for amount, n, want in ((1000, 3, [334, 333, 333]), (1, 3, [1, 0, 0]), (10, 3, [4, 3, 3]), (999, 3, [333, 333, 333]),
                                (5, 5, [1, 1, 1, 1, 1])):
            r = self.split("ada", amount, names[:n])
            self.assertEqual(r.status, 201, r)
            self.assertEqual([s["amount"] for s in r.body["shares"]], want, (amount, n))
            self.assertEqual([q["amount"] for q in r.body["requests"]], want)
            self.assertEqual([s["handle"] for s in r.body["shares"]], names[:n])

    def test_rounding_property(self):
        "[SPL-03] for amounts 1..40 and n 1..7: whole units, sum exact, differ <= 1, remainder to the earliest"
        names = ["bob", "cy", "dee", "eve", "op", "op2", "ada"]
        for n in range(1, 8):
            for amount in list(range(1, 41)) + [999999999, 1000000000]:
                r = self.split("ada", amount, names[:n])
                self.assertEqual(r.status, 201, (amount, n, r))
                sh = [s["amount"] for s in r.body["shares"]]
                q, rem = divmod(amount, n)
                self.assertEqual(sh, [q + 1] * rem + [q] * (n - rem), (amount, n))
                self.assertEqual(sum(sh), amount)

    def test_order_changes_who_gets_the_extra_unit(self):
        "[SPL-04] same people, different order -> extra unit goes to a different person"
        a = self.split("ada", 1000, ["bob", "cy", "dee"]).body["shares"]
        b = self.split("ada", 1000, ["dee", "cy", "bob"]).body["shares"]
        self.assertEqual({s["handle"]: s["amount"] for s in a}, {"bob": 334, "cy": 333, "dee": 333})
        self.assertEqual({s["handle"]: s["amount"] for s in b}, {"dee": 334, "cy": 333, "bob": 333})

    def test_caller_position_and_omission(self):
        "[SPL-05] caller may be anywhere in the list or omitted; requests exclude only the caller"
        r = self.split("ada", 10, ["bob", "ada", "cy"]).body
        self.assertEqual([s["amount"] for s in r["shares"]], [4, 3, 3])
        self.assertEqual([(q["payer_handle"], q["amount"]) for q in r["requests"]], [("bob", 4), ("cy", 3)])
        r = self.split("ada", 10, ["bob", "cy", "dee"]).body
        self.assertEqual([(q["payer_handle"], q["amount"]) for q in r["requests"]], [("bob", 4), ("cy", 3), ("dee", 3)])
        self.assertEqual(len(r["shares"]), 3)

    def test_only_caller(self):
        "[SPL-06] caller as the only participant is valid: one share, zero requests"
        r = self.split("ada", 777, ["ada"])
        self.assertEqual(r.status, 201, r)
        self.assertEqual(r.body["shares"], [{"handle": "ada", "amount": 777}])
        self.assertEqual(r.body["requests"], [])

    def test_zero_share_still_produces_request(self):
        "[SPL-07] share of 0 is legal and still creates a request for that participant"
        r = self.split("ada", 1, ["ada", "bob", "cy"]).body
        self.assertEqual([s["amount"] for s in r["shares"]], [1, 0, 0])
        self.assertEqual([q["amount"] for q in r["requests"]], [0, 0])
        self.assertEqual(len([x for x in requests_of("bob")["requests"] if x["request_id"] == r["requests"][0]["request_id"]]), 1)

    def test_no_balance_checks_no_money_moves(self):
        "[SPL-08] nobody's balance is checked or changed (a broke caller can split; broke participants get requests)"
        r = self.split("dee", 10**9, ["bob", "cy", "dee", "eve"])
        self.assertEqual(r.status, 201, r)
        self.assertEqual([bal(n) for n in BASE_NAMES], [10000, 2500, 1000, 0, 5000, 0, 0])

    def test_validation(self):
        "[SPL-09] empty / duplicate participants, amount range, note length, unknown handle (nothing created)"
        for h in ([], ["bob", "bob"], ["ada", "bob", "ada"]):
            self.err(self.split("ada", 100, h), 422, "validation_failed")
        for a in (0, -1, 1000000001):
            self.err(self.split("ada", a, ["bob"]), 422, "validation_failed")
        for form in ("true", '"100"', "10.5"):
            r = call("POST", "/splits", raw='{"amount":%s,"participant_handles":["bob"]}' % form, token=tok("ada"), key=k())
            self.err(r, 422, "validation_failed")
        self.err(self.split("ada", 100, ["bob"], note="x" * 201), 422, "validation_failed")
        self.assertEqual(self.split("ada", 100, ["bob"], note="x" * 200).status, 201)
        before = [len(requests_of(n, limit=200)["requests"]) for n in ("bob", "cy", "ada")]
        self.err(self.split("ada", 100, ["bob", "nobody", "cy"]), 404, "not_found")
        self.assertEqual([len(requests_of(n, limit=200)["requests"]) for n in ("bob", "cy", "ada")], before)
        self.err(call("POST", "/splits", {"amount": 5}, token=tok("ada"), key=k()), 422, "validation_failed")
        self.err(call("POST", "/splits", {"participant_handles": ["bob"]}, token=tok("ada"), key=k()), 422, "validation_failed")
        for bad in ("bob", 5, {"a": "bob"}):
            self.err(call("POST", "/splits", {"amount": 5, "participant_handles": bad}, token=tok("ada"), key=k()), 400, "malformed_request")
        self.err(call("POST", "/splits", {"amount": 5, "participant_handles": [5]}, token=tok("ada"), key=k()), 400, "malformed_request")

    def test_many_participants(self):
        "[SPL-10] a large participant list is validated, not crashed (unknown handle -> 404; never 5xx)"
        r = self.split("ada", 1000, ["h%d" % i for i in range(1000)])
        self.err4xx(r)
        self.assertLess(r.status, 500)
        r = self.split("ada", 1000, ["ada"] + ["bob"] * 5000)
        self.err(r, 422, "validation_failed")

    def test_requests_visible_only_to_parties_and_not_in_feed(self):
        "[SPL-11] split requests are visible to requester and payer only; a split is not a feed item"
        r = self.split("ada", 900, ["ada", "bob", "cy"], note="visibility-split").body
        rids = {q["request_id"] for q in r["requests"]}
        self.assertTrue(rids <= {x["request_id"] for x in requests_of("ada", limit=200)["requests"]})
        self.assertIn(r["requests"][0]["request_id"], {x["request_id"] for x in requests_of("bob")["requests"]})
        self.assertNotIn(r["requests"][0]["request_id"], {x["request_id"] for x in requests_of("cy")["requests"]})
        self.assertEqual({x["request_id"] for x in requests_of("dee")["requests"]} & rids, set())
        self.assertEqual({x["request_id"] for x in requests_of("op")["requests"]} & rids, set())
        before = feed_ids("eve")
        for n in ("ada", "bob", "cy", "eve"):
            self.assertFalse([p for p in activity(n)["payments"] if r["split_id"] in str(p) or "visibility-split" == p["note"]])
        self.assertEqual(feed_ids("eve"), before)

    def test_paid_in_full_conserves_money(self):
        "[SPL-12] after splits are paid in full by everyone, balances still sum to the seeded total and nothing is negative"
        received = 0
        for amount, who in ((1000, ["ada", "bob", "cy"]), (1, ["ada", "bob", "cy"]), (10, ["ada", "eve", "bob", "cy"]), (999, ["ada", "bob", "cy"])):
            r = self.split("ada", amount, who)
            self.assertEqual(r.status, 201, r)
            received += sum(x["amount"] for x in r.body["shares"] if x["handle"] != "ada")
            for q in r.body["requests"]:
                if q["amount"] == 0:
                    continue  # paying a zero-amount request is unspecified (ambiguity A-12)
                rr = pay_req(q["payer_handle"], q["request_id"])
                self.assertEqual(rr.status, 201, (q, rr))
                self.assertEqual(rr.body["amount"], q["amount"])
        bs = [bal(n) for n in BASE_NAMES]
        self.assertEqual(sum(bs), BASE_TOTAL)
        self.assertTrue(all(x >= 0 for x in bs))
        self.assertEqual(bal("ada"), 10000 + received)

    def test_independent_of_previous_splits(self):
        "[SPL-13] each split's shares are independent of earlier splits (no carried remainder)"
        for _ in range(4):
            self.assertEqual([s["amount"] for s in self.split("ada", 1000, ["bob", "cy", "dee"]).body["shares"]], [334, 333, 333])

    def test_unknown_fields_ignored(self):
        "[SPL-14] unknown fields in a split body are ignored; note defaults to ''"
        r = self.split("ada", 10, ["bob"], whatever=1, shares=[1, 2])
        self.assertEqual(r.status, 201)
        self.assertEqual(r.body["note"], "")
        self.assertEqual(r.body["shares"], [{"handle": "bob", "amount": 10}])


class Feed(Base):
    def test_visibility_matrix(self):
        "[FED-01] payment visible iff public, or caller is sender/receiver; identical for both parties"
        pub = pay("ada", "bob", 10, visibility="public").body["payment_id"]
        prv = pay("ada", "bob", 11, visibility="private").body["payment_id"]
        for who, sees_pub, sees_prv in (("ada", 1, 1), ("bob", 1, 1), ("cy", 1, 0), ("dee", 1, 0), ("op", 1, 0)):
            ids = feed_ids(who)
            self.assertEqual(pub in ids, bool(sees_pub), who)
            self.assertEqual(prv in ids, bool(sees_prv), who)

    def test_seeded_payments(self):
        "[FED-02] seeded payments follow the feed contract (p_1 public, p_2 ada->cy private, p_3 bob->cy private)"
        want = {"ada": {"p_1", "p_2"}, "bob": {"p_1", "p_3"}, "cy": {"p_1", "p_2", "p_3"}, "eve": {"p_1"}, "op": {"p_1"}}
        for n, ids in want.items():
            self.assertEqual(set(feed_ids(n)), ids, n)
        p = {x["payment_id"]: x for x in activity("cy")["payments"]}
        self.payment_shape(p["p_2"], "ada", "cy", 300, "secret", "private")
        self.payment_shape(p["p_1"], "ada", "bob", 500, "coffee", "public")

    def test_third_party_never_sees_private_between_others(self):
        "[FED-03] a private payment between two users is invisible to everyone else, including operators and via paging"
        pay("ada", "bob", 5, visibility="private", note="hush")
        for n in ("cy", "dee", "eve", "op", "op2"):
            self.assertFalse([p for p in activity(n, limit=200)["payments"] if p["note"] == "hush"], n)

    def test_receiver_sees_private(self):
        "[FED-04] private is hidden from third parties, not from its receiver"
        p = pay("ada", "dee", 5, visibility="private").body["payment_id"]
        self.assertIn(p, feed_ids("dee"))
        self.assertIn(p, feed_ids("ada"))

    def test_no_follow_graph(self):
        "[FED-05] no follow/mute: a stranger sees exactly the public payments, however they relate to the parties"
        for _ in range(3):
            pay("ada", "bob", 1)
            pay("bob", "ada", 1, visibility="private")
        pub = [p for p in activity("eve", limit=200)["payments"]]
        self.assertTrue(all(p["visibility"] == "public" for p in pub))
        self.assertEqual(len(pub), 1 + 3)  # seeded p_1 + three public

    def test_order_newest_first(self):
        "[FED-06] newest first by created_at"
        ids = []
        for a in (1, 2, 3):
            ids.append(pay("ada", "bob", a).body["payment_id"])
            time.sleep(1.1)
        got = feed_ids("bob")
        self.assertEqual(got[:3], ids[::-1])
        ts = [p["created_at"] for p in activity("bob")["payments"]]
        parsed = [self.ts(t) for t in ts]
        self.assertEqual(parsed, sorted(parsed, reverse=True))

    def test_paging(self):
        "[FED-07] limit/offset/has_more; pages are disjoint and cover the whole feed"
        for i in range(5):
            pay("ada", "bob", 1 + i)
        full = feed_ids("bob")
        self.assertEqual(len(full), 5 + 2)  # + seeded p_1, p_3
        r = activity("bob", limit=3)
        self.assertEqual((len(r["payments"]), r["has_more"]), (3, True))
        r = activity("bob", limit=7)
        self.assertEqual((len(r["payments"]), r["has_more"]), (7, False))
        r = activity("bob", limit=3, offset=6)
        self.assertEqual((len(r["payments"]), r["has_more"]), (1, False))
        r = activity("bob", limit=3, offset=100)
        self.assertEqual((r["payments"], r["has_more"]), ([], False))
        seen = []
        for off in range(0, 7, 2):
            seen += [p["payment_id"] for p in activity("bob", limit=2, offset=off)["payments"]]
        self.assertEqual(seen, full)

    def test_paging_validation(self):
        "[FED-08] limit/offset ranges and formats on /activity"
        t = tok("ada")
        for qs in ("limit=0", "limit=201", "limit=-3", "limit=x", "limit=1e1", "limit=4.0", "limit=+4", "limit=", "offset=-1", "offset=1.0",
                   "offset=1e0", "offset=+0", "offset=x", "offset="):
            self.err(call("GET", "/activity?" + qs, token=t), 422, "validation_failed")
        for qs in ("limit=200", "limit=1&offset=0", "offset=3"):
            self.assertEqual(call("GET", "/activity?" + qs, token=t).status, 200)
        self.assertEqual(call("GET", "/activity?direction=sideways&status=zzz&foo=1", token=t).status, 200)

    def test_empty_feed(self):
        "[FED-09] a user with no visible payments gets an empty list and has_more false"
        reset(base_fixture(payments=[]))
        self.assertEqual(activity("dee"), {"payments": [], "has_more": False})

    def test_feed_payment_shape(self):
        "[FED-10] feed items are full payments (incl. request_id for paid requests, settlement_id null for non-members)"
        rid = req("bob", "ada", 10).body["request_id"]
        pay_req("ada", rid)
        pay("ada", "cy", 5)
        for p in activity("ada")["payments"]:
            self.assertIn("settlement_id", p)
            self.assertIsNone(p["settlement_id"])
            self.is_int(p["amount"])
            self.ts(p["created_at"]) if p["payment_id"] not in ("p_1", "p_2", "p_3") else None
        paid = [p for p in activity("ada")["payments"] if p["request_id"] == rid]
        self.assertEqual(len(paid), 1)

    def test_conservation_note_sender_view(self):
        "[FED-11] sender and receiver see the same payment objects (field for field)"
        pay("eve", "dee", 10, visibility="private", note="same")
        a = [p for p in activity("eve")["payments"] if p["note"] == "same"]
        b = [p for p in activity("dee")["payments"] if p["note"] == "same"]
        self.assertEqual(a, b)
        self.assertEqual(len(a), 1)


if __name__ == "__main__":
    unittest.main()
