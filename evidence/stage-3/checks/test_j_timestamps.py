"""Stage 3: payment created_at, seeding, GET /me as_of."""
import json
import time
import unittest

from lib import *


class CreatedAt(Base):
    def test_every_payment_shape_carries_an_instant(self):
        "[TS-01] every endpoint that returns a payment includes an RFC 3339 created_at with an offset (create, pay request, capture, settlement, feed, revisions of those)"
        p = pay("ada", "bob", 100)
        rid = req("bob", "ada", 50).body["request_id"]
        pr = pay_req("ada", rid)
        a = authorize("ada", "cy", 200).body["authorization_id"]
        cp = capture("cy", a)
        st = settle("op", [{"from_handle": "ada", "to_handle": "dee", "amount": 10}, {"from_handle": "bob", "to_handle": "dee", "amount": 5}])
        objs = [p.body, pr.body, cp.body] + st.body["payments"] + activity("ada", limit=200)["payments"]
        for o in objs:
            self.assertRegex(o["created_at"], TS_STRICT, o)
            dt(o["created_at"])
            self.assertLess(abs((dt(o["created_at"]) - now_utc()).total_seconds()), 300)
        for o in st.body["payments"]:
            self.assertEqual(o["created_at"], st.body["committed_at"])

    def test_activity_orders_by_created_at(self):
        "[TS-02] GET /activity stays newest first by created_at, also when seeded payments are listed out of order"
        now0 = now_utc()
        pays = [seed_pay("p_a", "ada", "bob", 1, now0 - timedelta(hours=1)), seed_pay("p_b", "ada", "bob", 2, now0 - timedelta(hours=5)),
                seed_pay("p_c", "ada", "bob", 3, now0 - timedelta(hours=3)), seed_pay("p_d", "ada", "bob", 4, now0 - timedelta(hours=2))]
        reset(base_fixture(payments=pays, requests=[], users=[user("ada", 9990), user("bob", 2510), user("cy", 0)]))
        got = [p["payment_id"] for p in activity("ada", limit=200)["payments"]]
        self.assertEqual(got, ["p_a", "p_d", "p_c", "p_b"])
        time.sleep(1.1)
        new = pay("ada", "bob", 5).body["payment_id"]
        self.assertEqual([p["payment_id"] for p in activity("ada", limit=200)["payments"]][0], new)
        self.assertEqual(activity("ada", limit=2)["has_more"], True)

    def test_seeded_created_at_is_kept(self):
        "[TS-03] a seeded created_at is the payment's created_at (any offset), in the feed and in the receipt shape"
        c1 = now_utc() - timedelta(days=3, hours=2, minutes=7, seconds=11)
        local = c1.astimezone(timezone(timedelta(hours=5, minutes=30)))
        pays = [seed_pay("p_1", "ada", "bob", 5, local.isoformat(timespec="seconds")), seed_pay("p_2", "bob", "cy", 7, c1 - timedelta(days=1))]
        reset(base_fixture(payments=pays, requests=[], users=[user("ada", 995), user("bob", 2998), user("cy", 1007), user("dee", 0)]))
        feed = {p["payment_id"]: p for p in activity("bob", limit=200)["payments"]}
        self.assertEqual(dt(feed["p_1"]["created_at"]), dt(local.isoformat(timespec="seconds")))
        self.assertEqual(dt(feed["p_2"]["created_at"]), c1 - timedelta(days=1))
        for p in feed.values():
            self.assertRegex(p["created_at"], TS_STRICT)
        self.assertEqual([p["payment_id"] for p in activity("bob", limit=200)["payments"]], ["p_1", "p_2"])

    def test_omitted_created_at_is_reset_time_before_api_payments(self):
        "[TS-04] omitted created_at = reset time, not later than any API payment made afterwards"
        before = now_utc() - timedelta(seconds=2)
        reset()
        after_reset = now_utc() + timedelta(seconds=2)
        feed = activity("ada", limit=200)["payments"]
        self.assertTrue(feed)
        for p in feed:
            c = dt(p["created_at"])
            self.assertTrue(before <= c <= after_reset, p)
        seeded = {p["payment_id"]: dt(p["created_at"]) for p in feed}
        time.sleep(1.1)
        newp = pay("ada", "bob", 1).body
        for pid, c in seeded.items():
            self.assertLess(c, dt(newp["created_at"]))
        self.assertEqual(activity("ada", limit=200)["payments"][0]["payment_id"], newp["payment_id"])

    def test_future_seeded_created_at_is_rejected_without_change(self):
        "[TS-05] a seeded created_at in the future -> 422 validation_failed from reset; previous state untouched"
        pay("ada", "bob", 1)
        before = (bal("ada"), bal("bob"), feed_ids("ada"))
        for delta in (timedelta(hours=1), timedelta(days=400), timedelta(minutes=10)):
            fx = base_fixture(payments=[seed_pay("p_x", "ada", "bob", 5, now_utc() + delta)])
            self.err(call("POST", "/_test/reset", fx, timeout=10), 422, "validation_failed")
        self.assertEqual((bal("ada"), bal("bob"), feed_ids("ada")), before)
        # the first of several seeded payments being fine does not rescue a later future one
        fx = base_fixture(payments=[seed_pay("p_1", "ada", "bob", 5, now_utc() - timedelta(days=1)), seed_pay("p_2", "bob", "cy", 5, now_utc() + timedelta(hours=2))])
        self.err(call("POST", "/_test/reset", fx, timeout=10), 422, "validation_failed")
        self.assertEqual((bal("ada"), bal("bob"), feed_ids("ada")), before)

    def test_loading_seeded_payments_does_not_change_balances(self):
        "[TS-06] a fixture balance remains the balance after all seeded payments"
        fx, led, now0 = hist(H_SPECS)
        reset(fx)
        for n in ("ada", "bob", "cy", "dee", "eve"):
            want = [u for u in fx["users"] if u["id"] == "u_" + n][0]["balance"]
            self.assertEqual(bal(n), want, n)
        self.assertEqual(total(), H_TOTAL)

    def test_payments_created_in_order_have_nondecreasing_created_at(self):
        "[TS-07] API payments get increasing created_at; instants are real (near now)"
        ts = []
        for _ in range(4):
            ts.append(dt(pay("ada", "bob", 1).body["created_at"]))
            time.sleep(0.05)
        self.assertEqual(ts, sorted(ts))
        self.assertLess(abs((ts[-1] - now_utc()).total_seconds()), 30)


class AsOf(Base):
    def setUp(self):
        self.fx, self.led, self.now0 = hist(H_SPECS)
        reset(self.fx)

    def bal_at(self, name, as_of, **kw):
        r = me_at(name, as_of, **kw)
        self.assertEqual(r.status, 200, r)
        return r.body

    def test_opening_and_walk_forward(self):
        "[AO-01] before the earliest payment: the opening balance; each later instant: balance after every payment at or before it"
        n = self.now0
        ada = lambda h: iso6(n - timedelta(hours=h))
        self.assertEqual(self.bal_at("ada", ada(11))["balance"], 10000)   # opening: what the wallet held before anything moved
        self.assertEqual(self.bal_at("ada", ada(9))["balance"], 9500)
        self.assertEqual(self.bal_at("ada", ada(7))["balance"], 9500)
        self.assertEqual(self.bal_at("ada", ada(5))["balance"], 9600)
        self.assertEqual(self.bal_at("ada", ada(3))["balance"], 8600)
        self.assertEqual(self.bal_at("bob", ada(9))["balance"], 2500)
        self.assertEqual(self.bal_at("bob", ada(7))["balance"], 2300)
        self.assertEqual(self.bal_at("cy", ada(7))["balance"], 1200)
        self.assertEqual(self.bal_at("dee", ada(5))["balance"], 0)
        self.assertEqual(self.bal_at("dee", ada(3))["balance"], 1000)
        self.assertEqual(self.bal_at("eve", ada(11))["balance"], 5000)

    def test_inclusive_instant(self):
        "[AO-02] a payment made at exactly as_of counts as having happened; one microsecond earlier it has not"
        t = self.now0 - timedelta(hours=10)
        self.assertEqual(self.bal_at("ada", iso6(t))["balance"], 9500)
        self.assertEqual(self.bal_at("ada", iso6(t - timedelta(microseconds=1)))["balance"], 10000)
        self.assertEqual(self.bal_at("bob", iso6(t))["balance"], 2500)
        self.assertEqual(self.bal_at("bob", iso6(t - timedelta(microseconds=1)))["balance"], 2000)

    def test_after_latest_is_current(self):
        "[AO-03] as_of at or after the latest payment (and in the future) returns the current balance"
        cur = me("ada")["balance"]
        for h in (4, 3, 1):
            self.assertEqual(self.bal_at("ada", iso6(self.now0 - timedelta(hours=h)))["balance"], 8600 if h <= 4 else 0)
        for off in (timedelta(minutes=1), timedelta(days=30), timedelta(days=3650)):
            self.assertEqual(self.bal_at("ada", iso6(now_utc() + off))["balance"], cur)
        pay("ada", "bob", 50)
        self.assertEqual(self.bal_at("ada", iso6(now_utc() + timedelta(days=1)))["balance"], cur - 50)

    def test_before_everything_is_opening(self):
        "[AO-04] as_of before the earliest payment (even year 1970) returns the opening balance = seeded ending balance minus the net effect of seeded payments"
        for iso in ("1970-01-01T00:00:00+00:00", "2000-01-01T00:00:00Z", "0001-01-01T00:00:00+00:00"):
            m = self.bal_at("ada", iso)
            self.assertEqual((m["balance"], m["total"], m["available"], m["held"]), (10000, 10000, 10000, 0))
        self.assertEqual(self.bal_at("dee", "1970-01-01T00:00:00+00:00")["balance"], 0)

    def test_new_accounts_open_at_zero(self):
        "[AO-05] a new account's opening balance is 0; it is 0 as of any earlier instant and counts only later payments"
        s = signup("fresh.acct@example.com")
        t = call("GET", "/me?" + q(as_of="1970-01-01T00:00:00+00:00"), token=s.body["token"])
        self.assertEqual(t.body["balance"], 0)
        time.sleep(1.1)
        c = pay("ada", "fresh_acct", 300).body["created_at"]
        self.assertEqual(call("GET", "/me?" + q(as_of=c), token=s.body["token"]).body["balance"], 300)
        self.assertEqual(call("GET", "/me?" + q(as_of=iso6(dt(c) - timedelta(seconds=1))), token=s.body["token"]).body["balance"], 0)

    def test_echo_and_money_fields(self):
        "[AO-06] as_of is echoed exactly as given; balance = total; available = total - held (0 held); other fields kept"
        for given in ("2026-09-24T13:20:00+00:00", "2026-09-24T13:20:00Z", "2026-09-24T15:20:00.250+02:00", "2000-01-01T00:00:00.123456-05:30"):
            r = me_at("ada", given)
            self.assertEqual(r.status, 200, r)
            self.assertEqual(r.body["as_of"], given)
            m = r.body
            self.assertEqual(m["balance"], m["total"])
            self.assertEqual(m["available"], m["total"] - m["held"])
            self.assertEqual((m["user_id"], m["handle"], m["currency"], m["minor_units"]), ("u_ada", "ada", "EUR", 2))
            for f in ("balance", "total", "available", "held"):
                self.is_int(m[f])

    def test_equivalent_offsets(self):
        "[AO-07] the instant, not the spelling, decides: the same moment in +00:00, Z and +05:30 gives the same balance"
        t = self.now0 - timedelta(hours=8)
        forms = [iso6(t), t.strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z", t.astimezone(timezone(timedelta(hours=5, minutes=30))).isoformat(timespec="microseconds"),
                 t.astimezone(timezone(timedelta(hours=-8))).isoformat(timespec="microseconds")]
        vals = [self.bal_at("bob", f) for f in forms]
        self.assertEqual({v["balance"] for v in vals}, {2300})
        self.assertEqual([v["as_of"] for v in vals], forms)

    def test_invalid_as_of_is_422(self):
        "[AO-08] naive local time, bare date, empty value, garbage, impossible dates -> 422 validation_failed"
        for bad in ("2026-09-24T13:20:00", "2026-09-24", "", "yesterday", "1727184000", "2026-13-01T00:00:00Z", "2026-02-30T00:00:00Z", "2026-09-24T25:00:00Z",
                    "2026-09-24T13:20:00+25:00", "2026-09-24T13:20Z", "13:20:00Z", "2026-09-24T13:20:00 +00:00 ", "null", "NaN"):
            r = call("GET", "/me?" + urllib.parse.urlencode({"as_of": bad}), token=tok("ada"))
            self.err(r, 422, "validation_failed")
        r = call("GET", "/me?as_of=", token=tok("ada"))
        self.err(r, 422, "validation_failed")
        r = call("GET", "/me?as_of", token=tok("ada"))
        self.err(r, 422, "validation_failed")

    def test_no_temporal_params_unchanged(self):
        "[AO-09] without temporal parameters /me keeps the existing money fields and reports current values; unknown params ignored"
        m = call("GET", "/me", token=tok("ada")).body
        for f, v in (("balance", 8600), ("total", 8600), ("available", 8600), ("held", 0)):
            self.assertEqual(m[f], v)
        self.assertEqual(call("GET", "/me?foo=bar&Known_At=1", token=tok("ada")).body["balance"], 8600)

    def test_requires_authentication(self):
        "[AO-10] as_of without a token is 401"
        self.err(call("GET", "/me?" + q(as_of="2026-01-01T00:00:00Z")), 401, "unauthenticated")

    def test_sum_of_balances_is_the_seeded_total_at_every_instant(self):
        "[AO-11] the sum of all wallets' balances equals the seeded total as of any instant"
        names = ["ada", "bob", "cy", "dee", "eve", "op", "op2"]
        for h in (12, 10, 9, 8, 7, 6, 5, 4, 3, 0.5):
            t = iso6(self.now0 - timedelta(hours=h))
            self.assertEqual(sum(self.bal_at(n, t)["balance"] for n in names), H_TOTAL, h)
        pay("ada", "bob", 77)
        for h in (12, 6, 0.5):
            t = iso6(self.now0 - timedelta(hours=h))
            self.assertEqual(sum(self.bal_at(n, t)["balance"] for n in names), H_TOTAL, h)

    def test_corner_instants_with_subsecond_precision(self):
        "[AO-12] fractional seconds are honoured (instant resolution is finer than a second)"
        t = self.now0 - timedelta(hours=6)  # cy -> ada 100
        self.assertEqual(self.bal_at("ada", iso6(t - timedelta(milliseconds=1)))["balance"], 9500)
        self.assertEqual(self.bal_at("ada", iso6(t))["balance"], 9600)
        self.assertEqual(self.bal_at("ada", iso6(t + timedelta(milliseconds=1)))["balance"], 9600)

    def test_ties_at_one_instant_combine(self):
        "[AO-13] payments with the same instant all count at that instant"
        t = self.now0 - timedelta(hours=2)
        specs = [("p_001", "ada", "bob", 100, 2), ("p_002", "ada", "cy", 200, 2), ("p_003", "bob", "ada", 50, 2)]
        fx, led, n0 = hist(specs, now0=self.now0)
        reset(fx)
        self.assertEqual(self.bal_at("ada", iso6(t))["balance"], 10000 - 100 - 200 + 50)
        self.assertEqual(self.bal_at("ada", iso6(t - timedelta(microseconds=1)))["balance"], 10000)


if __name__ == "__main__":
    unittest.main()
