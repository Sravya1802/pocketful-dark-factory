"""Stage 3: known_at (recorded time), effective time, historical holds, model-based fuzz."""
import json
import random
import time
import unittest

from lib import *
from oracle import Ledger, Hold, INF

US = timedelta(microseconds=1)


def instants_close(a, b, tol=0.05):
    return abs((a - b).total_seconds()) <= tol


class Scenario:
    """fixture + oracle + helpers to execute corrections and compare views"""

    def __init__(self, tc, specs, openings=None, now0=None):
        self.tc = tc
        self.fx, self.led, self.now0 = hist(specs, openings, now0)
        reset(self.fx)
        self.owner = {pid: f for pid, f, t, a, h in specs}
        self.rec_times = []

    def correct(self, pid, amount, eff, reason="r", expect=201, code=None, rev=None):
        led = self.led
        cur = led.latest(pid)[0] if rev is None else rev
        r = correct(self.owner.get(pid, "ada"), pid, cur, amount, eff, reason)
        self.tc.assertEqual(r.status, expect, r)
        if expect == 201:
            led.add_rev(pid, r.body["revision"], r.body["amount"], dt(r.body["effective_at"]), dt(r.body["recorded_at"]), reason)
            self.rec_times.append(dt(r.body["recorded_at"]))
        elif code:
            self.tc.assertEqual(r.code, code, r)
        return r

    def me_check(self, name, T=None, K=None):
        led, tc = self.led, self.tc
        r = me_at(name, iso6(T) if T else None, iso6(K) if K else None)
        tc.assertEqual(r.status, 200, r)
        want = led.me("u_" + name, T if T else INF, K if K else INF)
        got = {f: r.body[f] for f in ("balance", "total", "held", "available")}
        tc.assertEqual(got, want, (name, T and iso6(T), K and iso6(K)))
        if T:
            tc.assertEqual(r.body["as_of"], iso6(T))
        if K:
            tc.assertEqual(r.body["known_at"], iso6(K))

    def st_check(self, name, frm=None, to=None, K=None, limit=200, offset=0):
        led, tc = self.led, self.tc
        r = statement(name, **{"from": iso6(frm) if frm else None, "to": iso6(to) if to else None, "known_at": iso6(K) if K else None},
                      limit=limit, offset=offset)
        tc.assertEqual(r.status, 200, r)
        want = led.statement("u_" + name, frm, to, K if K else INF, limit, offset)
        b = r.body
        ctx = (name, frm and iso6(frm), to and iso6(to), K and iso6(K), limit, offset)
        tc.assertEqual((b["opening_balance"], b["closing_balance"], b["has_more"]), (want["opening_balance"], want["closing_balance"], want["has_more"]), ctx)
        got = [(e["payment"]["payment_id"], e["delta"], e["balance_after"], e["revision"], dt(e["effective_at"]), dt(e["recorded_at"]), e["payment"]["amount"])
               for e in b["entries"]]
        exp = [(e["pid"], e["delta"], e["balance_after"], e["revision"], e["effective_at"], e["recorded_at"], e["amount"]) for e in want["entries"]]
        tc.assertEqual(got, exp, ctx)
        tc.assertEqual(want["opening_balance"] + sum(e["delta"] for e in want["all"]), want["closing_balance"])


class KnownAt(Base):
    def test_known_at_selects_the_latest_revision_recorded_at_or_before(self):
        "[BT-01] known_at picks each payment's latest revision recorded at or before it (inclusive); effective times place it"
        sc = Scenario(self, H_SPECS)
        n0 = sc.now0
        r1 = sc.correct("p_001", 300, n0 - timedelta(hours=10), "a")
        time.sleep(0.01)
        r2 = sc.correct("p_001", 100, n0 - timedelta(hours=9), "b")
        a, b = dt(r1.body["recorded_at"]), dt(r2.body["recorded_at"])
        self.assertLess(a, b)
        asof = n0 - timedelta(hours=9, minutes=30)
        for K in (a - US, a, b - US, b, b + timedelta(days=1), None):
            sc.me_check("ada", asof, K)
            sc.me_check("bob", asof, K)
            sc.me_check("ada", None, K)
        t9 = iso6(n0 - timedelta(hours=9))
        self.assertEqual(me_at("ada", t9, iso6(a - US)).body["balance"], 9500)   # original 500
        self.assertEqual(me_at("ada", t9, iso6(a)).body["balance"], 9700)        # rev 2 (300) recorded exactly at a
        self.assertEqual(me_at("ada", t9, iso6(b - US)).body["balance"], 9700)
        self.assertEqual(me_at("ada", t9, iso6(b)).body["balance"], 9900)        # rev 3 (100) effective exactly at as_of
        self.assertEqual(me_at("ada", iso6(n0 - timedelta(hours=9, minutes=1)), iso6(b)).body["balance"], 10000)  # rev 3 lands after this as_of and REPLACES rev 2: nothing is counted yet
        self.assertEqual(me("ada")["balance"], 9000)   # 8600 after the seeded history + 200 + 200 given back

    def test_payment_not_yet_recorded_contributes_nothing(self):
        "[BT-02] if no revision was recorded at known_at the payment contributes nothing (seeded payments and API payments)"
        sc = Scenario(self, H_SPECS)
        n0 = sc.now0
        far = iso6(now_utc() + timedelta(days=1))
        for who, opening in (("ada", 10000), ("bob", 2000), ("cy", 1000), ("dee", 0)):
            self.assertEqual(me_at(who, far, iso6(n0 - timedelta(hours=11))).body["balance"], opening, who)
        self.assertEqual(me_at("ada", far, iso6(n0 - timedelta(hours=10))).body["balance"], 9500)        # recorded exactly at known_at
        self.assertEqual(me_at("ada", far, iso6(n0 - timedelta(hours=5))).body["balance"], 9600)
        b = statement("ada", known_at=iso6(n0 - timedelta(hours=11))).body
        self.assertEqual((b["entries"], b["opening_balance"], b["closing_balance"]), ([], 10000, 10000))
        time.sleep(1.1)
        p = pay("ada", "bob", 40).body
        before = iso6(dt(p["created_at"]) - timedelta(seconds=2))
        self.assertEqual(me_at("ada", far, before).body["balance"], 8600)
        self.assertEqual(me_at("ada", far).body["balance"], 8560)
        self.assertEqual(me_at("ada", far, iso6(dt(p["created_at"]))).body["balance"], 8560)
        for who in ("ada", "bob"):
            ids = [e["payment"]["payment_id"] for e in statement(who, known_at=before, limit=200).body["entries"]]
            self.assertNotIn(p["payment_id"], ids)

    def test_known_at_validation_and_echo(self):
        "[BT-03] known_at is echoed exactly; naive, bare date and empty values -> 422 (also for /statement); future known_at = everything known"
        for given in ("2099-01-01T00:00:00+00:00", "2099-01-01T01:00:00.5+01:00", "2000-01-01T00:00:00Z"):
            r = me_at("ada", None, given)
            self.assertEqual(r.status, 200, r)
            self.assertEqual(r.body["known_at"], given)
            self.assertNotIn("as_of", r.body)
            r = me_at("ada", given, given)
            self.assertEqual((r.body["as_of"], r.body["known_at"]), (given, given))
        for bad in ("2026-09-24T13:20:00", "2026-09-24", "", "x", "2026-09-24T13:20:00+99:00"):
            self.err(call("GET", "/me?" + urllib.parse.urlencode({"known_at": bad}), token=tok("ada")), 422, "validation_failed")
            self.err(call("GET", "/me?" + urllib.parse.urlencode({"as_of": "2026-01-01T00:00:00Z", "known_at": bad}), token=tok("ada")), 422, "validation_failed")
            self.err(call("GET", "/statement?" + urllib.parse.urlencode({"known_at": bad}), token=tok("ada")), 422, "validation_failed")
        self.assertEqual(me_at("ada", None, "2099-01-01T00:00:00Z").body["balance"], me("ada")["balance"])

    def test_effective_time_moves_a_payment_across_a_query_instant(self):
        "[BT-04] a correction moving a payment's effective time later removes it from earlier as_of views (once known) but not from older known_at views"
        sc = Scenario(self, H_SPECS)
        n0 = sc.now0
        r = sc.correct("p_002", 200, n0 - timedelta(hours=3), "later")  # bob->cy 200: -8h -> -3h
        rec = dt(r.body["recorded_at"])
        asof = n0 - timedelta(hours=5)
        self.assertEqual(me_at("bob", iso6(asof)).body["balance"], 2500)                    # known now: not yet paid at -5h
        self.assertEqual(me_at("bob", iso6(asof), iso6(rec - US)).body["balance"], 2300)    # as known before the correction
        for who in ("bob", "cy", "ada"):
            sc.me_check(who, asof)
            sc.me_check(who, asof, rec - US)
            sc.st_check(who)
            sc.st_check(who, K=rec - US)
            sc.st_check(who, frm=n0 - timedelta(hours=4))

    def test_statement_after_corrections(self):
        "[BT-05] statement order = selected effective time then id; payment.amount is the selected amount; zero-amount revisions appear with delta 0; one entry per payment"
        sc = Scenario(self, H_SPECS)
        n0 = sc.now0
        sc.correct("p_004", 0, n0 - timedelta(hours=4), "reverse")               # ada->dee 1000 reversed in place
        sc.correct("p_001", 800, n0 - timedelta(hours=5), "later and bigger")    # moves after p_002 (bob->cy), before p_003? (-5h is after -6h)
        sc.correct("p_003", 50, n0 - timedelta(hours=11), "earlier")             # cy->ada moves before everything
        b, ents = all_statement("ada")
        self.assertEqual([e["payment"]["payment_id"] for e in ents], ["p_003", "p_001", "p_004"])
        self.assertEqual([e["payment"]["amount"] for e in ents], [50, 800, 0])
        self.assertEqual([e["delta"] for e in ents], [50, -800, 0])
        self.assertEqual([e["revision"] for e in ents], [2, 2, 2])
        self.assertEqual([e["balance_after"] for e in ents], [10050, 9250, 9250])
        self.assertEqual(len({e["payment"]["payment_id"] for e in ents}), 3)
        for e in ents:
            self.assertGreater(dt(e["recorded_at"]), dt(e["payment"]["created_at"]))
        self.assertEqual((b["opening_balance"], b["closing_balance"]), (10000, 9250))
        for who in ("ada", "bob", "cy", "dee", "eve"):
            sc.st_check(who)
        self.assertEqual(sum(sc.led.me("u_" + n)["balance"] for n in OPEN0), H_TOTAL)

    def test_window_membership_moves_with_corrections(self):
        "[BT-06] a correction can move a payment into or out of a [from,to) window; windows and known_at compose"
        sc = Scenario(self, H_SPECS)
        n0 = sc.now0
        w = (n0 - timedelta(hours=7), n0 - timedelta(hours=5))   # contains p_003 (-6h) for ada
        win = {"from": iso6(w[0]), "to": iso6(w[1])}
        self.assertEqual([e["payment"]["payment_id"] for e in statement("ada", **win).body["entries"]], ["p_003"])
        r = sc.correct("p_003", 100, n0 - timedelta(hours=3), "out")
        rec = dt(r.body["recorded_at"])
        self.assertEqual(statement("ada", **win).body["entries"], [])
        self.assertEqual([e["payment"]["payment_id"] for e in statement("ada", **win, known_at=iso6(rec - US)).body["entries"]], ["p_003"])
        r2 = sc.correct("p_001", 500, n0 - timedelta(hours=6), "in")
        self.assertEqual([e["payment"]["payment_id"] for e in statement("ada", **win).body["entries"]], ["p_001"])
        for who in ("ada", "bob", "cy"):
            sc.st_check(who, frm=w[0], to=w[1])
            sc.st_check(who, frm=w[0], to=w[1], K=rec - US)
            sc.st_check(who, frm=w[0], K=dt(r2.body["recorded_at"]))

    def test_activity_is_not_temporal(self):
        "[BT-07] corrections and known_at do not change GET /activity at all"
        sc = Scenario(self, H_SPECS)
        before = activity("ada", limit=200)
        sc.correct("p_001", 1, sc.now0 - timedelta(hours=10))
        self.assertEqual(activity("ada", limit=200), before)

    def test_current_balance_without_params_reports_corrected_values(self):
        "[BT-08] /me without temporal parameters reports the corrected current balance; /statement closing = it"
        sc = Scenario(self, H_SPECS)
        sc.correct("p_004", 400, sc.now0 - timedelta(hours=4))
        self.assertEqual(me("ada")["balance"], 10000 - 500 + 100 - 400)
        self.assertEqual(statement("ada").body["closing_balance"], me("ada")["balance"])
        self.assertEqual(me("dee")["balance"], 400)

    def test_old_known_at_after_many_corrections(self):
        "[BT-09] every historical known_at keeps answering as it did (immutability of the past)"
        sc = Scenario(self, H_SPECS)
        n0 = sc.now0
        marks = []
        T = n0 - timedelta(hours=5)
        for amt, hrs in ((450, 10), (400, 10), (0, 9), (700, 11), (50, 3)):
            marks.append((now_utc(), me_at("ada", iso6(T)).body["balance"], me_at("bob", iso6(T)).body["balance"]))
            sc.correct("p_001", amt, n0 - timedelta(hours=hrs))
            time.sleep(0.002)
        for ts, ada, bob in marks:
            self.assertEqual(me_at("ada", iso6(T), iso6(ts)).body["balance"], ada)
            self.assertEqual(me_at("bob", iso6(T), iso6(ts)).body["balance"], bob)
            sc.me_check("ada", T, ts)


class HistoricalHolds(Base):
    def setUp(self):
        reset(base_fixture(payments=[], requests=[]))   # no seeded payments: openings are the fixture balances (ada 10000, bob 2500)

    def lifecycle(self):
        """a hold with two nonfinal captures and a void, events >= 1.2 s apart"""
        a = authorize("ada", "bob", 2000)
        self.assertEqual(a.status, 201)
        aid = a.body["authorization_id"]
        self.assertIsNone(a.body["closed_at"])
        created = dt(a.body["created_at"])
        time.sleep(1.2)
        c1 = capture("bob", aid, 700, final=False).body
        time.sleep(1.2)
        c2 = capture("bob", aid, 300, final=False).body
        time.sleep(1.2)
        v = void("ada", aid)
        self.assertEqual(v.status, 200)
        closed = dt(v.body["closed_at"])
        return aid, created, dt(c1["created_at"]), dt(c2["created_at"]), closed, v.body

    def hold_model(self, aid, created, c1, c2, closed, expires, kind="void"):
        h = Hold(aid, "u_ada", 2000, created, expires)
        h.captures = [(c1, 700), (c2, 300)]
        h.close_kind, h.closed_at = kind, closed
        return h

    def test_hold_lifecycle_in_historical_views(self):
        "[HH-01] as_of views: hold starts at creation, nonfinal capture reduces it at capture time, void releases the remainder at its time; balance=total, available=total-held"
        aid, created, c1, c2, closed, vb = self.lifecycle()
        self.assertGreater(closed, c2)
        exp = dt(vb["expires_at"])
        led = Ledger({"u_ada": 10000, "u_bob": 2500})
        led.add_payment("c1", "u_ada", "u_bob", 700, c1)
        led.add_payment("c2", "u_ada", "u_bob", 300, c2)
        led.holds = [self.hold_model(aid, created, c1, c2, closed, exp)]
        probes = [created - timedelta(seconds=1), created, created + timedelta(seconds=0.6), c1 - US, c1, c1 + timedelta(seconds=0.6), c2 - US, c2,
                  c2 + timedelta(seconds=0.6), closed - US, closed, closed + timedelta(seconds=1), now_utc() + timedelta(days=2)]
        for T in probes:
            for who, uid in (("ada", "u_ada"), ("bob", "u_bob")):
                r = me_at(who, iso6(T))
                self.assertEqual(r.status, 200, r)
                m = r.body
                want = led.me(uid, T)
                self.assertEqual((m["balance"], m["total"], m["held"], m["available"]), (want["balance"], want["total"], want["held"], want["available"]), (who, iso6(T)))
                self.assertEqual(m["balance"], m["total"])
                self.assertEqual(m["available"], m["total"] - m["held"])
        self.assertEqual(me_at("ada", iso6(created + timedelta(seconds=0.6))).body["held"], 2000)
        self.assertEqual(me_at("ada", iso6(c1 + timedelta(seconds=0.6))).body["held"], 1300)
        self.assertEqual(me_at("ada", iso6(c2 + timedelta(seconds=0.6))).body["held"], 1000)
        self.assertEqual(me_at("ada", iso6(closed)).body["held"], 0)
        self.assertEqual(me_at("ada", iso6(created - timedelta(seconds=1))).body["held"], 0)
        m = me("ada")
        self.assertEqual((m["held"], m["available"], m["total"]), (0, 9000, 9000))

    def test_known_at_with_holds(self):
        "[HH-02] events other than expiry are known at their own time; once creation is known the expiry deadline is known too"
        aid, created, c1, c2, closed, vb = self.lifecycle()
        exp = dt(vb["expires_at"])
        after_everything = now_utc() + timedelta(minutes=1)
        self.assertGreater(exp, after_everything)
        m = me_at("ada", iso6(after_everything), iso6(created - timedelta(seconds=1))).body       # creation not yet known
        self.assertEqual((m["held"], m["balance"]), (0, 10000))
        m = me_at("ada", iso6(closed + timedelta(seconds=1)), iso6(c2 + timedelta(seconds=0.6))).body   # void unknown: still held
        self.assertEqual((m["held"], m["balance"]), (1000, 9000))
        m = me_at("ada", iso6(closed + timedelta(seconds=1)), iso6(closed)).body                       # void known
        self.assertEqual(m["held"], 0)
        m = me_at("ada", iso6(exp + timedelta(seconds=1)), iso6(created + timedelta(seconds=0.6))).body  # deadline known from creation
        self.assertEqual(m["held"], 0)
        m = me_at("ada", iso6(after_everything), iso6(c1 - US)).body                                     # captures unknown: hold not reduced
        self.assertEqual((m["held"], m["total"]), (2000, 10000))

    def test_expiry_at_the_deadline_and_future_queries(self):
        "[HH-03] expiry takes effect at expires_at (inclusive); for queries beyond now an open hold expires at its deadline"
        a = authorize("ada", "bob", 1500).body
        aid = a["authorization_id"]
        created, exp = dt(a["created_at"]), dt(a["expires_at"])
        self.assertIsNone(a["closed_at"])
        self.assertEqual(me_at("ada", iso6(exp - timedelta(seconds=1))).body["held"], 1500)
        self.assertEqual(me_at("ada", iso6(exp - US)).body["held"], 1500)
        self.assertEqual(me_at("ada", iso6(exp)).body["held"], 0)
        self.assertEqual(me_at("ada", iso6(exp + timedelta(days=30))).body["held"], 0)
        self.assertEqual(me_at("ada", iso6(created + timedelta(seconds=1))).body["held"], 1500)
        m = me_at("ada", iso6(exp - timedelta(seconds=30))).body
        self.assertEqual((m["held"], m["available"], m["balance"]), (1500, 8500, 10000))
        self.assertEqual(me("ada")["held"], 1500)
        self.assertIsNone(az_get("ada", aid)["closed_at"])

    def test_short_ttl_expiry_sets_closed_at(self):
        "[HH-04] a hold that expired on the clock has closed_at == expires_at; historical views release it exactly there"
        reset(azfixture(ttl=2))
        a = authorize("ada", "bob", 3000).body
        aid, created, exp = a["authorization_id"], dt(a["created_at"]), dt(a["expires_at"])
        time.sleep(max(0, (exp - now_utc()).total_seconds()) + 1.3)
        row = az_get("ada", aid)
        self.assertEqual(row["status"], "expired")
        self.assertEqual(dt(row["closed_at"]), exp)
        self.assertEqual(me_at("ada", iso6(exp - US)).body["held"], 3000)
        self.assertEqual(me_at("ada", iso6(exp)).body["held"], 0)
        self.assertEqual(me_at("ada", iso6(exp - US)).body["available"], 7000)
        self.assertEqual(me("ada")["available"], 10000)
        self.assertEqual(az_get("bob", aid)["closed_at"], row["closed_at"])

    def test_closed_at_on_every_authorization_response(self):
        "[HH-05] authorizations expose closed_at: null while open; the event time when closed by final capture or void"
        a1 = authorize("ada", "bob", 500).body
        a2 = authorize("ada", "bob", 400).body
        a3 = authorize("ada", "cy", 300).body
        self.assertTrue(all("closed_at" in x and x["closed_at"] is None for x in (a1, a2, a3)))
        c = capture("bob", a1["authorization_id"], 500).body
        v = void("ada", a2["authorization_id"]).body
        capture("cy", a3["authorization_id"], 100, final=False)
        r1, r2, r3 = (az_get("ada", x["authorization_id"]) for x in (a1, a2, a3))
        self.assertTrue(instants_close(dt(r1["closed_at"]), dt(c["created_at"])), (r1["closed_at"], c["created_at"]))
        self.assertEqual(dt(r2["closed_at"]), dt(v["closed_at"]))
        self.assertLess(abs((dt(r2["closed_at"]) - now_utc()).total_seconds()), 30)
        self.assertIsNone(r3["closed_at"])
        self.assertEqual(r3["status"], "open")
        self.assertRegex(r1["closed_at"], TS_STRICT)
        listed = {x["authorization_id"]: x for x in auths_of("bob", limit=200)["authorizations"]}
        self.assertEqual(listed[a1["authorization_id"]]["closed_at"], r1["closed_at"])
        self.assertEqual(void("ada", a2["authorization_id"]).body["closed_at"], v["closed_at"])  # voiding again keeps the original closed_at

    def test_seeded_holds_in_history(self):
        "[HH-06] seeded open holds are created at reset unless created_at is supplied; seeded closed holds hold nothing at any time"
        n0 = now_utc()
        c = n0 - timedelta(hours=6)
        fx = azfixture([seed_auth("a_1", "ada", "bob", 2000, created_at=iso6(c)), seed_auth("a_2", "ada", "cy", 500),
                        seed_auth("a_3", "ada", "bob", 900, status="voided"), seed_auth("a_4", "ada", "bob", 800, status="expired", expires_in=-7200),
                        seed_auth("a_5", "ada", "bob", 700, status="captured")])
        reset(fx)
        reset_time = now_utc()
        m = lambda t: me_at("ada", iso6(t)).body
        self.assertEqual(m(c - timedelta(seconds=1))["held"], 0)
        self.assertEqual(m(c)["held"], 2000)
        self.assertEqual(m(c + timedelta(hours=1))["held"], 2000)
        self.assertEqual(m(reset_time - timedelta(hours=1))["held"], 2000)            # a_2 (no created_at) not yet created
        self.assertEqual(m(reset_time + timedelta(seconds=1))["held"], 2500)          # created at reset
        for t in (c - timedelta(hours=1), c, reset_time, reset_time + timedelta(days=1)):
            x = m(t)
            self.assertEqual(x["available"], x["total"] - x["held"])
            self.assertEqual(x["balance"], x["total"])
        self.assertEqual(me("ada")["held"], 2500)
        self.assertEqual(me_at("ada", iso6(reset_time + timedelta(seconds=1)), iso6(c - timedelta(seconds=1))).body["held"], 0)

    def test_corrections_see_holds_in_history(self):
        "[HH-07] a past correction is judged against total AND available at past hold boundaries; releasing the hold later does not rewrite the past"
        specs = [("p_001", "ada", "bob", 100, 10)]
        fx, led, n0 = hist(specs, openings={"ada": 1000})
        reset(fx)
        a = authorize("ada", "cy", 800).body["authorization_id"]
        time.sleep(1.2)
        pay("eve", "ada", 500)
        self.err(correct("ada", "p_001", 1, 700, n0 - timedelta(hours=10)), 409, "historical_overdraft")
        void("ada", a)
        r = correct("ada", "p_001", 1, 700, n0 - timedelta(hours=10))
        self.err(r, 409, "historical_overdraft")  # at the hold's creation the hold (800) exceeded total (300): the past stays what it was
        ok = correct("ada", "p_001", 1, 150, n0 - timedelta(hours=10))
        self.assertEqual(ok.status, 201, ok)      # 1000-150 = 850 >= 800 at the hold boundary


class ModelFuzz(Base):
    def build(self, seed):
        rnd = random.Random(seed)
        users = ["ada", "bob", "cy", "dee", "eve"]
        bal0 = {n: rnd.randint(300, 2500) for n in users}
        cur = dict(bal0)
        events = sorted(((rnd.randint(1, 45) / 2.0, i) for i in range(14)), reverse=True)   # hours ago; ties likely
        specs = []
        for j, (hrs, i) in enumerate(events):
            for _ in range(20):
                f, t = rnd.sample(users, 2)
                if cur[f] >= 5:
                    a = rnd.randint(1, min(cur[f], 800))
                    cur[f] -= a
                    cur[t] += a
                    specs.append(("p_%03d" % j, f, t, a, hrs))
                    break
        return users, bal0, specs

    def run_seed(self, seed, ncorr):
        rnd = random.Random(seed * 7919 + 1)
        users, bal0, specs = self.build(seed)
        sc = Scenario(self, specs, openings={**{n: 0 for n in OPEN0}, **bal0})
        led, n0 = sc.led, sc.now0
        pids = list(led.pay)
        ok = {"201": 0, "insufficient_funds": 0, "historical_overdraft": 0, "stale_revision": 0}
        for step in range(ncorr):
            pid = rnd.choice(pids)
            p = led.pay[pid]
            frm = p["frm"]
            latest = led.latest(pid)
            exp_rev = latest[0]
            stale = rnd.random() < 0.08
            amount = rnd.choice([0, latest[1] + rnd.randint(1, 300), max(0, latest[1] - rnd.randint(1, 300)), rnd.randint(0, 900)])
            hrs = rnd.randint(1, 100) / 2.0
            eff = n0 - timedelta(hours=hrs) + timedelta(minutes=rnd.choice([0, 0, 1]))
            if amount == latest[1] and eff == latest[2]:
                amount += 1
            if stale:
                exp = ("409", "stale_revision")
            else:
                diff = amount - latest[1]
                debtor = frm if diff > 0 else p["to"]
                if diff != 0 and led.me(debtor)["available"] < abs(diff):
                    exp = ("409", "insufficient_funds")
                else:
                    ov = {pid: (exp_rev + 1, amount, eff, INF, "x")}
                    exp = ("409", "historical_overdraft") if led.violates(ov) else ("201", None)
            r = correct(frm[2:], pid, exp_rev + (1 if stale else 0), amount, eff, "fz%d" % step)
            self.assertEqual((str(r.status), r.code), (exp[0], exp[1]), "seed %d step %d pid %s amount %d eff -%sh: %r" % (seed, step, pid, amount, hrs, r))
            if r.status == 201:
                led.add_rev(pid, r.body["revision"], amount, dt(r.body["effective_at"]), dt(r.body["recorded_at"]), "x")
                sc.rec_times.append(dt(r.body["recorded_at"]))
                ok["201"] += 1
            else:
                ok[exp[1]] += 1
            if step % 2 == 1 or step == ncorr - 1:
                self.probe(sc, rnd, users)
        return ok

    def probe(self, sc, rnd, users):
        led, n0 = sc.led, sc.now0
        eff_times = sorted({m[0] for m in led.movements()})
        recs = sorted(sc.rec_times) or [now_utc()]

        def pick_T():
            c = rnd.choice(["eff", "effpm", "rand", "future", "none"])
            if c == "none":
                return None
            if c == "future":
                return now_utc() + timedelta(days=rnd.randint(1, 30))
            if c == "rand":
                return n0 - timedelta(minutes=rnd.randint(0, 3000))
            t = rnd.choice(eff_times)
            return t + (rnd.choice([-1, 1]) * US if c == "effpm" else timedelta(0))

        def pick_K():
            c = rnd.choice(["rec", "recm", "mid", "before", "future", "none"])
            if c == "none":
                return None
            if c == "future":
                return now_utc() + timedelta(days=2)
            if c == "before":
                return n0 - timedelta(hours=rnd.randint(1, 60))
            r = rnd.choice(recs)
            if c == "rec":
                return r
            if c == "recm":
                return r - US
            i = recs.index(r)
            return r + (recs[i + 1] - r) / 2 if i + 1 < len(recs) else r + timedelta(seconds=1)

        for _ in range(4):
            sc.me_check(rnd.choice(users), pick_T(), pick_K())
        for _ in range(3):
            u = rnd.choice(users)
            a, b = pick_T(), pick_T()
            frm, to = (a, b) if (a and b and a <= b) or not (a and b) else (b, a)
            sc.st_check(u, frm, to, pick_K(), rnd.choice([1, 2, 3, 200]), rnd.choice([0, 0, 1, 2]))
        T, K = pick_T(), pick_K()
        tot = sum(me_at(u, iso6(T) if T else None, iso6(K) if K else None).body["balance"] for u in OPEN0)
        self.assertEqual(tot, sum(led.opening.values()))  # money is conserved in every historical view

    def test_fuzz_seed_1(self):
        "[FZ-01] model-based fuzz (seed 1): random corrections predicted by an independent oracle; statuses, codes and every probed /me and /statement view agree"
        self.run_seed(1, 30)

    def test_fuzz_seed_2(self):
        "[FZ-02] model-based fuzz (seed 2)"
        self.run_seed(2, 30)

    def test_fuzz_seed_3(self):
        "[FZ-03] model-based fuzz (seed 3)"
        self.run_seed(3, 30)

    def test_fuzz_covers_every_outcome(self):
        "[FZ-04] across seeds the fuzz actually exercises 201, stale_revision, insufficient_funds and historical_overdraft"
        tot = {}
        for seed in (11, 12, 13, 14):
            for k_, v in self.run_seed(seed, 25).items():
                tot[k_] = tot.get(k_, 0) + v
        for key in ("201", "stale_revision", "insufficient_funds", "historical_overdraft"):
            self.assertGreater(tot[key], 0, tot)


if __name__ == "__main__":
    unittest.main()
