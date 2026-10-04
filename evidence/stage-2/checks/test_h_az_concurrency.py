"""Stage 2: concurrency races, invariants at every read, expiry races."""
import json
import threading
import time
import unittest

from lib import *


def T(f, t, a, **kw):
    d = {"from_handle": f, "to_handle": t, "amount": a}
    d.update(kw)
    return d


class Watcher(threading.Thread):
    """Polls /me (and optionally /authorizations) for users; every read must satisfy the stage-2 invariants."""

    def __init__(self, names, lists=False):
        super().__init__(daemon=True)
        self.names, self.lists = names, lists
        self.stop_evt, self.bad, self.reads = threading.Event(), [], 0
        self.toks = {n: tok(n) for n in names}

    def run(self):
        while not self.stop_evt.is_set():
            for n in self.names:
                r = safe_call("GET", "/me", token=self.toks[n])
                if r.status != 200 or not isinstance(r.body, dict):
                    self.bad.append(("status", n, r))
                    continue
                m = r.body
                self.reads += 1
                if m["balance"] != m["total"] or m["available"] != m["total"] - m["held"] or m["available"] < 0 or m["held"] < 0 or m["total"] < 0:
                    self.bad.append(("invariant", n, m))
                if self.lists:
                    l = safe_call("GET", "/authorizations?limit=200", token=self.toks[n], headers={"Accept": "application/json"})
                    if l.status != 200:
                        self.bad.append(("list", n, l))
                        continue
                    for a in l.body["authorizations"]:
                        if a["captured_amount"] > a["amount"] or a["captured_amount"] + a["remaining_amount"] > a["amount"] or a["remaining_amount"] < 0:
                            self.bad.append(("auth-invariant", n, a))
                        if a["status"] != "open" and a["remaining_amount"] != 0:
                            self.bad.append(("closed-with-hold", n, a))

    def finish(self):
        self.stop_evt.set()
        self.join(timeout=15)


class Races(AzBase):
    def test_double_capture_distinct_keys(self):
        "[AC-01] many concurrent default captures with different keys: exactly one 201, rest 409 authorization_not_open; money once"
        aid = authorize("ada", "bob", 2000).body["authorization_id"]
        rs = parallel([lambda: capture("bob", aid) for _ in range(20)])
        self.assertEqual(sorted(r.status for r in rs), [201] + [409] * 19, rs)
        self.assertTrue(all(r.code == "authorization_not_open" for r in rs if r.status == 409), rs)
        self.assertEqual((bal("ada"), bal("bob")), (8000, 4500))
        self.assertEqual(len(az_get("ada", aid)["payment_ids"]), 1)
        self.me_inv("ada", held=0)

    def test_fresh_key_identical_captures(self):
        "[AC-02] concurrent identical captures with ONE fresh key: exactly one 201, the rest 200 with the same body, money once"
        aid = authorize("ada", "bob", 2000).body["authorization_id"]
        key = k()
        rs = parallel([lambda: capture("bob", aid, 1500, key=key) for _ in range(20)])
        self.assertEqual(sorted(r.status for r in rs), [200] * 19 + [201], rs)
        self.assertEqual(len({json.dumps(r.body, sort_keys=True) for r in rs}), 1)
        self.assertEqual((bal("ada"), bal("bob")), (8500, 4000))

    def test_fresh_key_identical_authorizations(self):
        "[AC-03] concurrent identical authorizations with one fresh key create one hold"
        key = k()
        t = tok("ada")
        rs = parallel([lambda: call("POST", "/authorizations", {"to_handle": "bob", "amount": 1000}, token=t, key=key) for _ in range(20)])
        self.assertEqual(sorted(r.status for r in rs), [200] * 19 + [201], rs)
        self.assertEqual(len({r.body["authorization_id"] for r in rs}), 1)
        self.me_inv("ada", held=1000)
        self.assertEqual(len(auths_of("ada")["authorizations"]), 1)

    def test_capture_vs_void(self):
        "[AC-04] concurrent capture and void: exactly one wins; money moves iff captured; hold released either way"
        for _ in range(10):
            reset()
            aid = authorize("ada", "bob", 2000).body["authorization_id"]
            cr, vr = parallel([lambda: capture("bob", aid), lambda: void("ada", aid)])
            self.assertTrue((cr.status, vr.status) in ((201, 409), (409, 200)), (cr, vr))
            row = az_get("ada", aid)
            if cr.status == 201:
                self.assertEqual((row["status"], bal("ada"), bal("bob")), ("captured", 8000, 4500))
            else:
                self.assertEqual((row["status"], bal("ada"), bal("bob")), ("voided", 10000, 2500))
                self.assertEqual(cr.code, "authorization_not_open")
            self.me_inv("ada", held=0)
            self.assertEqual(total(), BASE_TOTAL)

    def test_nonfinal_captures_never_exceed_authorized(self):
        "[AC-05] 30 concurrent nonfinal captures of 500 on a 2000 hold: exactly 4 succeed, cumulative == authorized, none 5xx"
        aid = authorize("ada", "bob", 2000).body["authorization_id"]
        rs = parallel([lambda: capture("bob", aid, 500, final=False) for _ in range(30)])
        self.assertEqual(sum(r.status == 201 for r in rs), 4, rs)
        for r in rs:
            if r.status != 201:
                self.assertIn(r.status, (409, 422), r)
                self.assertIn(r.code, ("authorization_not_open", "capture_exceeds_authorization"), r)
        row = az_get("ada", aid)
        self.az_shape(row, status="captured", captured=2000, remaining=0)
        self.assertEqual(len(row["payment_ids"]), 4)
        self.assertEqual((bal("ada"), bal("bob")), (8000, 4500))

    def test_mixed_amount_captures_bounded(self):
        "[AC-06] concurrent captures of mixed sizes: cumulative captured never exceeds the hold; sum of capture payments == captured_amount"
        aid = authorize("ada", "bob", 1000).body["authorization_id"]
        amounts = [100, 250, 300, 400, 150, 90, 60, 500] * 3
        rs = parallel([lambda a=a: capture("bob", aid, a, final=False) for a in amounts])
        ok = [r for r in rs if r.status == 201]
        row = az_get("ada", aid)
        self.assertEqual(sum(r.body["amount"] for r in ok), row["captured_amount"])
        self.assertLessEqual(row["captured_amount"], 1000)
        self.assertEqual(row["remaining_amount"], 1000 - row["captured_amount"] if row["status"] == "open" else 0)
        self.assertEqual(len(row["payment_ids"]), len(ok))
        self.assertTrue(all(r.status in (201, 409, 422) for r in rs), rs)
        self.me_inv("ada", total_=10000 - row["captured_amount"])
        self.assertEqual(total(), BASE_TOTAL)

    def test_overdraft_vs_holds(self):
        "[AC-07] 40 concurrent payments + authorizations of 1000 from a wallet holding 10000: at most 10 succeed in total; available never negative"
        w = Watcher(["ada"], lists=True)
        w.start()
        ta = tok("ada")
        fns = []
        for i in range(20):
            fns.append(lambda: call("POST", "/payments", {"to_handle": "dee", "amount": 1000}, token=ta, key=k()))
            fns.append(lambda: call("POST", "/authorizations", {"to_handle": "eve", "amount": 1000}, token=ta, key=k()))
        rs = parallel(fns)
        w.finish()
        self.assertEqual(w.bad, [])
        ok = [r for r in rs if r.status == 201]
        self.assertEqual(len(ok), 10, rs)
        self.assertTrue(all(r.status == 409 and r.code == "insufficient_funds" for r in rs if r.status != 201), rs)
        m = me("ada")
        paid = sum(1 for r in ok if r.body.get("payment_id")) * 1000  # payment bodies have a payment_id, authorization bodies null
        self.assertEqual(m["available"], 0)
        self.assertEqual(m["total"] + 0, 10000 - paid)
        self.assertEqual(m["held"], m["total"])
        self.assertEqual(total(), BASE_TOTAL)

    def test_capture_and_payment_race_for_reserved_money(self):
        "[AC-08] a capture and a direct payment race: capture always succeeds from the reserved money; payment only from available"
        reset(azfixture(users=[user("ada", 3000), user("bob", 0), user("cy", 0)], payments=[], requests=[]))
        aid = authorize("ada", "bob", 2000).body["authorization_id"]  # available 1000
        rs = parallel([lambda: capture("bob", aid), lambda: pay("ada", "cy", 1000), lambda: pay("ada", "cy", 1000)])
        self.assertEqual(rs[0].status, 201, rs)
        self.assertEqual(sorted(r.status for r in rs[1:]), [201, 409], rs)
        self.me_inv("ada", held=0, available=0, total_=0)

    def test_settlements_racing_holds(self):
        "[AC-09] settlements and authorizations racing for one wallet never overdraw it"
        w = Watcher(["ada"])
        w.start()
        top, ta = tok("op"), tok("ada")
        fns = []
        for i in range(10):
            fns.append(lambda: call("POST", "/settlements", {"transfers": [T("ada", "dee", 1000)]}, token=top, key=k()))
            fns.append(lambda: call("POST", "/authorizations", {"to_handle": "eve", "amount": 1000}, token=ta, key=k()))
        rs = parallel(fns)
        w.finish()
        self.assertEqual(w.bad, [])
        self.assertEqual(sum(r.status == 201 for r in rs), 10, rs)
        m = me("ada")
        self.assertEqual(m["available"], 0)
        self.assertEqual(total(), BASE_TOTAL)

    def test_request_pay_vs_authorization(self):
        "[AC-10] paying a request races an authorization for the same available funds: only one fits"
        rid = req("bob", "ada", 7000).body["request_id"]
        rs = parallel([lambda: pay_req("ada", rid), lambda: authorize("ada", "cy", 7000)])
        self.assertEqual(sorted(r.status for r in rs), [201, 409], rs)
        m = me("ada")
        self.assertEqual(m["available"], 3000)

    def test_invariants_at_every_read_under_load(self):
        "[AC-11] under 50-way mixed load (authorize/capture/void/pay) every /me and /authorizations read satisfies all invariants"
        w = Watcher(["ada", "bob", "cy", "eve"], lists=True)
        w.start()
        auth_ids = [authorize("ada", ["bob", "cy"][i % 2], 800).body["authorization_id"] for i in range(8)]
        ta, tb, tc, te = tok("ada"), tok("bob"), tok("cy"), tok("eve")
        fns = []
        for i in range(50):
            kind = i % 5
            aid = auth_ids[i % 8]
            holder = tb if i % 2 == 0 else tc
            if kind == 0:
                fns.append(lambda aid=aid, h=holder: call("POST", "/authorizations/%s/capture" % aid, {"amount": 300, "final": False}, token=h, key=k()))
            elif kind == 1:
                fns.append(lambda aid=aid: call("POST", "/authorizations/%s/void" % aid, token=ta))
            elif kind == 2:
                fns.append(lambda: call("POST", "/authorizations", {"to_handle": "bob", "amount": 400}, token=ta, key=k()))
            elif kind == 3:
                fns.append(lambda: call("POST", "/payments", {"to_handle": "ada", "amount": 250}, token=te, key=k()))
            else:
                fns.append(lambda: call("POST", "/payments", {"to_handle": "cy", "amount": 500}, token=ta, key=k()))
        rs = parallel(fns)
        w.finish()
        self.assertEqual(w.bad, [])
        self.assertTrue(w.reads > 5, w.reads)
        for r in rs:
            self.assertTrue(r.error is None and r.status < 500, r)
        self.assertEqual(total(), BASE_TOTAL)
        for n in BASE_NAMES:
            self.me_inv(n)
        # held == remaining of open outgoing, per user
        for n in ("ada", "bob", "cy"):
            open_out = auths_of(n, direction="outgoing", status="open", limit=200)["authorizations"]
            self.assertEqual(sum(a["remaining_amount"] for a in open_out), me(n)["held"])

    def test_no_5xx_on_new_endpoints_under_garbage(self):
        "[AC-12] hostile bodies/ids on the new endpoints: 4xx with the error body, never 5xx"
        aid = authorize("ada", "bob", 100).body["authorization_id"]
        bodies = [b"", b"null", b"[]", b"{", b'{"amount":1e999}', b'{"amount":NaN}', b'{"amount":' + b"9" * 500 + b"}", b'{"final":"x"}',
                  b"\xff\xfe", b"[" * 20000, b'{"to_handle":"\\ud800","amount":1}', b'{"amount":1}garbage']
        for path, tk in (("/authorizations", "ada"), ("/authorizations/%s/capture" % aid, "bob"), ("/authorizations/%s/void" % aid, "ada"),
                         ("/authorizations/%00/capture", "bob"), ("/authorizations/" + "a" * 4000 + "/void", "ada")):
            for raw in bodies:
                r = safe_call("POST", path, raw=raw, token=tok(tk), headers={"Idempotency-Key": k()})
                self.assertTrue(r.error is None, (path, raw[:30], r.error))
                self.assertLess(r.status, 500, (path, raw[:30], r))
                if r.status >= 400:
                    self.assertIsInstance(r.body, dict, (path, r))
                    self.assertIsInstance(r.body["error"]["code"], str)
        self.assertEqual(call("GET", "/health").status, 200)


class ExpiryRaces(AzBase):
    def test_capture_hammering_across_the_deadline(self):
        "[AC-13] captures fired continuously across the deadline: every outcome is consistent (money moves iff 201), then closed as expired"
        reset(azfixture(ttl=2))
        aid = authorize("ada", "bob", 1000).body["authorization_id"]
        stop = threading.Event()
        out = []

        def hammer():
            while not stop.is_set():
                out.append(capture("bob", aid, 10, final=False))
                time.sleep(0.05)

        th = threading.Thread(target=hammer, daemon=True)
        th.start()
        time.sleep(3.5)
        stop.set()
        th.join(timeout=10)
        ok = [r for r in out if r.status == 201]
        self.assertTrue(len(ok) >= 1, "no capture succeeded before the deadline")
        late = [r for r in out if r.status != 201]
        self.assertTrue(late, "hammer never reached the deadline")
        for r in late:
            self.assertIn(r.status, (409, 422), r)
        row = az_get("ada", aid)
        self.az_shape(row, status="expired")
        self.assertEqual(row["captured_amount"], sum(r.body["amount"] for r in ok))
        self.assertLessEqual(row["captured_amount"], 1000)
        self.me_inv("ada", held=0, total_=10000 - row["captured_amount"])
        self.assertEqual(total(), BASE_TOTAL)

    def test_void_vs_expiry(self):
        "[AC-14] void fired around the deadline: 200 (before) or 409 not_open (after); never 5xx; hold released exactly once"
        reset(azfixture(ttl=2))
        aid = authorize("ada", "bob", 1000).body["authorization_id"]
        time.sleep(1.6)
        rs = [void("ada", aid) for _ in range(6)]
        time.sleep(1.2)
        rs += [void("ada", aid) for _ in range(3)]
        for r in rs:
            self.assertIn(r.status, (200, 409), r)
        self.assertIn(az_get("ada", aid)["status"], ("voided", "expired"))
        self.me_inv("ada", held=0, available=10000)

    def test_expiry_invariants_with_watcher(self):
        "[AC-15] several short-lived holds expire while payments run: available never negative, sum conserved"
        reset(azfixture(ttl=2))
        w = Watcher(["ada", "bob"], lists=True)
        w.start()
        for i in range(5):
            authorize("ada", "bob", 1500)
            pay("bob", "ada", 10)
            time.sleep(0.3)
        time.sleep(3.0)
        w.finish()
        self.assertEqual(w.bad, [])
        self.me_inv("ada", held=0)
        self.assertEqual(total(), BASE_TOTAL)


if __name__ == "__main__":
    unittest.main()
