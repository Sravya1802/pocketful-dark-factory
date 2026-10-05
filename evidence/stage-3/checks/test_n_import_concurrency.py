"""Stage 3: importing stage-1 / stage-2 / stage-3 exports; concurrency; eight write paths; robustness."""
import json
import os
import threading
import time
import unittest

from lib import *

STAGE1_URL = os.environ.get("STAGE1_URL")
STAGE2_URL = os.environ.get("STAGE2_URL")


def T(f, t, a, **kw):
    d = {"from_handle": f, "to_handle": t, "amount": a}
    d.update(kw)
    return d


def sig(e):
    return (e["payment"]["payment_id"], e["delta"], e["balance_after"], e["revision"], dt(e["effective_at"]), dt(e["recorded_at"]), e["payment"]["amount"])


class Roundtrip(Base):
    def test_stage3_export_import_keeps_the_ledger(self):
        "[IM-01] stage-3 export/import preserves revisions, recorded times, statements, historical views, correction replays and tokens"
        fx, led, n0 = hist(H_SPECS)
        reset(fx)
        keys = []
        for amt, hrs in ((300, 10), (100, 9)):
            key = k()
            r = correct("ada", "p_001", 1 + len(keys), amt, n0 - timedelta(hours=hrs), "c%d" % amt, key=key)
            self.assertEqual(r.status, 201, r)
            keys.append((key, amt, hrs, r.body))
        time.sleep(0.05)
        a = authorize("ada", "bob", 500).body["authorization_id"]
        capture("bob", a, 100, final=False)
        names = ["ada", "bob", "cy", "dee", "eve"]
        probe_times = [iso6(n0 - timedelta(hours=h)) for h in (11, 9.5, 8.5, 7, 5, 3)] + [None]

        def snap():
            out = {}
            for n in names:
                out[n] = (statement(n, limit=200).body["entries"], me(n))
                for t in probe_times:
                    out[(n, t)] = me_at(n, t).body if t else None
            out["rev"] = revisions("ada", "p_001").body
            out["auth"] = auths_of("ada", limit=200)["authorizations"]
            return out

        before = snap()
        exp = call("GET", "/_test/export", timeout=10)
        self.assertEqual(exp.status, 200)
        pay("ada", "bob", 5)
        correct("ada", "p_001", 3, 10, n0 - timedelta(hours=8))
        reset(fx)
        self.assertEqual(call("POST", "/_test/import", exp.body, timeout=10).status, 204)
        after = snap()
        strip = lambda o: json.loads(json.dumps({str(kk): vv for kk, vv in o.items()}, default=str))
        self.assertEqual(strip(after), strip(before))
        for key, amt, hrs, body in keys:
            r = call("POST", "/payments/p_001/corrections", {"expected_revision": 1 + keys.index((key, amt, hrs, body)), "amount": amt,
                                                              "effective_at": iso6(n0 - timedelta(hours=hrs)), "reason": "c%d" % amt}, token=tok("ada"), key=key)
            self.assertEqual((r.status, r.body), (200, body))
        self.assertEqual(sum(me_at(n, None).body["balance"] for n in OPEN0), H_TOTAL)


@unittest.skipUnless(STAGE1_URL, "set STAGE1_URL to a running STAGE-1 service")
class ImportStage1(unittest.TestCase):
    def s1(self, method, path, body=None, token=None, key=None):
        return call(method, path, body, token=token, key=key, base=STAGE1_URL, timeout=10)

    def setUp(self):
        self.assertEqual(call("POST", "/_test/reset", base_fixture(), timeout=10, base=STAGE1_URL).status, 204)
        self.t1 = {n: self.s1("POST", "/auth/login", {"email": n + "@example.com", "password": PW}).body["token"] for n in ("ada", "bob", "cy", "op")}

    def test_stage1_export_into_stage3(self):
        "[IM-10] a stage-1 export imports: balances, tokens, opening balances (= ending - net effect of ALL imported payments), statements, revisions, corrections, replays"
        t = self.t1
        time.sleep(1.1)
        pk = k()
        paid = self.s1("POST", "/payments", {"to_handle": "bob", "amount": 1234, "note": "x"}, token=t["ada"], key=pk)
        st = self.s1("POST", "/settlements", {"transfers": [T("ada", "cy", 50), T("bob", "cy", 20)]}, token=t["op"], key=k())
        pend = self.s1("POST", "/requests", {"payer_handle": "ada", "amount": 400}, token=t["bob"], key=k())
        self.assertEqual((paid.status, st.status, pend.status), (201, 201, 201))
        cur = {n: self.s1("GET", "/me", token=t[n]).body["balance"] for n in ("ada", "bob", "cy")}
        feed1 = self.s1("GET", "/activity?limit=200", token=t["ada"]).body["payments"]
        exp = self.s1("GET", "/_test/export")
        self.assertEqual(exp.status, 200)
        reset(base_fixture(users=[user("zed", 5)], payments=[], requests=[]))
        r = call("POST", "/_test/import", exp.body, timeout=10)
        self.assertEqual(r.status, 204, r)
        for n in ("ada", "bob", "cy"):
            m = call("GET", "/me", token=t[n]).body
            self.assertEqual(m["balance"], cur[n], n)
            self.assertEqual((m["total"], m["available"], m["held"]), (cur[n], cur[n], 0))
        epoch = "1970-01-01T00:00:00+00:00"
        opening = {"ada": 10800, "bob": 2200, "cy": 500}      # fixture ending balance - seeded net effect
        for n, o in opening.items():
            self.assertEqual(call("GET", "/me?" + q(as_of=epoch), token=t[n]).body["balance"], o, n)
        # statement closes its arithmetic; imported payments are revision 1 with effective = recorded = created_at
        for n in ("ada", "bob", "cy"):
            b = call("GET", "/statement?limit=200", token=t[n]).body
            self.assertEqual(b["opening_balance"], opening[n])
            self.assertEqual(b["closing_balance"], cur[n])
            self.assertEqual(b["opening_balance"] + sum(e["delta"] for e in b["entries"]), b["closing_balance"])
            for e in b["entries"]:
                self.assertEqual(e["revision"], 1)
                self.assertEqual(dt(e["effective_at"]), dt(e["recorded_at"]))
                self.assertEqual(dt(e["effective_at"]), dt(e["payment"]["created_at"]))
        ids = {e["payment"]["payment_id"] for e in call("GET", "/statement?limit=200", token=t["ada"]).body["entries"]}
        self.assertTrue({"p_1", "p_2", paid.body["payment_id"]} <= ids)
        self.assertEqual(call("GET", "/activity?limit=200", token=t["ada"]).body["payments"][0]["payment_id"], feed1[0]["payment_id"])
        # settlement members: effective = recorded = committed_at; immutable
        for m in st.body["payments"]:
            who = "ada" if m["from_handle"] == "ada" else "bob"
            rv = call("GET", "/payments/%s/revisions" % m["payment_id"], token=t[who]).body["revisions"]
            self.assertEqual((len(rv), dt(rv[0]["effective_at"]), dt(rv[0]["recorded_at"])), (1, dt(st.body["committed_at"]), dt(st.body["committed_at"])))
            cr = call("POST", "/payments/%s/corrections" % m["payment_id"], {"expected_revision": 1, "amount": 1, "effective_at": iso6(ago(seconds=1)), "reason": "x"},
                      token=t[who], key=k())
            self.assertEqual((cr.status, cr.code), (422, "linked_payment_immutable"))
        # an imported API payment is correctable by its sender; seeded payments too
        c = call("POST", "/payments/%s/corrections" % paid.body["payment_id"], {"expected_revision": 1, "amount": 200, "effective_at": iso6(ago(seconds=1)), "reason": "fix"},
                 token=t["ada"], key=k())
        self.assertEqual(c.status, 201, c)
        self.assertEqual(call("GET", "/me", token=t["ada"]).body["balance"], cur["ada"] + 1034)
        # replays of stage-1 keys
        again = call("POST", "/payments", {"to_handle": "bob", "amount": 1234, "note": "x"}, token=t["ada"], key=pk)
        self.assertEqual((again.status, again.body["payment_id"]), (200, paid.body["payment_id"]))
        self.assertEqual(again.body["amount"], 1234)
        # pending request still payable
        pr = call("POST", "/requests/%s/pay" % pend.body["request_id"], {}, token=t["ada"], key=k())
        self.assertEqual(pr.status, 201, pr)
        total_ = sum(call("GET", "/me?" + q(as_of=epoch), token=call("POST", "/auth/login", {"email": n + "@example.com", "password": PW}).body["token"]).body["balance"]
                     for n in ("ada", "bob", "cy", "dee", "eve", "op", "op2"))
        self.assertEqual(total_, BASE_TOTAL)

    def test_native_and_imported_openings_agree(self):
        "[IM-11] after importing, the opening balances equal those a native stage-3 reset with the same fixture reports"
        exp = self.s1("GET", "/_test/export")
        reset()
        native = {n: me_at(n, "1970-01-01T00:00:00+00:00").body["balance"] for n in ("ada", "bob", "cy", "dee", "eve")}
        reset(base_fixture(users=[user("zed", 5)], payments=[], requests=[]))
        self.assertEqual(call("POST", "/_test/import", exp.body, timeout=10).status, 204)
        imported = {n: call("GET", "/me?" + q(as_of="1970-01-01T00:00:00+00:00"),
                            token=call("POST", "/auth/login", {"email": n + "@example.com", "password": PW}).body["token"]).body["balance"]
                    for n in ("ada", "bob", "cy", "dee", "eve")}
        self.assertEqual(imported, native)


@unittest.skipUnless(STAGE2_URL, "set STAGE2_URL to a running STAGE-2 service")
class ImportStage2(unittest.TestCase):
    def s2(self, method, path, body=None, token=None, key=None):
        return call(method, path, body, token=token, key=key, base=STAGE2_URL, timeout=10, headers={"Accept": "application/json"})

    def test_stage2_export_with_holds_and_captures(self):
        "[IM-20] a stage-2 export imports: open holds keep holding, captures appear once with links, closed holds hold nothing, closed_at exposed, history is consistent"
        self.assertEqual(call("POST", "/_test/reset", azfixture([seed_auth("a_1", "ada", "cy", 1000)]), timeout=10, base=STAGE2_URL).status, 204)
        t = {n: self.s2("POST", "/auth/login", {"email": n + "@example.com", "password": PW}).body["token"] for n in ("ada", "bob", "cy", "op")}
        time.sleep(2.2)   # the seeded hold (created at reset) must be clearly older than the next one
        a = self.s2("POST", "/authorizations", {"to_handle": "bob", "amount": 2000, "note": "open"}, token=t["ada"], key=k()).body
        b = self.s2("POST", "/authorizations", {"to_handle": "bob", "amount": 1500, "note": "cap"}, token=t["ada"], key=k()).body
        time.sleep(1.1)
        c1 = self.s2("POST", "/authorizations/%s/capture" % b["authorization_id"], {"amount": 500, "final": False}, token=t["bob"], key=k()).body
        time.sleep(1.1)
        c2 = self.s2("POST", "/authorizations/%s/capture" % b["authorization_id"], {"amount": 400}, token=t["bob"], key=k())
        v = self.s2("POST", "/authorizations", {"to_handle": "cy", "amount": 300}, token=t["ada"], key=k()).body
        self.s2("POST", "/authorizations/%s/void" % v["authorization_id"], token=t["ada"])
        pk = k()
        pp = self.s2("POST", "/payments", {"to_handle": "bob", "amount": 77}, token=t["ada"], key=pk).body
        cur = {n: self.s2("GET", "/me", token=t[n]).body for n in ("ada", "bob", "cy")}
        exp = call("GET", "/_test/export", base=STAGE2_URL, timeout=10)
        self.assertEqual(exp.status, 200)
        reset(base_fixture(users=[user("zed", 5)], payments=[], requests=[]))
        self.assertEqual(call("POST", "/_test/import", exp.body, timeout=10).status, 204)
        # current money fields
        for n in ("ada", "bob", "cy"):
            m = call("GET", "/me", token=t[n]).body
            self.assertEqual({f: m[f] for f in ("balance", "total", "available", "held")}, {f: cur[n][f] for f in ("balance", "total", "available", "held")}, n)
        ada = call("GET", "/me", token=t["ada"]).body
        self.assertEqual(ada["held"], 1000 + 2000)    # seeded a_1 + open hold
        # authorizations keep their shape and expose closed_at
        rows = {x["authorization_id"]: x for x in call("GET", "/authorizations?limit=200", token=t["ada"], headers={"Accept": "application/json"}).body["authorizations"]}
        self.assertIsNone(rows[a["authorization_id"]]["closed_at"])
        self.assertIsNone(rows["a_1"]["closed_at"])
        for x in rows.values():
            self.assertIn("closed_at", x)
        # historical: before the open hold existed
        created = dt(a["created_at"])
        m = call("GET", "/me?" + q(as_of=iso6(created - timedelta(seconds=1))), token=t["ada"]).body
        self.assertEqual(m["held"], 1000)             # only the seeded hold (created at reset in stage 2, earlier)
        self.assertEqual(m["available"], m["total"] - m["held"])
        m = call("GET", "/me?" + q(as_of=iso6(dt(a["created_at"]) + timedelta(seconds=0.2))), token=t["ada"]).body
        self.assertGreaterEqual(m["held"], 2000)
        # statements: captures once with links, holds never as entries
        for n in ("ada", "bob"):
            b_ = call("GET", "/statement?limit=200", token=t[n]).body
            ents = b_["entries"]
            caps = [e for e in ents if e["payment"].get("authorization_id") == b["authorization_id"]]
            self.assertEqual(sorted(e["payment"]["payment_id"] for e in caps), sorted([c1["payment_id"], c2.body["payment_id"]]))
            self.assertEqual(len(ents), len({e["payment"]["payment_id"] for e in ents}))
            self.assertEqual(b_["opening_balance"] + sum(e["delta"] for e in ents), b_["closing_balance"])
            self.assertEqual(b_["closing_balance"], cur[n]["balance"])
            self.assertEqual(len([e for e in ents if e["payment"]["note"] in ("open",)]), 0)
        # captures are immutable linked payments
        cr = call("POST", "/payments/%s/corrections" % c1["payment_id"], {"expected_revision": 1, "amount": 1, "effective_at": iso6(ago(seconds=1)), "reason": "x"},
                  token=t["ada"], key=k())
        self.assertEqual((cr.status, cr.code), (422, "linked_payment_immutable"))
        # the plain payment is correctable, replay of its original key still 200
        ok = call("POST", "/payments/%s/corrections" % pp["payment_id"], {"expected_revision": 1, "amount": 7, "effective_at": iso6(ago(seconds=1)), "reason": "fix"},
                  token=t["ada"], key=k())
        self.assertEqual(ok.status, 201, ok)
        rep = call("POST", "/payments", {"to_handle": "bob", "amount": 77}, token=t["ada"], key=pk)
        self.assertEqual((rep.status, rep.body["payment_id"]), (200, pp["payment_id"]))
        # an imported open hold is fully alive: capture it, then history shows the release
        cap = call("POST", "/authorizations/%s/capture" % a["authorization_id"], {"amount": 2000}, token=t["bob"], key=k())
        self.assertEqual(cap.status, 201, cap)
        row = [x for x in call("GET", "/authorizations?limit=200", token=t["ada"], headers={"Accept": "application/json"}).body["authorizations"]
               if x["authorization_id"] == a["authorization_id"]][0]
        self.assertEqual(row["status"], "captured")
        self.assertIsNotNone(row["closed_at"])
        self.assertEqual(call("GET", "/me", token=t["ada"]).body["held"], 1000)


class Concurrency(Base):
    def test_same_expected_revision_cannot_both_succeed(self):
        "[CC-01] N concurrent corrections with the same expected_revision (distinct keys, distinct amounts): exactly one 201, the rest stale_revision; money moves once"
        pid = pay("ada", "bob", 1000).body["payment_id"]
        t = tok("ada")
        e = iso6(ago(seconds=2))
        rs = parallel([lambda i=i: call("POST", "/payments/%s/corrections" % pid, {"expected_revision": 1, "amount": 100 + i, "effective_at": e, "reason": "c%d" % i},
                                        token=t, key=k()) for i in range(20)])
        self.assertEqual(sum(r.status == 201 for r in rs), 1, rs)
        self.assertTrue(all(r.status == 409 and r.code == "stale_revision" for r in rs if r.status != 201), rs)
        won = [r for r in rs if r.status == 201][0].body
        self.assertEqual(len(revisions("ada", pid).body["revisions"]), 2)
        self.assertEqual(bal("ada"), 10000 - won["amount"])
        self.assertEqual(bal("bob"), 2500 + won["amount"])

    def test_identical_requests_with_a_fresh_key(self):
        "[CC-02] N concurrent identical corrections with one fresh key: exactly one 201, the others 200 with the same body"
        pid = pay("ada", "bob", 1000).body["payment_id"]
        t, key = tok("ada"), k()
        body = {"expected_revision": 1, "amount": 250, "effective_at": iso6(ago(seconds=2)), "reason": "same"}
        rs = parallel([lambda: call("POST", "/payments/%s/corrections" % pid, body, token=t, key=key) for _ in range(20)])
        self.assertEqual(sorted(r.status for r in rs), [200] * 19 + [201], rs)
        self.assertEqual(len({json.dumps(r.body, sort_keys=True) for r in rs}), 1)
        self.assertEqual(len(revisions("ada", pid).body["revisions"]), 2)
        self.assertEqual(bal("ada"), 9750)

    def test_correction_chain_races(self):
        "[CC-03] concurrent corrections on many payments: every success is a consistent revision, balances equal the sum of latest amounts, nothing negative, total conserved"
        fx, led, n0 = hist(H_SPECS)
        reset(fx)
        pids = []
        for i in range(6):
            pids.append(pay("ada", "bob", 100 + i).body["payment_id"])
            pids.append(pay("bob", "ada", 50 + i).body["payment_id"])
        owner = {p: ("ada" if i % 2 == 0 else "bob") for i, p in enumerate(pids)}
        e = iso6(ago(seconds=2))
        fns = []
        for rnd_ in range(3):
            for i, p in enumerate(pids):
                fns.append(lambda p=p, i=i, rnd_=rnd_: call("POST", "/payments/%s/corrections" % p, {"expected_revision": 1 + rnd_, "amount": (i * 37 + rnd_ * 11) % 400, "effective_at": e,
                                                                                                   "reason": "r"}, token=tok(owner[p]), key=k()))
        results = []
        for batch in range(0, len(fns), 24):
            results += parallel(fns[batch:batch + 24])
        for r in results:
            self.assertTrue(r.error is None and r.status in (201, 409), r)
            if r.status == 409:
                self.assertIn(r.code, ("stale_revision", "insufficient_funds", "historical_overdraft"), r)
        names = ["ada", "bob", "cy", "dee", "eve", "op", "op2"]
        bs = [bal(n) for n in names]
        self.assertEqual(sum(bs), H_TOTAL)
        self.assertTrue(all(b >= 0 for b in bs), bs)
        # balances follow the latest amounts of every payment (ledger consistency)
        net = {"ada": 0, "bob": 0}
        for p in pids:
            latest = revisions(owner[p], p).body["revisions"][-1]["amount"]
            first = revisions(owner[p], p).body["revisions"][0]["amount"]
            f, t = owner[p], ("bob" if owner[p] == "ada" else "ada")
            net[f] -= latest - first
            net[t] += latest - first
        self.assertEqual(bal("ada"), 8300 + net["ada"])
        self.assertEqual(bal("bob"), 2600 + net["bob"])
        b_, ents = all_statement("ada")
        self.assertEqual((b_["opening_balance"] + sum(e["delta"] for e in ents), b_["closing_balance"]), (b_["closing_balance"], bal("ada")))

    def test_correction_versus_receiver_spending(self):
        "[CC-04] a decrease racing the receiver spending the money: either the correction wins or the payment does; invariants hold and the outcomes are consistent"
        for _ in range(6):
            reset()
            pid = pay("ada", "dee", 800).body["payment_id"]
            rs = parallel([lambda: correct("ada", pid, 1, 0, ago(seconds=2)), lambda: pay("dee", "cy", 800)])
            cr, sp = rs
            self.assertTrue(cr.status in (201, 409) and sp.status in (201, 409), rs)
            ds = [bal(n) for n in ("ada", "bob", "cy", "dee", "eve", "op", "op2")]
            self.assertEqual(sum(ds), BASE_TOTAL)
            self.assertTrue(all(x >= 0 for x in ds), ds)
            if cr.status == 201:
                self.assertEqual(bal("ada"), 10000)
                self.assertEqual(sp.status, 409)   # reversed first: dee had nothing left to spend
            else:
                self.assertEqual((cr.code, sp.status), ("insufficient_funds", 201))
                self.assertEqual(bal("dee"), 0)

    def test_eight_write_paths(self):
        "[CC-05] corrections are the eighth idempotent write path: missing key 400, and one key string used on all eight paths is a first use on each"
        pid = pay("ada", "bob", 100).body["payment_id"]
        aid = authorize("ada", "cy", 10).body["authorization_id"]
        t, tb, top, tc = tok("ada"), tok("bob"), tok("op"), tok("cy")
        body = {"expected_revision": 1, "amount": 50, "effective_at": iso6(ago(seconds=2)), "reason": "r"}
        self.err(call("POST", "/payments/%s/corrections" % pid, body, token=t), 400, "missing_idempotency_key")
        key = k()
        rs = [call("POST", "/payments", {"to_handle": "bob", "amount": 5}, token=t, key=key),
              call("POST", "/requests", {"payer_handle": "bob", "amount": 5}, token=t, key=key),
              call("POST", "/splits", {"amount": 9, "participant_handles": ["bob"]}, token=t, key=key),
              call("POST", "/authorizations", {"to_handle": "bob", "amount": 5}, token=t, key=key),
              call("POST", "/settlements", {"transfers": [T("ada", "bob", 1)]}, token=top, key=key),
              call("POST", "/authorizations/%s/capture" % aid, {}, token=tc, key=key),
              call("POST", "/payments/%s/corrections" % pid, body, token=t, key=key)]
        self.assertEqual([r.status for r in rs], [201] * 7, rs)
        rid = req("bob", "ada", 7).body["request_id"]
        pr = call("POST", "/requests/%s/pay" % rid, {}, token=t, key=key)
        self.assertEqual(pr.status, 201, pr)
        again = call("POST", "/payments/%s/corrections" % pid, body, token=t, key=key)
        self.assertEqual((again.status, again.body), (200, rs[6].body))

    def test_many_snapshots_stay_valid(self):
        "[CC-06] snapshots live until reset: hundreds of statement reads later, the very first token still pages the same frozen result"
        first = statement("ada", limit=2)
        tokn, frozen = first.body["snapshot"], statement("ada", snapshot=first.body["snapshot"], limit=200).body
        for i in range(300):
            r = statement("ada" if i % 2 else "bob", limit=1)
            self.assertEqual(r.status, 200)
        pay("ada", "bob", 1)
        again = statement("ada", snapshot=tokn, limit=200)
        self.assertEqual(again.status, 200)
        self.assertEqual(again.body["entries"], frozen["entries"])
        self.assertEqual(again.body["closing_balance"], frozen["closing_balance"])

    def test_snapshot_reads_are_cheap_under_load(self):
        "[CC-07] 50 concurrent first-time statement reads and snapshot pages all succeed inside the timeout"
        for i in range(30):
            pay("ada", "bob", 1)
        t = tok("ada")
        rs = parallel([lambda: call("GET", "/statement?limit=10", token=t) for _ in range(25)])
        self.assertTrue(all(r.status == 200 for r in rs), rs[:2])
        toks = [r.body["snapshot"] for r in rs]
        self.assertEqual(len(set(toks)), len(toks))
        rs2 = parallel([lambda s=s: call("GET", "/statement?snapshot=%s&limit=5&offset=3" % s, token=t) for s in toks])
        self.assertTrue(all(r.status == 200 and len(r.body["entries"]) == 5 for r in rs2), rs2[:2])

    def test_invariants_during_a_mixed_storm(self):
        "[CC-08] payments, corrections, authorizations, captures, statements and historical reads at once: no 5xx, sums conserved at every sampled historical view"
        stop, bad = threading.Event(), []
        t = {n: tok(n) for n in ("ada", "bob", "cy", "eve")}
        pids = [pay("ada", "bob", 200 + i).body["payment_id"] for i in range(8)]

        def writer(i):
            j = 0
            while not stop.is_set():
                pr = safe_call("POST", "/payments", {"to_handle": "cy", "amount": 1 + (j % 5)}, token=t["bob"], key=k())
                if pr.status >= 500 or pr.error:
                    bad.append(pr)
                c = safe_call("POST", "/payments/%s/corrections" % pids[(i + j) % 8], {"expected_revision": 1 + (j // 8), "amount": (j * 13) % 150, "effective_at": iso6(ago(seconds=1)),
                                                                                     "reason": "s"}, token=t["ada"], headers={"Idempotency-Key": k()})
                if c.status >= 500 or c.error:
                    bad.append(c)
                a = safe_call("POST", "/authorizations", {"to_handle": "eve", "amount": 20}, token=t["cy"], key=k())
                if a.status == 201:
                    safe_call("POST", "/authorizations/%s/capture" % a.body["authorization_id"], {"amount": 7}, token=t["eve"], key=k())
                j += 1

        ws = [threading.Thread(target=writer, args=(i,), daemon=True) for i in range(5)]
        for w in ws:
            w.start()
        for _ in range(12):
            far = iso6(now_utc() + timedelta(days=1))
            past = iso6(now_utc() - timedelta(minutes=30))
            for view in (None, far, past):
                for n in ("ada", "bob", "cy", "dee", "eve", "op", "op2"):
                    r = safe_call("GET", "/me" + ("?" + q(as_of=view) if view else ""), token=tok(n))
                    if r.status != 200:
                        bad.append(r)
                        continue
                    m = r.body
                    if m["balance"] != m["total"] or m["available"] != m["total"] - m["held"]:
                        bad.append(("shape", m))
                # reads are not one atomic snapshot across users while writers run; only per-user shape is asserted here
            r = safe_call("GET", "/statement?limit=50", token=t["ada"])
            if r.status != 200:
                bad.append(r)
        stop.set()
        for w in ws:
            w.join(timeout=15)
        self.assertEqual(bad, [])
        self.assertEqual(sum(me_at(n, None).body["balance"] for n in OPEN0), BASE_TOTAL)
        self.assertEqual(sum(me_at(n, iso6(now_utc() - timedelta(minutes=30))).body["balance"] for n in OPEN0), BASE_TOTAL)


class Robustness(Base):
    def test_no_5xx_on_new_endpoints(self):
        "[RB-01] hostile inputs on corrections, revisions, statement and /me temporal parameters are 4xx with the error body, never 5xx"
        pid = pay("ada", "bob", 10).body["payment_id"]
        bodies = [b"", b"null", b"[]", b"{", b'{"expected_revision":1e999,"amount":1,"effective_at":"x","reason":"r"}', b"\xff\xfe", b"[" * 20000,
                  b'{"expected_revision":1,"amount":1,"effective_at":"2026-01-01T00:00:00Z","reason":"' + b"x" * 300000 + b'"}',
                  b'{"expected_revision":99999999999999999999999,"amount":1,"effective_at":"2026-01-01T00:00:00Z","reason":"r"}',
                  b'{"expected_revision":1,"amount":NaN,"effective_at":"2026-01-01T00:00:00Z","reason":"r"}']
        for path in ("/payments/%s/corrections" % pid, "/payments/%00/corrections", "/payments/" + "a" * 4000 + "/corrections"):
            for raw in bodies:
                r = safe_call("POST", path, raw=raw, token=tok("ada"), headers={"Idempotency-Key": k()})
                self.assertTrue(r.error is None, (path[:30], raw[:30], r.error))
                self.assertLess(r.status, 500, (path[:30], raw[:30], r))
                if r.status >= 400:
                    self.assertIsInstance(r.body["error"]["code"], str)
        for qs in ("as_of=%00", "as_of=" + "9" * 3000, "known_at=%ff", "as_of=0000-00-00T00:00:00Z", "as_of=9999-12-31T23:59:59Z", "as_of=0001-01-01T00:00:00Z",
                   "as_of=2026-01-01T00:00:00%2B23:59", "as_of=2026-01-01T00:00:00.123456789123Z", "as_of=292277026596-12-04T15:30:07Z"):
            for path in ("/me", "/statement"):
                r = safe_call("GET", path + "?" + qs, token=tok("ada"))
                self.assertTrue(r.error is None, (path, qs[:30], r.error))
                self.assertLess(r.status, 500, (path, qs[:30], r))
        for qs in ("from=9999-12-31T23:59:59Z&to=0001-01-01T00:00:00Z", "from=0001-01-01T00:00:00Z", "to=9999-12-31T23:59:59Z&limit=200&offset=99999999999999999999"):
            r = safe_call("GET", "/statement?" + qs, token=tok("ada"))
            self.assertLess(r.status, 500, (qs, r))
        for who in ("p_none", "%00", "x" * 5000):
            r = safe_call("GET", "/payments/%s/revisions" % who, token=tok("ada"))
            self.assertLess(r.status, 500, r)
        self.assertEqual(call("GET", "/health").status, 200)

    def test_extreme_instants_and_amounts_stay_exact(self):
        "[RB-02] extreme (but valid) instants are accepted or rejected cleanly; balances stay exact integers"
        for iso in ("0001-01-01T00:00:00+00:00", "9999-12-31T23:59:59+00:00", "1970-01-01T00:00:00Z", "2262-04-11T23:47:16.854775807Z"):
            r = me_at("ada", iso)
            self.assertIn(r.status, (200, 422), r)
            if r.status == 200:
                self.is_int(r.body["balance"])
        far = me_at("ada", "9999-12-31T23:59:59+00:00")
        if far.status == 200:
            self.assertEqual(far.body["balance"], me("ada")["balance"])


if __name__ == "__main__":
    unittest.main()
