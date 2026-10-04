"""Concurrency, invariants under load, resource limits, 5xx-freedom."""
import json
import random
import threading
import time
import unittest

from lib import *


def T(f, t, a, **kw):
    d = {"from_handle": f, "to_handle": t, "amount": a}
    d.update(kw)
    return d


class Observer(threading.Thread):
    """Polls GET /me for the given users until stopped; records every balance seen and any failure."""

    def __init__(self, names):
        super().__init__(daemon=True)
        self.names, self.stop_evt, self.seen, self.bad = names, threading.Event(), {n: [] for n in names}, []
        self.toks = {n: tok(n) for n in names}

    def run(self):
        while not self.stop_evt.is_set():
            for n in self.names:
                r = safe_call("GET", "/me", token=self.toks[n])
                if r.status != 200:
                    self.bad.append(r)
                else:
                    self.seen[n].append(r.body["balance"])

    def finish(self):
        self.stop_evt.set()
        self.join(timeout=15)


class Concurrency(Base):
    def test_overdraw_race(self):
        "[CON-01] 30 concurrent different-key payments that together exceed the balance: exactly floor(balance/amount) succeed"
        t = tok("bob")
        targets = ["ada", "cy", "dee", "eve", "op", "op2"]
        rs = parallel([lambda i=i: call("POST", "/payments", {"to_handle": targets[i % 6], "amount": 1000}, token=t, key=k()) for i in range(30)])
        self.assertEqual(sum(r.status == 201 for r in rs), 2, rs)
        self.assertEqual(sum(r.status == 409 and r.code == "insufficient_funds" for r in rs), 28, rs)
        self.assertEqual(bal("bob"), 500)
        self.assertEqual(total(), BASE_TOTAL)

    def test_overdraw_exact_drain(self):
        "[CON-02] 50 concurrent payments of 200 from a wallet holding 10000: all succeed, wallet ends at exactly 0, never below"
        obs = Observer(["ada"])
        obs.start()
        t = tok("ada")
        rs = parallel([lambda: call("POST", "/payments", {"to_handle": "dee", "amount": 200}, token=t, key=k()) for _ in range(50)])
        obs.finish()
        self.assertEqual(sorted(r.status for r in rs), [201] * 50, [r for r in rs if r.status != 201][:3])
        self.assertEqual((bal("ada"), bal("dee")), (0, 10000))
        self.assertTrue(all(b >= 0 for b in obs.seen["ada"]), min(obs.seen["ada"] or [0]))
        self.assertEqual(obs.bad, [])
        # monotone drain: balance only ever decreases
        self.assertEqual(obs.seen["ada"], sorted(obs.seen["ada"], reverse=True))

    def test_mixed_load_conserves_and_never_negative(self):
        "[CON-03] 50 concurrent mixed payments/requests/pays/splits: conserved sum, no negative balance (observed continuously), no 5xx"
        names = ["ada", "bob", "cy", "eve", "dee"]
        obs = Observer(names)
        obs.start()
        rnd = random.Random(11)
        jobs = []
        rids = [req("bob", "ada", 300).body["request_id"] for _ in range(5)]
        for i in range(50):
            a, b = rnd.sample(names, 2)
            kind = i % 5
            if kind == 0 or kind == 1:
                jobs.append(lambda a=a, b=b: pay(a, b, rnd.randint(1, 3000)))
            elif kind == 2:
                jobs.append(lambda a=a, b=b: req(a, b, rnd.randint(1, 500)))
            elif kind == 3:
                jobs.append(lambda i=i: pay_req("ada", rids[i % 5]))
            else:
                jobs.append(lambda a=a: call("POST", "/splits", {"amount": 100, "participant_handles": ["bob", "cy"]}, token=tok(a), key=k()))
        rs = parallel(jobs)
        obs.finish()
        for r in rs:
            self.assertTrue(r.status in (200, 201, 409), r)
        self.assertEqual(obs.bad, [])
        for n, seen in obs.seen.items():
            self.assertTrue(all(b >= 0 for b in seen), (n, min(seen)))
        self.assertEqual(total(), BASE_TOTAL)

    def test_opposite_direction_transfers(self):
        "[CON-08] ada<->bob payments in both directions at once: no deadlock/timeout/5xx, sum conserved"
        ta, tb = tok("ada"), tok("bob")
        fns = []
        for i in range(25):
            fns.append(lambda: call("POST", "/payments", {"to_handle": "bob", "amount": 100}, token=ta, key=k()))
            fns.append(lambda: call("POST", "/payments", {"to_handle": "ada", "amount": 100}, token=tb, key=k()))
        rs = parallel(fns)
        self.assertTrue(all(r.status == 201 for r in rs), [r for r in rs if r.status != 201][:3])
        self.assertTrue(max(r.ms for r in rs) < 5000)
        self.assertEqual((bal("ada"), bal("bob")), (10000, 2500))

    def test_competing_settlements_and_payments(self):
        "[CON-09] settlements and direct payments competing for one wallet: total successes bounded by funds, wallet never negative"
        obs = Observer(["ada"])
        obs.start()
        top, ta = tok("op"), tok("ada")
        fns = []
        for i in range(10):
            fns.append(lambda: call("POST", "/settlements", {"transfers": [T("ada", "dee", 1000)]}, token=top, key=k()))
            fns.append(lambda: call("POST", "/payments", {"to_handle": "dee", "amount": 1000}, token=ta, key=k()))
        rs = parallel(fns)
        obs.finish()
        ok = sum(r.status == 201 for r in rs)
        self.assertEqual(ok, 10, rs)
        self.assertEqual(sum(r.status == 409 and r.code == "insufficient_funds" for r in rs), 10)
        self.assertEqual((bal("ada"), bal("dee")), (0, 10000))
        self.assertTrue(all(b >= 0 for b in obs.seen["ada"]))

    def test_settlement_is_atomic_to_observers(self):
        "[CON-10] a settlement's members become visible together: an observer never sees a partial batch"
        top = tok("op")
        sids = {}
        stop = threading.Event()
        partial = []
        ta = tok("ada")

        def watch():
            while not stop.is_set():
                r = safe_call("GET", "/activity?limit=200", token=ta)
                if r.status != 200:
                    partial.append(("status", r))
                    continue
                cnt = {}
                for p in r.body["payments"]:
                    if p.get("settlement_id"):
                        cnt[p["settlement_id"]] = cnt.get(p["settlement_id"], 0) + 1
                for sid, c in cnt.items():
                    if c != 8:
                        partial.append((sid, c))

        w = threading.Thread(target=watch, daemon=True)
        w.start()
        body = {"transfers": [T("ada", ["bob", "cy", "dee", "eve"][i % 4], 1) for i in range(8)]}
        rs = parallel([lambda: call("POST", "/settlements", body, token=top, key=k()) for _ in range(15)])
        time.sleep(0.2)
        stop.set()
        w.join(timeout=15)
        self.assertTrue(all(r.status == 201 for r in rs), rs)
        self.assertEqual(partial, [])
        self.assertEqual(bal("ada"), 10000 - 120)

    def test_passthrough_wallet_never_goes_negative_or_leaks(self):
        "[CON-11] a settlement whose listed order would overdraw a wallet never exposes that intermediate state"
        obs = Observer(["dee"])
        obs.start()
        top = tok("op")
        body = {"transfers": [T("dee", "cy", 500), T("ada", "dee", 500)]}
        rs = parallel([lambda: call("POST", "/settlements", body, token=top, key=k()) for _ in range(20)])
        obs.finish()
        self.assertTrue(all(r.status == 201 for r in rs), rs)
        self.assertEqual(set(obs.seen["dee"]), {0} if obs.seen["dee"] else set())
        self.assertEqual((bal("dee"), bal("ada"), bal("cy")), (0, 10000 - 10000, 1000 + 10000))

    def test_pay_vs_direct_payment_same_payer(self):
        "[CON-12] paying a request and a direct payment race for the same funds: only one fits"
        rid = req("bob", "ada", 7000).body["request_id"]
        rs = parallel([lambda: pay_req("ada", rid), lambda: pay("ada", "cy", 7000)])
        self.assertEqual(sorted(r.status for r in rs), [201, 409], rs)
        self.assertEqual(bal("ada"), 3000)
        self.assertEqual(total(), BASE_TOTAL)

    def test_split_and_pay_flood(self):
        "[CON-13] many concurrent requests against one payer: each pay moves its own request's money once"
        rids = [req("bob", "ada", 100 + i).body["request_id"] for i in range(20)]
        fns = []
        for rid in rids:
            fns += [lambda rid=rid: pay_req("ada", rid), lambda rid=rid: pay_req("ada", rid)]
        rs = parallel(fns)
        self.assertEqual(sum(r.status == 201 for r in rs), 20, rs)
        self.assertEqual(sum(r.status == 409 and r.code == "request_not_pending" for r in rs), 20, rs)
        self.assertEqual(bal("ada"), 10000 - sum(100 + i for i in range(20)))
        self.assertEqual(total(), BASE_TOTAL)

    def test_conservation_after_many_split_payments_concurrent(self):
        "[CON-14] splits paid concurrently: shares sum exactly, balances sum to seed"
        sp = call("POST", "/splits", {"amount": 1000, "participant_handles": ["bob", "cy", "eve"]}, token=tok("ada"), key=k()).body
        rs = parallel([lambda q=q: pay_req(q["payer_handle"], q["request_id"]) for q in sp["requests"]])
        self.assertTrue(all(r.status == 201 for r in rs), rs)
        self.assertEqual(sum(s["amount"] for s in sp["shares"]), 1000)
        self.assertEqual(total(), BASE_TOTAL)
        self.assertEqual(bal("ada"), 11000)

    def test_hashing_does_not_stall_wallet_ops(self):
        "[CON-15] a burst of signups/logins (password hashing) does not stall payments; everything inside the 5 s budget"
        fns = [lambda i=i: signup("burst%d.%s@example.com" % (i, uuid.uuid4().hex[:4])) for i in range(20)]
        fns += [lambda: call("POST", "/auth/login", {"email": "ada@example.com", "password": PW}) for _ in range(10)]
        ta = tok("ada")
        fns += [lambda: call("POST", "/payments", {"to_handle": "bob", "amount": 1}, token=ta, key=k()) for _ in range(20)]
        rs = parallel(fns)
        self.assertTrue(all(r.status in (200, 201) for r in rs), [r for r in rs if r.status not in (200, 201)][:3])
        pay_lat = [r.ms for r in rs[30:]]
        self.assertLess(max(pay_lat), 4500, pay_lat)
        self.assertEqual(bal("ada"), 9980)


class Limits(Base):
    def test_fifty_in_flight(self):
        "[LIM-01] 50 simultaneous requests (health, me, activity, requests) all succeed within 5 s"
        t = tok("ada")
        fns = []
        for i in range(50):
            p = ("/health", "/me", "/activity", "/requests")[i % 4]
            fns.append(lambda p=p: call("GET", p, token=t))
        rs = parallel(fns)
        self.assertEqual([r.status for r in rs], [200] * 50, [r for r in rs if r.status != 200][:3])

    def test_fifty_in_flight_writes_one_wallet(self):
        "[LIM-02] 50 concurrent writes on one wallet, each inside the 5 s timeout"
        t = tok("ada")
        rs = parallel([lambda: call("POST", "/payments", {"to_handle": "bob", "amount": 1}, token=t, key=k()) for _ in range(50)])
        self.assertEqual([r.status for r in rs], [201] * 50)
        self.assertEqual(bal("ada"), 9950)

    def test_sequential_latency(self):
        "[LIM-04] sequential payments are fast (mean < 500 ms, none above 2 s)"
        ms = [pay("ada", "bob", 1).ms for _ in range(40)]
        self.assertLess(sum(ms) / len(ms), 500)
        self.assertLess(max(ms), 2000)

    def test_large_state_export_import_within_budget(self):
        "[LIM-05] export and import of a few hundred records finish inside the 10 s control-call budget"
        t = tok("ada")
        parallel([lambda: call("POST", "/payments", {"to_handle": "bob", "amount": 1}, token=t, key=k()) for _ in range(40)])
        for _ in range(8):
            parallel([lambda: call("POST", "/payments", {"to_handle": "bob", "amount": 1}, token=t, key=k()) for _ in range(40)])
        e = call("GET", "/_test/export", timeout=10)
        self.assertEqual(e.status, 200)
        self.assertLess(e.ms, 9000)
        i = call("POST", "/_test/import", e.body, timeout=10)
        self.assertEqual(i.status, 204)
        self.assertLess(i.ms, 9000)
        self.assertEqual(bal("ada"), 10000 - 360)


class NoServerErrors(Base):
    ENDPOINTS = [("POST", "/payments", True), ("POST", "/requests", True), ("POST", "/splits", True), ("POST", "/settlements", True),
                 ("POST", "/requests/rq_1/pay", True), ("POST", "/requests/rq_1/decline", False), ("POST", "/requests/rq_1/cancel", False),
                 ("POST", "/auth/signup", False), ("POST", "/auth/login", False), ("POST", "/_test/import", False)]

    def test_garbage_bodies(self):
        "[ROB-01] hostile bodies on every write endpoint are 4xx with the standard error body, never 5xx"
        bodies = [b"", b"null", b"true", b"[]", b"[1,2]", b'"str"', b"12", b"{", b"{}", b'{"a":', b"\xff\xfe\x00", b"\x00" * 100,
                  b'{"amount": NaN}', b'{"amount": Infinity}', b'{"amount": 1e999}', b'{"amount": -1e999}', b'{"amount": 99999999999999999999999999999999999999}',
                  b'{"to_handle": "\xed\xa0\x80", "amount": 1}', b'{"to_handle": "\\ud800", "amount": 1}', b'{"note": "' + b"x" * 300000 + b'"}',
                  b"[" * 50000 + b"]" * 50000, b'{"a":' * 5000 + b"1" + b"}" * 5000,
                  b'{"transfers": [' + b",".join([b'{"from_handle":"ada","to_handle":"bob","amount":1}'] * 3000) + b"]}",
                  b'{"participant_handles": [' + b",".join([b'"x"'] * 100000) + b'], "amount": 5}',
                  b'{"to_handle":"bob","amount":1,"amount":2}', b'{"to_handle":"bob","amount":1}garbage', b"\xef\xbb\xbf{}"]
        for method, path, needs_key in self.ENDPOINTS:
            for raw in bodies:
                headers = {"Idempotency-Key": k()} if needs_key else None
                r = safe_call(method, path, raw=raw, token=tok("op"), headers=headers, timeout=5)
                self.assertTrue(r.error is None, (method, path, raw[:40], r.error))
                self.assertLess(r.status, 500, (method, path, raw[:60], r))
                if r.status >= 400:
                    self.assertIsInstance(r.body, dict, (method, path, raw[:40], r))
                    self.assertIsInstance(r.body.get("error", {}).get("code"), str, (method, path, raw[:40], r))
        self.assertEqual(total(), BASE_TOTAL)

    def test_hostile_paths_headers_queries(self):
        "[ROB-02] odd ids, paths, headers and query strings never produce 5xx"
        t = tok("ada")
        paths = ["/requests/%00/pay", "/requests/%ff/pay", "/requests/" + "a" * 5000 + "/pay", "/requests//pay", "/requests/../me/pay",
                 "/requests/rq_1/pay/extra", "/payments/x", "//me", "/me/", "/activity?limit=%ff", "/activity?limit=" + "9" * 5000,
                 "/requests?direction=%00", "/requests?status=" + "a" * 10000, "/activity?" + "a=b&" * 5000, "/%", "/%zz", "/me?x=%"]
        for p in paths:
            for m in ("GET", "POST"):
                r = safe_call(m, p, token=t, key=k() if m == "POST" else None, body={} if m == "POST" else None)
                if r.error is None:  # dropping an absurd request line is acceptable; a 5xx is not
                    self.assertLess(r.status, 500, (m, p, r))
                else:
                    self.assertEqual(call("GET", "/health").status, 200, "service died on " + p[:80])
        for hdr in ({"Idempotency-Key": "éè" .encode("latin-1").decode("latin-1")}, {"Authorization": "Bearer " + "x" * 20000},
                    {"Idempotency-Key": " "}, {"Idempotency-Key": "a b c"}, {"Content-Type": "text/plain"}, {"Content-Type": "application/json; charset=latin-1"},
                    {"Content-Type": ""}):
            r = safe_call("POST", "/payments", {"to_handle": "bob", "amount": 1}, token=t, headers=hdr)
            if r.error is None:  # a server may legitimately drop an absurd header; a 5xx is never ok
                self.assertLess(r.status, 500, (hdr, r))
        self.assertEqual(call("GET", "/health").status, 200)

    def test_five_xx_free_under_chaos(self):
        "[ROB-03] 50 concurrent valid, invalid and contradictory requests: every status is a documented 2xx/4xx, none 5xx or timeout"
        t = {n: tok(n) for n in ("ada", "bob", "cy", "op")}
        rid = req("bob", "ada", 100).body["request_id"]
        kk = k()
        fns = [
            lambda: call("POST", "/payments", {"to_handle": "bob", "amount": 10}, token=t["ada"], key=kk),
            lambda: call("POST", "/payments", {"to_handle": "bob", "amount": 11}, token=t["ada"], key=kk),
            lambda: call("POST", "/payments", {"to_handle": "nobody", "amount": 1}, token=t["ada"], key=k()),
            lambda: call("POST", "/payments", {"to_handle": "ada", "amount": 1}, token=t["ada"], key=k()),
            lambda: call("POST", "/payments", {"to_handle": "bob", "amount": "x"}, token=t["ada"], key=k()),
            lambda: call("POST", "/requests/%s/pay" % rid, {}, token=t["ada"], key=k()),
            lambda: call("POST", "/requests/%s/cancel" % rid, token=t["bob"]),
            lambda: call("POST", "/requests/%s/decline" % rid, token=t["ada"]),
            lambda: call("POST", "/requests/%s/pay" % rid, {}, token=t["cy"], key=k()),
            lambda: call("POST", "/splits", {"amount": 7, "participant_handles": ["bob", "cy", "ada"]}, token=t["ada"], key=k()),
            lambda: call("POST", "/settlements", {"transfers": [T("ada", "bob", 5), T("bob", "cy", 5)]}, token=t["op"], key=k()),
            lambda: call("POST", "/settlements", {"transfers": [T("ada", "ada", 5)]}, token=t["op"], key=k()),
            lambda: call("POST", "/settlements", {"transfers": [T("ada", "bob", 5)]}, token=t["cy"], key=k()),
            lambda: call("GET", "/activity?limit=0", token=t["ada"]),
            lambda: call("GET", "/requests?limit=5&offset=1", token=t["ada"]),
            lambda: call("POST", "/auth/signup", {"email": uniq_email(), "password": PW, "display_name": "c"}),
            lambda: call("GET", "/_test/export", timeout=10),
        ]
        fns = (fns * 3)[:50]
        rs = parallel(fns)
        for r in rs:
            self.assertTrue(r.error is None, r.error)
            self.assertLess(r.status, 500, r)
            if r.status >= 400:
                self.assertIsInstance(r.body.get("error"), dict, r)
        self.assertEqual(total(), BASE_TOTAL + 0)  # export did not alter anything; signups add users with balance 0
        self.assertTrue(all(bal(n) >= 0 for n in BASE_NAMES))


if __name__ == "__main__":
    unittest.main()
