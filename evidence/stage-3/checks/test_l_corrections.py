"""Stage 3: payment corrections, revisions, linked payments."""
import json
import threading
import time
import unittest

from lib import *


def T(f, t, a, **kw):
    d = {"from_handle": f, "to_handle": t, "amount": a}
    d.update(kw)
    return d


def eff(**kw):
    return iso6(ago(**kw))


class CorrectBase(Base):
    def mk(self, frm="ada", to="bob", amount=1000, **kw):
        r = pay(frm, to, amount, **kw)
        self.assertEqual(r.status, 201, r)
        return r.body["payment_id"], r.body


class Corrections(CorrectBase):
    def test_decrease_moves_money_back(self):
        "[CR-01] decreasing the amount: 201 with payment_id, revision 2, amount, effective_at, server recorded_at, reason; the difference moves receiver -> sender"
        pid, orig = self.mk(amount=1000)
        e = ago(seconds=5)
        r = correct("ada", pid, 1, 400, e, "corrected amount")
        self.assertEqual(r.status, 201, r)
        b = r.body
        self.assertEqual((b["payment_id"], b["revision"], b["amount"], b["reason"]), (pid, 2, 400, "corrected amount"))
        self.assertEqual(dt(b["effective_at"]), e)
        self.assertRegex(b["recorded_at"], TS_STRICT)
        self.assertGreater(dt(b["recorded_at"]), dt(orig["created_at"]))
        self.assertLess(abs((dt(b["recorded_at"]) - now_utc()).total_seconds()), 30)
        self.assertEqual((bal("ada"), bal("bob")), (9600, 2900))
        self.me_total_ok()

    def me_total_ok(self):
        names = ["ada", "bob", "cy", "dee", "eve", "op", "op2"]
        self.assertEqual(sum(bal(n) for n in names), BASE_TOTAL)

    def test_increase_debits_the_sender(self):
        "[CR-02] increasing the amount debits the original sender and credits the original receiver"
        pid, _ = self.mk(amount=100)
        r = correct("ada", pid, 1, 700, ago(seconds=3))
        self.assertEqual(r.status, 201, r)
        self.assertEqual((bal("ada"), bal("bob")), (9300, 3200))
        self.me_total_ok()

    def test_zero_reverses_the_payment(self):
        "[CR-03] amount 0 reverses the entire payment (and a later correction can raise it again)"
        pid, _ = self.mk(amount=300)
        r = correct("ada", pid, 1, 0, ago(seconds=3), "reverse")
        self.assertEqual((r.status, r.body["amount"], r.body["revision"]), (201, 0, 2))
        self.assertEqual((bal("ada"), bal("bob")), (10000, 2500))
        r = correct("ada", pid, 2, 250, ago(seconds=2), "again")
        self.assertEqual((r.status, r.body["revision"]), (201, 3))
        self.assertEqual((bal("ada"), bal("bob")), (9750, 2750))
        self.me_total_ok()

    def test_only_the_two_wallets_move(self):
        "[CR-04] third wallets are untouched; parties and visibility are unchanged by a correction"
        pid, orig = self.mk("ada", "bob", 400, visibility="private", note="hush")
        before = {n: bal(n) for n in ("cy", "dee", "eve", "op")}
        correct("ada", pid, 1, 50, ago(seconds=2))
        self.assertEqual({n: bal(n) for n in ("cy", "dee", "eve", "op")}, before)
        e = [x for x in all_statement("ada")[1] if x["payment"]["payment_id"] == pid][0]
        p = e["payment"]
        self.assertEqual((p["from_handle"], p["to_handle"], p["visibility"], p["note"], p["amount"]), ("ada", "bob", "private", "hush", 50))
        self.assertEqual(e["delta"], -50)
        self.assertNotIn(pid, [x["payment"]["payment_id"] for x in all_statement("cy")[1]])
        self.assertIn(pid, feed_ids("bob"))
        self.assertNotIn(pid, feed_ids("cy"))

    def test_validation_matrix(self):
        "[CR-05] all four fields required; revision positive integer; amount integer 0..1e9; reason 1..200 chars; effective_at RFC 3339 not later than now -> 422"
        pid, _ = self.mk(amount=500)
        good = {"expected_revision": 1, "amount": 100, "effective_at": eff(seconds=5), "reason": "r"}
        t = tok("ada")

        def post(body, raw=None):
            return call("POST", "/payments/%s/corrections" % pid, body if raw is None else None, raw=raw, token=t, key=k())

        for f in good:
            b = dict(good)
            del b[f]
            self.err(post(b), 422, "validation_failed")
        self.err(post({}), 422, "validation_failed")
        for v in ("0", "-1", "1.5", "0.0"):
            self.err(post(None, raw=json.dumps({**good, "expected_revision": 0}).replace('"expected_revision": 0', '"expected_revision": ' + v)), 422, "validation_failed")
        for v in ("-1", "1000000001", "1.5", '"5"', "true", "1e10"):
            raw = json.dumps(good).replace('"amount": 100', '"amount": ' + v)
            self.err(post(None, raw=raw), 422, "validation_failed")
        for v in ("null", "[]", "{}"):
            raw = json.dumps(good).replace('"amount": 100', '"amount": ' + v)
            self.err4xx(post(None, raw=raw), {"validation_failed", "malformed_request"})
        for v in ('"1"', "true", "null", "[]"):
            raw = json.dumps(good).replace('"expected_revision": 1', '"expected_revision": ' + v)
            self.err4xx(post(None, raw=raw), {"validation_failed", "malformed_request"})
        for reason in ("", "x" * 201, "y" * 1000):
            self.err(post({**good, "reason": reason}), 422, "validation_failed")
        for v in ("5", "null", "true", "[]", "{}"):
            raw = json.dumps(good).replace('"reason": "r"', '"reason": ' + v)
            self.err4xx(post(None, raw=raw), {"validation_failed", "malformed_request"})
        for bad in (iso6(now_utc() + timedelta(hours=1)), iso6(now_utc() + timedelta(days=3)), "2026-09-20T12:00:00", "2026-09-20", "", "garbage",
                    "2026-13-01T00:00:00Z", "2026-09-20T12:00Z"):
            self.err(post({**good, "effective_at": bad}), 422, "validation_failed")
        for v in ("5", "null", "true"):
            raw = json.dumps(good).replace('"effective_at": "%s"' % good["effective_at"], '"effective_at": ' + v)
            self.err4xx(post(None, raw=raw), {"validation_failed", "malformed_request"})
        r = call("POST", "/payments/%s/corrections" % pid, raw=b"{not json", token=t, key=k())
        self.err(r, 400, "malformed_request")
        self.assertEqual((bal("ada"), len(revisions("ada", pid).body["revisions"])), (9500, 1))
        # boundaries that are valid
        ok_reason = "z" * 200
        self.assertEqual(post({**good, "reason": "q"}).status, 201)
        self.assertEqual(post({**good, "expected_revision": 2, "reason": ok_reason, "amount": 0}).status, 201)

    def test_amount_upper_bound(self):
        "[CR-06] 1000000000 is a valid corrected amount; 1000000001 is not"
        reset(base_fixture(users=[user("ada", 5 * 10**9), user("bob", 0), user("cy", 0)], payments=[], requests=[]))
        pid, _ = self.mk(amount=5)
        r = correct("ada", pid, 1, 10**9, ago(seconds=2))
        self.assertEqual(r.status, 201, r)
        self.assertEqual(bal("bob"), 10**9)
        self.err(correct("ada", pid, 2, 10**9 + 1, ago(seconds=1)), 422, "validation_failed")

    def test_numeric_forms(self):
        "[CR-07] integral JSON numbers 400.0 and 4e2 are valid amounts; effective_at in several offsets"
        pid, _ = self.mk(amount=900)
        e = ago(seconds=4)
        for i, form in enumerate(("400.0", "4e2")):
            raw = '{"expected_revision": %d, "amount": %s, "effective_at": "%s", "reason": "n"}' % (i + 1, form, iso6(e))
            r = call("POST", "/payments/%s/corrections" % pid, raw=raw, token=tok("ada"), key=k())
            self.assertEqual(r.status, 201, r)
            self.assertEqual(r.body["amount"], 400)
            self.is_int(r.body["amount"])
        for i, ef in enumerate((e.astimezone(timezone(timedelta(hours=5, minutes=30))).isoformat(timespec="seconds"),
                                e.strftime("%Y-%m-%dT%H:%M:%SZ"), e.astimezone(timezone(timedelta(hours=-7))).strftime("%Y-%m-%dT%H:%M:%S.123456-07:00"))):
            r = correct("ada", pid, 3 + i, 400 + i, ef)
            self.assertEqual(r.status, 201, (ef, r))
            self.assertEqual(dt(r.body["effective_at"]), dt(ef))

    def test_permissions_and_unknown(self):
        "[CR-08] no token 401; any authenticated non-sender (receiver, stranger, operator) 403 even for a public payment; unknown payment 404"
        pid, _ = self.mk(visibility="public")
        body = {"expected_revision": 1, "amount": 1, "effective_at": eff(seconds=3), "reason": "x"}
        self.err(call("POST", "/payments/%s/corrections" % pid, body, key=k()), 401, "unauthenticated")
        self.err(call("POST", "/payments/%s/corrections" % pid, body, key=k(), token="bogus"), 401, "unauthenticated")
        for who in ("bob", "cy", "eve", "op"):
            self.err(correct(who, pid, 1, 1, ago(seconds=3)), 403, "forbidden")
        for bad in ("p_nope", "x" * 200, "%00", "..%2F..%2Fme"):
            self.err(call("POST", "/payments/%s/corrections" % bad, body, token=tok("ada"), key=k()), 404, "not_found")
        self.assertEqual(len(revisions("ada", pid).body["revisions"]), 1)
        self.assertEqual(bal("ada"), 9000)

    def test_stale_revision(self):
        "[CR-09] a stale or wrong expected revision -> 409 stale_revision; nothing changes; the key is not claimed"
        pid, _ = self.mk(amount=500)
        key = k()
        self.err(correct("ada", pid, 2, 100, ago(seconds=2), key=key), 409, "stale_revision")
        self.err(correct("ada", pid, 99, 100, ago(seconds=2), key=key), 409, "stale_revision")
        self.assertEqual(len(revisions("ada", pid).body["revisions"]), 1)
        self.assertEqual(bal("ada"), 9500)
        r = correct("ada", pid, 1, 100, ago(seconds=2), key=key)  # the key was never claimed
        self.assertEqual(r.status, 201)
        self.err(correct("ada", pid, 1, 50, ago(seconds=2)), 409, "stale_revision")
        self.assertEqual(correct("ada", pid, 2, 50, ago(seconds=2)).status, 201)
        self.assertEqual(bal("ada"), 9950)

    def test_idempotent_replay_returns_the_original_revision(self):
        "[CR-10] replay -> 200 with the ORIGINAL revision body even after newer revisions; no money moves; JSON-equal bodies replay"
        pid, _ = self.mk(amount=500)
        key = k()
        e = iso6(ago(seconds=5))
        body = {"expected_revision": 1, "amount": 300, "effective_at": e, "reason": "first"}
        a = call("POST", "/payments/%s/corrections" % pid, body, token=tok("ada"), key=key)
        self.assertEqual(a.status, 201)
        self.assertEqual(correct("ada", pid, 2, 100, ago(seconds=3)).status, 201)
        self.assertEqual(correct("ada", pid, 3, 200, ago(seconds=2)).status, 201)
        after = (bal("ada"), bal("bob"))
        for raw in (json.dumps(body), "  " + json.dumps(body, indent=1) + "\n", json.dumps(dict(reversed(list(body.items()))))):
            b = call("POST", "/payments/%s/corrections" % pid, raw=raw, token=tok("ada"), key=key)
            self.assertEqual(b.status, 200, b)
            self.assertEqual(b.body, a.body)
            self.assertEqual(b.body["revision"], 2)
        self.assertEqual((bal("ada"), bal("bob")), after)
        self.assertEqual(len(revisions("ada", pid).body["revisions"]), 4)

    def test_key_reuse_and_precedence(self):
        "[CR-11] same key + different body -> 409 idempotency_key_reuse (also for invalid or stale bodies: the claimed key is resolved first)"
        pid, _ = self.mk(amount=500)
        key = k()
        e = iso6(ago(seconds=5))
        base = {"expected_revision": 1, "amount": 300, "effective_at": e, "reason": "first"}
        self.assertEqual(call("POST", "/payments/%s/corrections" % pid, base, token=tok("ada"), key=key).status, 201)
        for diff in ({"amount": 301}, {"reason": "other"}, {"effective_at": iso6(ago(seconds=6))}, {"expected_revision": 2}, {"amount": "bad"},
                     {"amount": -1}, {"reason": ""}, {"effective_at": "nope"}, {"zzz": 1}):
            self.err(call("POST", "/payments/%s/corrections" % pid, {**base, **diff}, token=tok("ada"), key=key), 409, "idempotency_key_reuse")
        self.assertEqual(len(revisions("ada", pid).body["revisions"]), 2)

    def test_key_requirements_and_scope(self):
        "[CR-12] missing/empty key 400; 256 chars 422; per user; the same key on a different payment's path is a first use"
        p1, _ = self.mk(amount=500)
        p2, _ = self.mk("bob", "cy", 300)
        body = {"expected_revision": 1, "amount": 100, "effective_at": eff(seconds=3), "reason": "r"}
        t = tok("ada")
        self.err(call("POST", "/payments/%s/corrections" % p1, body, token=t), 400, "missing_idempotency_key")
        self.err(call("POST", "/payments/%s/corrections" % p1, body, token=t, key=""), 400, "missing_idempotency_key")
        self.err(call("POST", "/payments/%s/corrections" % p1, body, token=t, key="z" * 256), 422, "validation_failed")
        self.assertEqual(call("POST", "/payments/%s/corrections" % p1, body, token=t, key="z" * 255).status, 201)
        key = k()
        self.assertEqual(call("POST", "/payments/%s/corrections" % p2, body, token=tok("bob"), key=key).status, 201)
        # same key by another user on another payment: independent
        p3, _ = self.mk("ada", "cy", 300)
        self.assertEqual(call("POST", "/payments/%s/corrections" % p3, body, token=t, key=key).status, 201)
        # and as a plain payment key
        self.assertEqual(call("POST", "/payments", {"to_handle": "bob", "amount": 1}, token=t, key=key).status, 201)

    def test_server_assigns_recorded_at_and_ignores_unknown_fields(self):
        "[CR-13] recorded_at in the body is ignored (server-assigned); unknown fields ignored"
        pid, _ = self.mk(amount=500)
        r = correct("ada", pid, 1, 100, ago(seconds=3), recorded_at="1999-01-01T00:00:00+00:00", payment_id="p_other", revision=77, junk=[1])
        self.assertEqual(r.status, 201, r)
        self.assertEqual((r.body["revision"], r.body["payment_id"]), (2, pid))
        self.assertLess(abs((dt(r.body["recorded_at"]) - now_utc()).total_seconds()), 30)

    def test_recorded_at_strictly_increases(self):
        "[CR-14] recorded times of one payment strictly increase across many rapid corrections (also over revision 1)"
        pid, orig = self.mk(amount=900)
        rec = [dt(orig["created_at"])]
        for i in range(8):
            r = correct("ada", pid, 1 + i, 800 - i, ago(seconds=3))
            self.assertEqual(r.status, 201, r)
            self.assertEqual(r.body["revision"], 2 + i)
            rec.append(dt(r.body["recorded_at"]))
        self.assertTrue(all(b > a for a, b in zip(rec, rec[1:])), rec)
        revs = revisions("ada", pid).body["revisions"]
        self.assertEqual([x["revision"] for x in revs], list(range(1, 10)))
        got = [dt(x["recorded_at"]) for x in revs]
        self.assertEqual(got, rec)

    def test_insufficient_funds_current_debit(self):
        "[CR-15] an unaffordable current debit -> 409 insufficient_funds; balances, revisions, statements and the key are untouched"
        pid, _ = self.mk("ada", "bob", 1000)
        pay("bob", "cy", 2600)  # bob had 3500, keeps 900
        pre = (bal("ada"), bal("bob"), bal("cy"), all_statement("ada")[0]["closing_balance"], all_statement("bob")[0]["closing_balance"])
        key = k()
        r = correct("ada", pid, 1, 0, ago(seconds=2), key=key)  # bob must give back 1000 but holds 900
        self.err(r, 409, "insufficient_funds")
        self.assertEqual((bal("ada"), bal("bob"), bal("cy"), all_statement("ada")[0]["closing_balance"], all_statement("bob")[0]["closing_balance"]), pre)
        self.assertEqual(len(revisions("ada", pid).body["revisions"]), 1)
        self.assertEqual(correct("ada", pid, 1, 600, ago(seconds=2), key=key).status, 201)  # failed 4xx: the key is a first use again (bob returns 400 <= 900)
        # an affordable partial decrease at the exact limit
        pid2, _ = self.mk("ada", "dee", 100)
        self.assertEqual(correct("ada", pid2, 1, 0, ago(seconds=2)).status, 201)

    def test_insufficient_funds_increase(self):
        "[CR-16] increasing beyond the sender's available funds -> insufficient_funds (held funds do not count)"
        pid, _ = self.mk("ada", "bob", 100)
        authorize("ada", "cy", 9000)  # ada: total 9900, available 900
        self.err(correct("ada", pid, 1, 1001, ago(seconds=2)), 409, "insufficient_funds")  # needs +901
        self.assertEqual(correct("ada", pid, 1, 1000, ago(seconds=2)).status, 201)         # needs +900 = available
        self.assertEqual(me("ada")["available"], 0)

    def test_receiver_held_funds_do_not_pay_back(self):
        "[CR-17] a decrease debits the receiver's AVAILABLE funds: money the receiver holds in an authorization cannot be returned"
        pid, _ = self.mk("ada", "dee", 500)           # dee: 500
        authorize("dee", "cy", 400)                  # dee available 100
        self.err(correct("ada", pid, 1, 0, ago(seconds=2)), 409, "insufficient_funds")
        self.assertEqual(correct("ada", pid, 1, 400, ago(seconds=2)).status, 201)  # returns 100 = available
        self.assertEqual(me("dee")["available"], 0)

    def test_historical_overdraft_decrease(self):
        "[CR-20] affordable now but the receiver would have been negative at a later boundary -> 409 historical_overdraft, nothing changes"
        specs = [("p_001", "ada", "bob", 500, 10), ("p_002", "bob", "cy", 500, 8), ("p_003", "eve", "bob", 1000, 2)]
        fx, led, n0 = hist(specs, openings={"bob": 0})
        reset(fx)
        pre = {n: bal(n) for n in ("ada", "bob", "cy", "eve")}
        key = k()
        r = correct("ada", "p_001", 1, 100, n0 - timedelta(hours=10), key=key)
        self.err(r, 409, "historical_overdraft")
        self.assertEqual({n: bal(n) for n in ("ada", "bob", "cy", "eve")}, pre)
        self.assertEqual(len(revisions("ada", "p_001").body["revisions"]), 1)
        self.assertEqual(statement("bob", limit=200).body["closing_balance"], pre["bob"])
        # the failure left the key unclaimed: a different valid body under the same key is a first use
        r = correct("ada", "p_001", 1, 500, n0 - timedelta(hours=10), key=key)
        self.assertIn(r.status, (201,), r)

    def test_historical_overdraft_increase(self):
        "[CR-21] increasing a payment so the SENDER would have been negative back then (though rich now) -> historical_overdraft"
        specs = [("p_001", "ada", "bob", 100, 10), ("p_002", "eve", "ada", 5000, 2)]
        fx, led, n0 = hist(specs, openings={"ada": 1000})
        reset(fx)
        self.err(correct("ada", "p_001", 1, 3000, n0 - timedelta(hours=10)), 409, "historical_overdraft")
        self.assertEqual(bal("ada"), 5900)
        # effective after the inflow it is fine
        self.assertEqual(correct("ada", "p_001", 1, 3000, n0 - timedelta(hours=1)).status, 201)
        self.assertEqual(bal("ada"), 3000)

    def test_historical_overdraft_moving_effective_time(self):
        "[CR-22] moving a payment's effective time earlier than the funds that paid for it -> historical_overdraft even with an unchanged amount"
        specs = [("p_001", "ada", "bob", 500, 10), ("p_002", "bob", "cy", 500, 8)]
        fx, led, n0 = hist(specs, openings={"bob": 0})
        reset(fx)
        self.err(correct("bob", "p_002", 1, 500, n0 - timedelta(hours=12)), 409, "historical_overdraft")
        r = correct("bob", "p_002", 1, 500, n0 - timedelta(hours=9))
        self.assertEqual(r.status, 201, r)
        self.assertEqual(bal("bob"), 0)
        self.assertEqual(me_at("bob", iso6(n0 - timedelta(hours=9, minutes=30))).body["balance"], 500)
        self.assertEqual(me_at("bob", iso6(n0 - timedelta(hours=8, minutes=30))).body["balance"], 0)

    def test_insufficient_funds_takes_precedence(self):
        "[CR-23] a currently unaffordable debit is insufficient_funds even when the correction would also overdraw history"
        specs = [("p_001", "ada", "bob", 500, 10), ("p_002", "bob", "cy", 500, 8)]
        fx, led, n0 = hist(specs, openings={"bob": 0})
        reset(fx)
        self.err(correct("ada", "p_001", 1, 0, n0 - timedelta(hours=10)), 409, "insufficient_funds")
        self.assertEqual(bal("bob"), 0)

    def test_ties_combine_at_a_boundary(self):
        "[CR-24] movements at one instant combine: a payee that is paid and pays at the same instant is not overdrawn; unrelated corrections still succeed"
        n0 = now_utc()
        specs = [("p_0a", "bob", "cy", 500, 5), ("p_0b", "ada", "bob", 500, 5), ("p_0c", "ada", "eve", 50, 3)]
        fx, led, _ = hist(specs, openings={"bob": 0}, now0=n0)
        reset(fx)
        r = correct("ada", "p_0c", 1, 10, n0 - timedelta(hours=3))
        self.assertEqual(r.status, 201, r)
        self.assertEqual(bal("bob"), 0)
        # touching the outflow of that instant alone WOULD overdraw bob
        self.err(correct("bob", "p_0a", 1, 600, n0 - timedelta(hours=5)), 409, "insufficient_funds")
        # and shrinking the inflow does too (historical): bob has 0 now so the receiver cannot return anything -> insufficient first
        self.err(correct("ada", "p_0b", 1, 400, n0 - timedelta(hours=5)), 409, "insufficient_funds")

    def test_historical_overdraft_on_available_with_holds(self):
        "[CR-25] total non-negative but AVAILABLE negative at a past hold boundary -> historical_overdraft"
        specs = [("p_001", "ada", "bob", 100, 10)]
        fx, led, n0 = hist(specs, openings={"ada": 1000})
        reset(fx)
        authorize("ada", "cy", 800)          # ada: total 900, held 800
        time.sleep(1.2)
        pay("eve", "ada", 500)               # inflow AFTER the hold: ada total 1400
        self.err(correct("ada", "p_001", 1, 700, n0 - timedelta(hours=10)), 409, "historical_overdraft")  # at hold creation: 300 total < 800 held
        self.assertEqual(me("ada")["available"], 600)
        self.assertEqual(len(revisions("ada", "p_001").body["revisions"]), 1)

    def test_feed_keeps_the_original_receipt(self):
        "[CR-30] GET /activity still shows the original payment (original amount and created_at); corrections are not feed items; the original idempotent response is unchanged"
        key = k()
        orig = pay("ada", "bob", 1000, key=key, note="n")
        pid = orig.body["payment_id"]
        feed_before = activity("ada", limit=200)
        bob_before = activity("bob", limit=200)
        correct("ada", pid, 1, 100, ago(seconds=2))
        correct("ada", pid, 2, 0, ago(seconds=1))
        feed_after = activity("ada", limit=200)
        self.assertEqual(feed_after, feed_before)
        item = [p for p in feed_after["payments"] if p["payment_id"] == pid][0]
        self.assertEqual((item["amount"], item["created_at"]), (1000, orig.body["created_at"]))
        replay = pay("ada", "bob", 1000, key=key, note="n")
        self.assertEqual((replay.status, replay.body), (200, orig.body))
        self.assertEqual(replay.body["amount"], 1000)
        rev = revisions("ada", pid).body["revisions"]
        self.assertEqual(len(rev), 3)
        self.assertEqual(activity("bob", limit=200), bob_before)

    def test_activity_for_a_correction_of_a_request_payment(self):
        "[CR-31] a payment that settled a request is an ordinary payment: correctable (or, if linked, refused with linked_payment_immutable); the request stays paid"
        rid = req("bob", "ada", 200).body["request_id"]
        p = pay_req("ada", rid).body
        r = correct("ada", p["payment_id"], 1, 150, ago(seconds=2))
        if r.status == 422:
            self.assertEqual(r.code, "linked_payment_immutable")
        else:
            self.assertEqual(r.status, 201, r)
            self.assertEqual(bal("bob"), 2500 + 150)
        q = [x for x in requests_of("bob", limit=200)["requests"] if x["request_id"] == rid][0]
        self.assertEqual(q["status"], "paid")

    def test_noop_correction(self):
        "[CR-32] correcting to the same amount and instant moves no money (accepted as a new revision or refused with 422); never 5xx"
        pid, orig = self.mk(amount=300)
        r = correct("ada", pid, 1, 300, dt(orig["created_at"]))
        self.assertLess(r.status, 500)
        self.assertIn(r.status, (201, 422))
        self.assertEqual((bal("ada"), bal("bob")), (9700, 2800))


class Revisions(CorrectBase):
    def test_revision_list(self):
        "[RV-01] revisions are listed in revision order including revision 1 (reason ''); revision 1 effective = recorded = created_at"
        pid, orig = self.mk(amount=500)
        revs = revisions("ada", pid)
        self.assertEqual(revs.status, 200)
        self.assertEqual(list(revs.body), ["revisions"])
        r1 = revs.body["revisions"][0]
        self.assertEqual((r1["revision"], r1["amount"], r1["reason"]), (1, 500, ""))
        self.assertEqual((dt(r1["effective_at"]), dt(r1["recorded_at"])), (dt(orig["created_at"]), dt(orig["created_at"])))
        e = ago(seconds=3)
        c1 = correct("ada", pid, 1, 300, e, "why").body
        c2 = correct("ada", pid, 2, 0, ago(seconds=2), "zero").body
        got = revisions("bob", pid).body["revisions"]
        self.assertEqual([x["revision"] for x in got], [1, 2, 3])
        self.assertEqual([x["amount"] for x in got], [500, 300, 0])
        self.assertEqual([x["reason"] for x in got], ["", "why", "zero"])
        for x, c in zip(got[1:], (c1, c2)):
            self.assertEqual((x["revision"], x["amount"], x["reason"], dt(x["effective_at"]), dt(x["recorded_at"])),
                             (c["revision"], c["amount"], c["reason"], dt(c["effective_at"]), dt(c["recorded_at"])))
        for x in got:
            self.assertRegex(x["effective_at"], TS_STRICT)
            self.assertRegex(x["recorded_at"], TS_STRICT)

    def test_visibility_of_revisions(self):
        "[RV-02] only the two parties can read it; a third party gets 404 even for a public payment; unknown 404; no token 401"
        pid, _ = self.mk(visibility="public")
        priv, _ = self.mk("ada", "cy", 10, visibility="private")
        for who in ("ada", "bob"):
            self.assertEqual(revisions(who, pid).status, 200)
        for who in ("cy", "eve", "op", "dee"):
            self.err(revisions(who, pid), 404, "not_found")
        self.err(revisions("bob", priv), 404, "not_found")
        self.assertEqual(revisions("cy", priv).status, 200)
        self.err(revisions("ada", "p_nope"), 404, "not_found")
        self.err(call("GET", "/payments/%s/revisions" % pid), 401, "unauthenticated")
        self.err(call("GET", "/payments/%s/revisions" % pid, token="bogus"), 401, "unauthenticated")

    def test_failed_corrections_leave_no_revision(self):
        "[RV-03] rejected corrections (422/403/409) add nothing; replays add nothing"
        pid, _ = self.mk(amount=500)
        correct("ada", pid, 1, -1, ago(seconds=2))
        correct("bob", pid, 1, 5, ago(seconds=2))
        correct("ada", pid, 7, 5, ago(seconds=2))
        key = k()
        correct("ada", pid, 1, 5, ago(seconds=2), key=key)
        correct("ada", pid, 1, 5, ago(seconds=2), key=key)
        self.assertEqual(len(revisions("ada", pid).body["revisions"]), 2)

    def test_seeded_payments_have_revision_one(self):
        "[RV-04] a seeded payment's revision 1 uses its supplied created_at as effective and recorded time; omission uses reset time"
        c = now_utc() - timedelta(days=2)
        fx = base_fixture(payments=[seed_pay("p_1", "ada", "bob", 5, c), seed_pay("p_2", "bob", "cy", 7)], requests=[],
                          users=[user("ada", 9995), user("bob", 2498), user("cy", 1007), user("dee", 0), user("eve", 5000), user("op", 0), user("op2", 0)])
        reset(fx)
        r1 = revisions("ada", "p_1").body["revisions"][0]
        self.assertEqual((dt(r1["effective_at"]), dt(r1["recorded_at"]), r1["revision"], r1["amount"], r1["reason"]), (c, c, 1, 5, ""))
        r2 = revisions("bob", "p_2").body["revisions"][0]
        self.assertEqual(dt(r2["effective_at"]), dt(r2["recorded_at"]))
        self.assertLess(abs((dt(r2["effective_at"]) - now_utc()).total_seconds()), 60)


class Linked(CorrectBase):
    def test_settlement_members_are_immutable(self):
        "[LK-01] correcting a settlement member -> 422 linked_payment_immutable (every member); receipts, balances and history unchanged; revision 1 uses committed_at"
        st = settle("op", [T("ada", "bob", 100), T("ada", "cy", 50), T("bob", "dee", 20)])
        self.assertEqual(st.status, 201)
        ca = dt(st.body["committed_at"])
        before = (bal("ada"), bal("bob"), bal("cy"), bal("dee"))
        owners = ["ada", "ada", "bob"]
        for m, who in zip(st.body["payments"], owners):
            self.err(correct(who, m["payment_id"], 1, 1, ago(seconds=1)), 422, "linked_payment_immutable")
            revs = revisions(who, m["payment_id"]).body["revisions"]
            self.assertEqual(len(revs), 1)
            self.assertEqual((dt(revs[0]["effective_at"]), dt(revs[0]["recorded_at"])), (ca, ca))
        self.assertEqual((bal("ada"), bal("bob"), bal("cy"), bal("dee")), before)
        again = settle("op", [T("ada", "bob", 100), T("ada", "cy", 50), T("bob", "dee", 20)], key=k())
        self.assertEqual(again.status, 201)

    def test_settlement_member_privacy_unchanged(self):
        "[LK-02] settlement constituents keep ordinary feed visibility; their revisions are readable only by their two parties"
        st = settle("op", [T("ada", "bob", 10, visibility="private"), T("ada", "cy", 11, visibility="public")]).body
        priv, pub = st["payments"]
        self.assertNotIn(priv["payment_id"], feed_ids("eve"))
        self.assertIn(pub["payment_id"], feed_ids("eve"))
        self.err(revisions("eve", pub["payment_id"]), 404, "not_found")
        self.err(revisions("op", pub["payment_id"]), 404, "not_found")
        self.assertEqual(revisions("cy", pub["payment_id"]).status, 200)
        self.assertEqual({e["payment"]["payment_id"] for e in all_statement("ada")[1]} >= {priv["payment_id"], pub["payment_id"]}, True)

    def test_capture_payments_are_immutable(self):
        "[LK-03] correcting a capture payment -> 422 linked_payment_immutable; captures appear once in statements with their links"
        a = authorize("ada", "bob", 2000, note="dep").body["authorization_id"]
        c1 = capture("bob", a, 700, final=False).body
        c2 = capture("bob", a, 300).body
        for c in (c1, c2):
            self.err(correct("ada", c["payment_id"], 1, 1, ago(seconds=1)), 422, "linked_payment_immutable")
            self.assertEqual(len(revisions("bob", c["payment_id"]).body["revisions"]), 1)
        for who in ("ada", "bob"):
            ents = [e for e in all_statement(who)[1] if e["payment"].get("authorization_id") == a]
            self.assertEqual(sorted(e["payment"]["payment_id"] for e in ents), sorted([c1["payment_id"], c2["payment_id"]]))
            self.assertEqual(sorted(abs(e["delta"]) for e in ents), [300, 700])
            for e in ents:
                self.assertEqual(e["payment"]["authorization_id"], a)
                self.assertEqual(e["revision"], 1)
        # authorization / release / expiry are not payments: nothing else is in the statement
        self.assertEqual(len([e for e in all_statement("ada")[1] if e["payment"]["note"] == "dep"]), 2)
        self.assertEqual(bal("ada"), 9000)

    def test_unrelated_party_on_linked_gets_403_not_422(self):
        "[LK-04] permission is checked before linkage: a non-sender correcting a capture/settlement member is 403"
        a = authorize("ada", "bob", 100).body["authorization_id"]
        c = capture("bob", a).body
        self.err(correct("bob", c["payment_id"], 1, 1, ago(seconds=1)), 403, "forbidden")
        self.err(correct("cy", c["payment_id"], 1, 1, ago(seconds=1)), 403, "forbidden")


if __name__ == "__main__":
    unittest.main()
