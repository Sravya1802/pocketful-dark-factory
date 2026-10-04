"""POST /payments, GET /me, and idempotency across the five write paths."""
import json
import unittest

from lib import *


class Payments(Base):
    def test_happy_path_shape_and_balances(self):
        "[PAY-01] 201 payment shape; debit and credit equal; sum conserved"
        r = pay("ada", "bob", 1500, note="dinner", visibility="public")
        self.assertEqual(r.status, 201, r)
        self.payment_shape(r.body, "ada", "bob", 1500, "dinner", "public")
        self.assertEqual((bal("ada"), bal("bob")), (8500, 4000))
        self.assertEqual(total(), BASE_TOTAL)

    def test_defaults(self):
        "[PAY-02] note defaults to '' and visibility to public"
        r = pay("ada", "bob", 10)
        self.payment_shape(r.body, note="", vis="public")

    def test_note_length_boundary(self):
        "[PAY-03] 200-character note ok, 201 -> 422 (nothing moves)"
        self.assertEqual(pay("ada", "bob", 1, note="n" * 200).status, 201)
        self.err(pay("ada", "bob", 1, note="n" * 201), 422, "validation_failed")
        self.assertEqual(bal("ada"), 9999)

    def test_note_counted_in_characters(self):
        "[PAY-04] 200 emoji (non-BMP code points) is within the 200-character limit; 201 is not"
        self.assertEqual(pay("ada", "bob", 1, note="\U0001F600" * 200).status, 201)
        self.err(pay("ada", "bob", 1, note="\U0001F600" * 201), 422, "validation_failed")

    def test_note_verbatim(self):
        "[PAY-05] note stored and returned verbatim: whitespace, markup, unicode, escapes"
        notes = ["  leading and trailing  ", "<script>alert(1)</script> & \"quotes\" 'x'", "café ☕ \U0001F468‍\U0001F469‍\U0001F467 中文",
                 "line1\nline2\ttab", "á (combining) vs á", "\\u0041 literal backslash", "", " "]
        for n in notes:
            r = pay("ada", "bob", 1, note=n)
            self.assertEqual(r.status, 201, (n, r))
            self.assertEqual(r.body["note"], n)
        got = {p["note"] for p in activity("ada")["payments"]}
        for n in notes:
            self.assertIn(n, got)

    def test_note_wrong_types(self):
        "[PAY-06] non-string note (incl. null) -> 422"
        for v in ("null", "5", "true", "[]", "{}", "1.5"):
            raw = '{"to_handle":"bob","amount":1,"note":%s}' % v
            self.err(call("POST", "/payments", raw=raw, token=tok("ada"), key=k()), 422, "validation_failed")

    def test_visibility_values(self):
        "[PAY-07] only public/private; anything else -> 422"
        self.assertEqual(pay("ada", "bob", 1, visibility="private").body["visibility"], "private")
        for v in ("PUBLIC", "Private", "friends", "", "public ", None, 1, True, ["public"]):
            r = pay("ada", "bob", 1, visibility=v)
            self.err(r, 422, "validation_failed")
        self.assertEqual(bal("ada"), 9999)

    def test_insufficient_funds(self):
        "[PAY-08] amount > balance -> 409 insufficient_funds, no trace anywhere"
        before = (bal("ada"), bal("bob"), feed_ids("ada"), feed_ids("bob"))
        self.err(pay("ada", "bob", 10001), 409, "insufficient_funds")
        self.assertEqual((bal("ada"), bal("bob"), feed_ids("ada"), feed_ids("bob")), before)

    def test_exact_balance_boundary(self):
        "[PAY-09] paying exactly the balance succeeds and leaves 0; one more unit fails"
        self.assertEqual(pay("ada", "bob", 10000).status, 201)
        self.assertEqual(bal("ada"), 0)
        self.err(pay("ada", "bob", 1), 409, "insufficient_funds")
        self.assertEqual(pay("bob", "ada", 12500).status, 201)  # bob: 2500 + 10000 received
        self.assertEqual(bal("bob"), 0)

    def test_zero_balance_user(self):
        "[PAY-10] a zero-balance user cannot pay"
        self.err(pay("dee", "bob", 1), 409, "insufficient_funds")

    def test_amount_bounds(self):
        "[PAY-11] 1 and 1000000000 valid; 0, negative and 1000000001 -> 422"
        reset(base_fixture(users=[user("ada", 5 * 10**9), user("bob", 0)], payments=[], requests=[]))
        self.assertEqual(pay("ada", "bob", 1).status, 201)
        self.assertEqual(pay("ada", "bob", 1000000000).status, 201)
        for a in (0, -1, 1000000001, 2**53, -2**53):
            self.err(pay("ada", "bob", a), 422, "validation_failed")
        self.assertEqual(bal("bob"), 1000000001)
        self.assertEqual(bal("ada"), 5 * 10**9 - 1000000001)

    def test_self_payment(self):
        "[PAY-12] to_handle = own handle -> 422 self_payment"
        self.err(pay("ada", "ada", 5), 422, "self_payment")
        self.assertEqual(bal("ada"), 10000)

    def test_unknown_handle(self):
        "[PAY-13] unknown handle -> 404 not_found"
        for h in ("nobody", "BOB", "bob ", "", "b" * 30):
            r = pay("ada", h, 5)
            self.assertIn(r.status, (404, 422) if h in ("", "b" * 30, "bob ") else (404,), (h, r))
            self.err4xx(r)
        self.assertEqual(bal("ada"), 10000)

    def test_missing_fields(self):
        "[PAY-14] missing to_handle or amount -> 422; wrong-typed to_handle -> 400"
        t = tok("ada")
        self.err(call("POST", "/payments", {"amount": 5}, token=t, key=k()), 422, "validation_failed")
        self.err(call("POST", "/payments", {"to_handle": "bob"}, token=t, key=k()), 422, "validation_failed")
        self.err(call("POST", "/payments", {}, token=t, key=k()), 422, "validation_failed")
        self.err(call("POST", "/payments", {"to_handle": 7, "amount": 5}, token=t, key=k()), 400, "malformed_request")
        self.err(call("POST", "/payments", {"to_handle": ["bob"], "amount": 5}, token=t, key=k()), 400, "malformed_request")

    def test_unparseable_body(self):
        "[PAY-15] unparseable body -> 400 malformed_request, claims no key"
        t = tok("ada")
        key = k()
        for raw in (b"{", b"", b"not json", b'{"to_handle":"bob","amount":5', b"\xff\xfe"):
            self.err(call("POST", "/payments", raw=raw, token=t, key=key), 400, "malformed_request")
        self.assertEqual(call("POST", "/payments", {"to_handle": "bob", "amount": 5}, token=t, key=key).status, 201)

    def test_unknown_fields_ignored(self):
        "[PAY-16] unknown body fields ignored"
        r = pay("ada", "bob", 5, from_user_id="u_eve", id="x", currency="USD", balance=99, request_id="rq_1", settlement_id="s")
        self.assertEqual(r.status, 201, r)
        self.payment_shape(r.body, "ada", "bob", 5, request_id=None)
        self.assertEqual(r.body["currency"], "EUR")

    def test_sender_is_the_token_owner(self):
        "[PAY-17] debit always comes from the authenticated caller"
        pay("bob", "cy", 100, from_handle="ada", from_user_id="u_ada")
        self.assertEqual((bal("ada"), bal("bob"), bal("cy")), (10000, 2400, 1100))

    def test_payment_appears_in_both_feeds_and_is_consistent(self):
        "[PAY-18] a payment is visible to sender and receiver identically"
        p = pay("ada", "bob", 77, note="x", visibility="private").body
        a = {x["payment_id"]: x for x in activity("ada")["payments"]}[p["payment_id"]]
        b = {x["payment_id"]: x for x in activity("bob")["payments"]}[p["payment_id"]]
        self.assertEqual(a, b)
        self.assertEqual(a, {**a, **{f: p[f] for f in p}})
        self.assertEqual(a["visibility"], "private")

    def test_large_balances_exact(self):
        "[MON-01] arithmetic stays exact near 2^53"
        big = 2**53 - 1000
        reset(base_fixture(users=[user("ada", big - 1), user("bob", 1), user("cy", 0)], payments=[], requests=[]))
        for a in (999999999, 1000000000, 7):
            self.assertEqual(pay("ada", "bob", a).status, 201)
        self.assertEqual(bal("ada"), big - 1 - 2000000006)
        self.assertEqual(bal("bob"), 1 + 2000000006)
        self.assertEqual(bal("ada") + bal("bob"), big)

    def test_me_shape(self):
        "[API-01] GET /me shape"
        m = call("GET", "/me", token=tok("bob")).body
        self.assertEqual(m, {"user_id": "u_bob", "display_name": "Bob", "handle": "bob", "balance": 2500,
                             "currency": "EUR", "minor_units": 2})

    def test_conservation_sequence(self):
        "[INV-01] after many mixed payments, balances sum to the seeded total and none is negative"
        import random
        rnd = random.Random(7)
        names = ["ada", "bob", "cy", "dee", "eve"]
        for _ in range(60):
            a, b = rnd.sample(names, 2)
            pay(a, b, rnd.randint(1, 4000))
        bs = [bal(n) for n in BASE_NAMES]
        self.assertEqual(sum(bs), BASE_TOTAL)
        self.assertTrue(all(x >= 0 for x in bs), bs)


class Idempotency(Base):
    def test_missing_or_empty_key(self):
        "[IDM-01] missing/empty Idempotency-Key -> 400 missing_idempotency_key on all five paths; nothing happens"
        t = tok("ada")
        ops = [("/payments", {"to_handle": "bob", "amount": 5}),
               ("/requests", {"payer_handle": "bob", "amount": 5}),
               ("/requests/rq_1/pay", {}),
               ("/splits", {"amount": 9, "participant_handles": ["bob"]})]
        for path, body in ops:
            self.err(call("POST", path, body, token=t), 400, "missing_idempotency_key")
            self.err(call("POST", path, body, token=t, key=""), 400, "missing_idempotency_key")
        so = tok("op")
        sb = {"transfers": [{"from_handle": "ada", "to_handle": "bob", "amount": 5}]}
        self.err(call("POST", "/settlements", sb, token=so), 400, "missing_idempotency_key")
        self.err(call("POST", "/settlements", sb, token=so, key=""), 400, "missing_idempotency_key")
        self.assertEqual(bal("ada"), 10000)
        self.assertEqual(len(requests_of("bob")["requests"]), 1)  # only seeded rq_1: nothing was created

    def test_key_length(self):
        "[IDM-02] key 1..255 chars valid; 256 and more -> 422 validation_failed"
        self.assertEqual(pay("ada", "bob", 1, key="x").status, 201)
        self.assertEqual(pay("ada", "bob", 1, key="y" * 255).status, 201)
        self.err(pay("ada", "bob", 1, key="z" * 256), 422, "validation_failed")
        self.err(pay("ada", "bob", 1, key="z" * 10000), 422, "validation_failed")
        self.assertEqual(bal("ada"), 9998)

    def test_first_use_201_replay_200_identical(self):
        "[IDM-03] first 201; replay 200 with identical body; money moves once"
        key = k()
        a = pay("ada", "bob", 123, key=key, note="n")
        b = pay("ada", "bob", 123, key=key, note="n")
        c = pay("ada", "bob", 123, key=key, note="n")
        self.assertEqual((a.status, b.status, c.status), (201, 200, 200))
        self.assertEqual(a.body, b.body)
        self.assertEqual(a.body, c.body)
        self.assertEqual(bal("ada"), 10000 - 123)
        self.assertEqual(bal("bob"), 2500 + 123)
        self.assertEqual(len([x for x in activity("ada")["payments"] if x["note"] == "n"]), 1)

    def test_same_json_value_different_formatting(self):
        "[IDM-04] same JSON value after parsing (key order, whitespace) is a replay"
        key = k()
        t = tok("ada")
        a = call("POST", "/payments", raw='{"to_handle":"bob","amount":50,"note":"z","visibility":"private"}', token=t, key=key)
        b = call("POST", "/payments", raw='  {\n "visibility" : "private",\n\t"note":"z", "amount":50 , "to_handle":"bob" }\n', token=t, key=key)
        self.assertEqual((a.status, b.status), (201, 200), (a, b))
        self.assertEqual(a.body, b.body)
        self.assertEqual(bal("ada"), 9950)

    def test_different_body_is_reuse(self):
        "[IDM-05] same key, different body -> 409 idempotency_key_reuse; nothing moves"
        key = k()
        self.assertEqual(pay("ada", "bob", 100, key=key).status, 201)
        for kw in ({"amount": 101}, {"to_handle": "cy"}, {"note": "other"}, {"visibility": "private"}):
            body = {"to_handle": "bob", "amount": 100}
            body.update(kw)
            r = call("POST", "/payments", body, token=tok("ada"), key=key)
            self.err(r, 409, "idempotency_key_reuse")
        # extra field makes it a different JSON value too
        r = call("POST", "/payments", {"to_handle": "bob", "amount": 100, "zzz": 1}, token=tok("ada"), key=key)
        self.err(r, 409, "idempotency_key_reuse")
        self.assertEqual(bal("ada"), 9900)

    def test_key_outranks_validation(self):
        "[IDM-06] a claimed key is resolved before field validation: invalid body with the same key -> 409 reuse"
        key = k()
        self.assertEqual(pay("ada", "bob", 100, key=key).status, 201)
        t = tok("ada")
        for body in ({"to_handle": "bob", "amount": "bad"}, {"to_handle": "nobody", "amount": 100},
                     {"to_handle": "ada", "amount": 100}, {"amount": 100}, {"to_handle": "bob", "amount": 10**12},
                     {"to_handle": "bob", "amount": 100, "note": "x" * 500}, {"to_handle": "bob", "amount": 10**9}):
            self.err(call("POST", "/payments", body, token=t, key=key), 409, "idempotency_key_reuse")

    def test_key_scoped_per_user(self):
        "[IDM-07] two users may use the same key string without interaction"
        key = k()
        a = pay("ada", "cy", 10, key=key)
        b = pay("bob", "cy", 20, key=key)
        self.assertEqual((a.status, b.status), (201, 201))
        self.assertNotEqual(a.body["payment_id"], b.body["payment_id"])
        self.assertEqual(pay("ada", "cy", 10, key=key).status, 200)
        self.assertEqual(pay("bob", "cy", 20, key=key).status, 200)
        self.assertEqual((bal("ada"), bal("bob"), bal("cy")), (9990, 2480, 1030))

    def test_same_key_other_user_cannot_replay_or_collide(self):
        "[IDM-08] another user sending a different body under my key is not a reuse error"
        key = k()
        self.assertEqual(pay("ada", "cy", 10, key=key).status, 201)
        r = pay("bob", "cy", 999, key=key)
        self.assertEqual(r.status, 201)

    def test_same_key_different_path_not_a_replay(self):
        "[IDM-09] same key on a different path succeeds normally (all path pairs)"
        key = k()
        t = tok("ada")
        self.assertEqual(call("POST", "/payments", {"to_handle": "bob", "amount": 5}, token=t, key=key).status, 201)
        self.assertEqual(call("POST", "/requests", {"payer_handle": "bob", "amount": 5}, token=t, key=key).status, 201)
        self.assertEqual(call("POST", "/splits", {"amount": 5, "participant_handles": ["bob"]}, token=t, key=key).status, 201)
        self.assertEqual(call("POST", "/requests/rq_1/pay", {}, token=t, key=key).status, 201)
        # each is still individually replayable
        self.assertEqual(call("POST", "/requests", {"payer_handle": "bob", "amount": 5}, token=t, key=key).status, 200)
        self.assertEqual(call("POST", "/payments", {"to_handle": "bob", "amount": 5}, token=t, key=key).status, 200)
        self.assertEqual(bal("ada"), 10000 - 5 - 1200)

    def test_same_key_different_requests_pay_paths(self):
        "[IDM-10] paying two different requests with one key: second is a different path, not a replay"
        a = req("bob", "ada", 100).body["request_id"]
        b = req("cy", "ada", 200).body["request_id"]
        key = k()
        self.assertEqual(call("POST", "/requests/%s/pay" % a, {}, token=tok("ada"), key=key).status, 201)
        self.assertEqual(call("POST", "/requests/%s/pay" % b, {}, token=tok("ada"), key=key).status, 201)
        self.assertEqual(bal("ada"), 9700)

    def test_failed_4xx_key_reusable(self):
        "[IDM-11] a key whose original request failed with 4xx is a first use next time (all failure kinds)"
        key = k()
        self.err(pay("ada", "bob", 20000, key=key), 409, "insufficient_funds")
        self.assertEqual(pay("ada", "bob", 20000 - 15000, key=key).status, 201)
        key = k()
        self.err(pay("ada", "nobody", 5, key=key), 404, "not_found")
        self.assertEqual(pay("ada", "bob", 5, key=key).status, 201)
        key = k()
        self.err(pay("ada", "bob", 0, key=key), 422, "validation_failed")
        self.err(pay("ada", "ada", 5, key=key), 422, "self_payment")
        r = pay("ada", "bob", 6, key=key)
        self.assertEqual(r.status, 201, r)
        key = k()
        self.err(call("POST", "/payments", raw=b"{", token=tok("ada"), key=key), 400, "malformed_request")
        self.assertEqual(pay("ada", "bob", 7, key=key).status, 201)

    def test_funded_later_same_key_same_body(self):
        "[IDM-12] insufficient_funds then funds arrive: identical request with the same key succeeds once"
        key = k()
        self.err(pay("dee", "bob", 100, key=key), 409, "insufficient_funds")
        self.assertEqual(pay("ada", "dee", 100).status, 201)
        a = pay("dee", "bob", 100, key=key)
        b = pay("dee", "bob", 100, key=key)
        self.assertEqual((a.status, b.status), (201, 200))
        self.assertEqual(a.body, b.body)
        self.assertEqual(bal("dee"), 0)

    def test_replay_is_original_even_when_state_changed(self):
        "[IDM-13] replay returns the original response although balances / resource state have since changed"
        key = k()
        a = pay("ada", "bob", 9000, key=key)
        self.assertEqual(a.status, 201)
        self.assertEqual(pay("ada", "bob", 1000, key=k()).status, 201)  # ada now at 0
        b = pay("ada", "bob", 9000, key=key)  # would be insufficient if re-evaluated
        self.assertEqual(b.status, 200)
        self.assertEqual(a.body, b.body)
        self.assertEqual(bal("ada"), 0)

    def test_request_replay_after_cancel_returns_original(self):
        "[IDM-14] POST /requests replay after the request was cancelled returns the original pending body and creates nothing"
        key = k()
        a = req("bob", "ada", 300, key=key)
        rid = a.body["request_id"]
        self.assertEqual(call("POST", "/requests/%s/cancel" % rid, token=tok("bob")).status, 200)
        b = req("bob", "ada", 300, key=key)
        self.assertEqual(b.status, 200)
        self.assertEqual(a.body, b.body)
        self.assertEqual(a.body["status"], "pending")
        self.assertEqual(len([q for q in requests_of("bob")["requests"] if q["amount"] == 300]), 1)

    def test_pay_replay_after_paid_is_200_not_409(self):
        "[IDM-15] replaying a successful pay returns 200 + original payment, never request_not_pending, moves no money"
        rid = req("bob", "ada", 400).body["request_id"]
        key = k()
        a = pay_req("ada", rid, key=key)
        self.assertEqual(a.status, 201)
        for _ in range(3):
            b = pay_req("ada", rid, key=key)
            self.assertEqual(b.status, 200, b)
            self.assertEqual(a.body, b.body)
        self.assertEqual(a.body["request_id"], rid)
        self.assertEqual((bal("ada"), bal("bob")), (9600, 2900))
        # a different key on the now-paid request is request_not_pending
        self.err(pay_req("ada", rid), 409, "request_not_pending")

    def test_pay_empty_body_vs_explicit_public(self):
        "[IDM-16] {} and {'visibility':'public'} are different bodies under one key -> 409 reuse"
        rid = req("bob", "ada", 10).body["request_id"]
        key = k()
        self.assertEqual(pay_req("ada", rid, key=key, body={}).status, 201)
        self.err(pay_req("ada", rid, key=key, body={"visibility": "public"}), 409, "idempotency_key_reuse")
        self.err(pay_req("ada", rid, key=key, body={"visibility": "private"}), 409, "idempotency_key_reuse")
        self.assertEqual(pay_req("ada", rid, key=key, body={}).status, 200)
        rid2 = req("bob", "ada", 10).body["request_id"]
        key2 = k()
        self.assertEqual(pay_req("ada", rid2, key=key2, body={"visibility": "public"}).status, 201)
        self.err(pay_req("ada", rid2, key=key2, body={}), 409, "idempotency_key_reuse")

    def test_key_outranks_current_state_on_pay(self):
        "[IDM-17] claimed key resolved before current-resource checks: same key, different (invalid) body on a paid request -> 409 reuse"
        rid = req("bob", "ada", 10).body["request_id"]
        key = k()
        self.assertEqual(pay_req("ada", rid, key=key, body={}).status, 201)
        self.err(pay_req("ada", rid, key=key, body={"visibility": "bogus"}), 409, "idempotency_key_reuse")

    def test_split_replay(self):
        "[IDM-18] split replay: 200, identical body, no extra requests"
        key = k()
        t = tok("ada")
        body = {"amount": 3000, "participant_handles": ["ada", "bob", "cy"], "note": "d"}
        a = call("POST", "/splits", body, token=t, key=key)
        b = call("POST", "/splits", body, token=t, key=key)
        self.assertEqual((a.status, b.status), (201, 200))
        self.assertEqual(a.body, b.body)
        self.assertEqual(len([q for q in requests_of("bob")["requests"] if q["note"] == "d"]), 1)
        self.err(call("POST", "/splits", {**body, "amount": 3001}, token=t, key=key), 409, "idempotency_key_reuse")
        self.err(call("POST", "/splits", {**body, "participant_handles": ["bob", "ada", "cy"]}, token=t, key=key), 409,
                 "idempotency_key_reuse")

    def test_settlement_replay(self):
        "[IDM-19] settlement replay: 200, identical complete response, money once; different body 409; key per user"
        key = k()
        tr = [{"from_handle": "ada", "to_handle": "bob", "amount": 100}, {"from_handle": "bob", "to_handle": "cy", "amount": 50}]
        a = settle("op", tr, key=key)
        b = settle("op", tr, key=key)
        self.assertEqual((a.status, b.status), (201, 200), (a, b))
        self.assertEqual(a.body, b.body)
        self.assertEqual((bal("ada"), bal("bob"), bal("cy")), (9900, 2550, 1050))
        self.err(settle("op", tr[:1], key=key), 409, "idempotency_key_reuse")
        c = settle("op2", tr, key=key)  # another operator, same key string: first use
        self.assertEqual(c.status, 201)
        self.assertEqual((bal("ada"), bal("bob"), bal("cy")), (9800, 2600, 1100))

    def test_concurrent_identical_fresh_key(self):
        "[IDM-20] N concurrent identical payments, fresh key: exactly one 201, rest 200 same body, money once"
        for n in (2, 10, 30):
            reset()
            key = k()
            t = tok("ada")
            rs = parallel([lambda: call("POST", "/payments", {"to_handle": "bob", "amount": 100}, token=t, key=key) for _ in range(n)])
            self.assertEqual(sorted(r.status for r in rs), [200] * (n - 1) + [201], rs)
            self.assertEqual(len({json.dumps(r.body, sort_keys=True) for r in rs}), 1)
            self.assertEqual((bal("ada"), bal("bob")), (9900, 2600))
            self.assertEqual(len(activity("ada")["payments"]), 2 + 1)  # seeded p_1, p_2 + one new

    def test_concurrent_identical_request_create(self):
        "[IDM-21] concurrent identical POST /requests with one key creates exactly one request"
        key = k()
        t = tok("bob")
        rs = parallel([lambda: call("POST", "/requests", {"payer_handle": "cy", "amount": 31}, token=t, key=key) for _ in range(15)])
        self.assertEqual(sorted(r.status for r in rs), [200] * 14 + [201], rs)
        self.assertEqual(len({r.body["request_id"] for r in rs}), 1)
        self.assertEqual(len([q for q in requests_of("cy")["requests"] if q["amount"] == 31]), 1)

    def test_concurrent_identical_split(self):
        "[IDM-22] concurrent identical split with one key creates its requests once"
        key = k()
        t = tok("ada")
        body = {"amount": 301, "participant_handles": ["bob", "cy", "dee"], "note": "once"}
        rs = parallel([lambda: call("POST", "/splits", body, token=t, key=key) for _ in range(12)])
        self.assertEqual(sorted(r.status for r in rs), [200] * 11 + [201], rs)
        self.assertEqual(len({r.body["split_id"] for r in rs}), 1)
        for n in ("bob", "cy", "dee"):
            self.assertEqual(len([q for q in requests_of(n)["requests"] if q["note"] == "once"]), 1)

    def test_concurrent_identical_settlement(self):
        "[IDM-23] concurrent identical settlement with one key commits once"
        key = k()
        t = tok("op")
        body = {"transfers": [{"from_handle": "ada", "to_handle": "bob", "amount": 111}]}
        rs = parallel([lambda: call("POST", "/settlements", body, token=t, key=key) for _ in range(12)])
        self.assertEqual(sorted(r.status for r in rs), [200] * 11 + [201], rs)
        self.assertEqual(len({r.body["settlement_id"] for r in rs}), 1)
        self.assertEqual(bal("ada"), 10000 - 111)

    def test_concurrent_identical_pay(self):
        "[IDM-24] concurrent identical pay (same key) -> one 201, rest 200, money once, request paid once"
        rid = req("bob", "ada", 250).body["request_id"]
        key = k()
        t = tok("ada")
        rs = parallel([lambda: call("POST", "/requests/%s/pay" % rid, {}, token=t, key=key) for _ in range(15)])
        self.assertEqual(sorted(r.status for r in rs), [200] * 14 + [201], rs)
        self.assertEqual(len({r.body["payment_id"] for r in rs}), 1)
        self.assertEqual((bal("ada"), bal("bob")), (9750, 2750))

    def test_key_is_not_global_across_wrong_owner_errors(self):
        "[IDM-25] 403/404 pay failures do not claim the key"
        rid = req("bob", "ada", 10).body["request_id"]
        key = k()
        self.err(call("POST", "/requests/%s/pay" % rid, {}, token=tok("bob"), key=key), 403, "forbidden")
        self.err(call("POST", "/requests/nope/pay", {}, token=tok("ada"), key=key), 404, "not_found")
        self.assertEqual(call("POST", "/requests/%s/pay" % rid, {}, token=tok("ada"), key=key).status, 201)


if __name__ == "__main__":
    unittest.main()
