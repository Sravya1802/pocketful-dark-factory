"""Stage 4: export/import across stages and processes: settlement membership, corrections, refunds, batches and SNAPSHOTS survive."""
import json
import os
import time
import unittest

from lib import *

STAGE1_URL = os.environ.get("STAGE1_URL")
STAGE2_URL = os.environ.get("STAGE2_URL")
STAGE3_URL = os.environ.get("STAGE3_URL")
ALT_URL = os.environ.get("ALT_URL")
NAMES = ["ada", "bob", "cy", "dee", "eve", "op", "op2"]


def T(f, t, a, **kw):
    d = {"from_handle": f, "to_handle": t, "amount": a}
    d.update(kw)
    return d


def norm(o):
    """drop the stage-4 refund_of: null (older stages have no such field) so older and newer bodies can be compared"""
    if isinstance(o, dict):
        return {k_: norm(v) for k_, v in o.items() if not (k_ == "refund_of" and v is None)}
    if isinstance(o, list):
        return [norm(x) for x in o]
    return o


def nosnap(b):
    return {k_: v for k_, v in b.items() if k_ != "snapshot"}


class Roundtrip(Base):
    def populate(self):
        a = {}
        a["pay_key"] = k()
        a["p"] = pay("ada", "bob", 1000, key=a["pay_key"], note="orig", visibility="private")
        a["settle_key"] = k()
        a["st"] = settle("op", [T("ada", "bob", 100), T("bob", "cy", 50), T("ada", "dee", 20)], key=a["settle_key"])
        a["refund_key"] = k()
        a["refund"] = refund("bob", a["p"].body["payment_id"], 300, key=a["refund_key"])
        q = pay("eve", "cy", 400)
        a["q"] = q
        a["corr_key"] = k()
        a["corr"] = correct("eve", q.body["payment_id"], 1, 350, ago(seconds=3), "fix", key=a["corr_key"])
        ids = [m["payment_id"] for m in a["st"].body["payments"]]
        a["batch_key"] = k()
        e = iso6(ago(seconds=2))
        a["batch_body"] = {"corrections": [citem(ids[0], 1, 80, e), citem(ids[1], 1, 40, e), citem(ids[2], 1, 10, e)]}
        a["batch"] = call("POST", "/correction-batches", a["batch_body"], token=tok("op"), key=a["batch_key"])
        au = authorize("ada", "cy", 500, note="dep").body["authorization_id"]
        a["cap"] = capture("cy", au, 200)
        a["snaps"] = {}
        for who in ("ada", "bob", "cy"):
            first = statement(who, limit=2).body
            a["snaps"][who] = (first["snapshot"], statement(who, snapshot=first["snapshot"], limit=200).body)
        for key in ("p", "st", "refund", "corr", "batch", "cap"):
            self.assertEqual(a[key].status, 201, (key, a[key]))
        return a

    def verify(self, a, call_, label):
        """everything must read the same after the import; call_(method, path, body, token, key) targets the importing service"""
        tk = {n: tok(n) for n in ("ada", "bob", "cy", "eve", "op")}
        for who, (token, frozen) in a["snaps"].items():
            r = call_("GET", "/statement?" + q(snapshot=token, limit=200), None, tk[who], None)
            self.assertEqual(r.status, 200, (label, who, "snapshot token lost", r))
            self.assertEqual(nosnap(r.body), nosnap(frozen), (label, who))
            r2 = call_("GET", "/statement?" + q(snapshot=token, limit=1, offset=1), None, tk[who], None)
            self.assertEqual(r2.body["entries"], frozen["entries"][1:2])
        for who in ("ada", "bob", "cy"):
            self.assertEqual(call_("GET", "/statement?" + q(snapshot=a["snaps"]["ada"][0]), None, tk[who], None).status, 200 if who == "ada" else 404)
        rp = call_("POST", "/payments", {"to_handle": "bob", "amount": 1000, "note": "orig", "visibility": "private"}, tk["ada"], a["pay_key"])
        self.assertEqual((rp.status, rp.body), (200, a["p"].body), label)
        rs = call_("POST", "/settlements", {"transfers": [T("ada", "bob", 100), T("bob", "cy", 50), T("ada", "dee", 20)]}, tk["op"], a["settle_key"])
        self.assertEqual((rs.status, rs.body), (200, a["st"].body), label)
        rr = call_("POST", "/payments/%s/refunds" % a["p"].body["payment_id"], {"amount": 300}, tk["bob"], a["refund_key"])
        self.assertEqual((rr.status, rr.body), (200, a["refund"].body), label)
        rc = call_("POST", "/payments/%s/corrections" % a["q"].body["payment_id"], {"expected_revision": 1, "amount": 350, "effective_at": a["corr"].body["effective_at"], "reason": "fix"}, tk["eve"], a["corr_key"])
        self.assertEqual((rc.status, rc.body), (200, a["corr"].body), label)
        rb = call_("POST", "/correction-batches", a["batch_body"], tk["op"], a["batch_key"])
        self.assertEqual((rb.status, rb.body), (200, a["batch"].body), label)
        # settlement membership retained
        feed = {x["payment_id"]: x for x in call_("GET", "/activity?limit=200", None, tk["ada"], None).body["payments"]}
        for m in a["st"].body["payments"]:
            if m["payment_id"] in feed:
                self.assertEqual(feed[m["payment_id"]]["settlement_id"], a["st"].body["settlement_id"], label)
        ids = [m["payment_id"] for m in a["st"].body["payments"]]
        e = iso6(ago(seconds=1))
        inc = call_("POST", "/correction-batches", {"corrections": [citem(ids[0], 2, 70, e)]}, tk["op"], k())
        self.assertEqual((inc.status, inc.code), (422, "incomplete_settlement"), (label, inc))
        self.assertEqual(call_("POST", "/payments/%s/corrections" % ids[0], {"expected_revision": 2, "amount": 70, "effective_at": e, "reason": "x"}, tk["ada"], k()).code, "linked_payment_immutable")
        # corrections preserved
        rv = call_("GET", "/payments/%s/revisions" % ids[0], None, tk["ada"], None).body["revisions"]
        self.assertEqual([x["revision"] for x in rv], [1, 2])
        self.assertEqual(rv[1]["correction_batch_id"], a["batch"].body["correction_batch_id"])
        # refund rules still see imported refunds: 700 left of 1000
        self.assertEqual(call_("POST", "/payments/%s/refunds" % a["p"].body["payment_id"], {"amount": 701}, tk["bob"], k()).code, "refund_exceeds_payment")
        # the refund payment is immutable, the capture is immutable
        self.assertEqual(call_("POST", "/payments/%s/corrections" % a["refund"].body["payment_id"], {"expected_revision": 1, "amount": 1, "effective_at": e, "reason": "x"}, tk["bob"], k()).code,
                         "linked_payment_immutable")
        self.assertEqual(call_("POST", "/payments/%s/corrections" % a["cap"].body["payment_id"], {"expected_revision": 1, "amount": 1, "effective_at": e, "reason": "x"}, tk["ada"], k()).code,
                         "linked_payment_immutable")

    def test_same_process_roundtrip(self):
        "[IM4-01] a stage-4 export/import keeps refunds, batches, settlement membership, corrections, replays and statement SNAPSHOT tokens (frozen entries); tokens die on reset"
        a = self.populate()
        before = snap4(["ada", "bob", "cy", "dee", "eve"])
        exp = call("GET", "/_test/export", timeout=10)
        self.assertEqual(exp.status, 200)
        pay("ada", "bob", 3)
        correct("eve", a["q"].body["payment_id"], 2, 100, ago(seconds=1))
        refund("bob", a["p"].body["payment_id"], 5)
        statement("ada")
        self.assertEqual(call("POST", "/_test/import", exp.body, timeout=10).status, 204)
        self.assertEqual(snap4(["ada", "bob", "cy", "dee", "eve"]), before)
        self.verify(a, lambda m, p, b, t, ky: call(m, p, b, token=t, key=ky), "same-process")
        reset()
        for who in ("ada", "bob", "cy"):
            self.err(statement(who, snapshot=a["snaps"][who][0]), 404, "not_found")

    @unittest.skipUnless(ALT_URL, "set ALT_URL to a SECOND running stage-4 instance for the cross-process check")
    def test_cross_process_import(self):
        "[IM4-02] export from one process, import into ANOTHER process: snapshot tokens page the same frozen entries there; replays, revisions and membership too; reset kills the tokens"
        a = self.populate()
        exp = call("GET", "/_test/export", timeout=10)
        self.assertEqual(exp.status, 200)
        self.assertEqual(call("POST", "/_test/import", exp.body, timeout=10, base=ALT_URL).status, 204)
        try:
            self.verify(a, lambda m, p, b, t, ky: call(m, p, b, token=t, key=ky, base=ALT_URL, timeout=10), "alt-process")
            self.assertEqual(call("POST", "/_test/reset", base_fixture(), timeout=10, base=ALT_URL).status, 204)
            for who in ("ada", "bob", "cy"):
                lg = call("POST", "/auth/login", {"email": who + "@example.com", "password": PW}, base=ALT_URL).body["token"]
                self.err(call("GET", "/statement?" + q(snapshot=a["snaps"][who][0]), token=lg, base=ALT_URL), 404, "not_found")
        finally:
            call("POST", "/_test/reset", base_fixture(), timeout=10, base=ALT_URL)


@unittest.skipUnless(STAGE1_URL, "set STAGE1_URL to a running STAGE-1 service")
class FromStage1(unittest.TestCase):
    def s1(self, method, path, body=None, token=None, key=None):
        return call(method, path, body, token=token, key=key, base=STAGE1_URL, timeout=10)

    def test_stage1_export(self):
        "[IM4-10] a stage-1 export imports into stage 4 with settlement membership; refunds, corrections and batches then work on the imported payments; stage-1 retries replay"
        self.assertEqual(call("POST", "/_test/reset", base_fixture(), timeout=10, base=STAGE1_URL).status, 204)
        t = {n: self.s1("POST", "/auth/login", {"email": n + "@example.com", "password": PW}).body["token"] for n in ("ada", "bob", "cy", "op")}
        pk, sk = k(), k()
        paid = self.s1("POST", "/payments", {"to_handle": "bob", "amount": 1234, "note": "n"}, token=t["ada"], key=pk)
        st = self.s1("POST", "/settlements", {"transfers": [T("ada", "bob", 100), T("bob", "cy", 50), T("ada", "dee", 20)]}, token=t["op"], key=sk)
        self.assertEqual((paid.status, st.status), (201, 201))
        cur = {n: self.s1("GET", "/me", token=t[n]).body["balance"] for n in ("ada", "bob", "cy")}
        exp = self.s1("GET", "/_test/export")
        self.assertEqual(exp.status, 200)
        reset(base_fixture(users=[user("zed", 5)], payments=[], requests=[]))
        self.assertEqual(call("POST", "/_test/import", exp.body, timeout=10).status, 204)
        for n in ("ada", "bob", "cy"):
            self.assertEqual(call("GET", "/me", token=t[n]).body["balance"], cur[n])
        ids = [m["payment_id"] for m in st.body["payments"]]
        feed = {x["payment_id"]: x for x in call("GET", "/activity?limit=200", token=t["ada"]).body["payments"]}
        for m in st.body["payments"]:
            if m["payment_id"] in feed:
                self.assertEqual(feed[m["payment_id"]]["settlement_id"], st.body["settlement_id"])
                self.assertIsNone(feed[m["payment_id"]]["refund_of"])
        e = iso6(ago(seconds=2))
        inc = call("POST", "/correction-batches", {"corrections": [citem(ids[0], 1, 90, e)]}, token=t["op"], key=k())
        self.assertEqual((inc.status, inc.code), (422, "incomplete_settlement"), inc)
        # a refund of a settlement member keeps membership
        rf = call("POST", "/payments/%s/refunds" % ids[0], {"amount": 30}, token=t["bob"], key=k())
        self.assertEqual(rf.status, 201, rf)
        self.assertEqual(rf.body["refund_of"], ids[0])
        ok = call("POST", "/correction-batches", {"corrections": [citem(ids[0], 1, 90, e), citem(ids[1], 1, 40, e), citem(ids[2], 1, 10, e)]}, token=t["op"], key=k())
        self.assertEqual(ok.status, 201, ok)
        # ordinary imported payment: refund, single correction, replay
        rf2 = call("POST", "/payments/%s/refunds" % paid.body["payment_id"], {"amount": 234}, token=t["bob"], key=k())
        self.assertEqual(rf2.status, 201, rf2)
        cr = call("POST", "/payments/%s/corrections" % paid.body["payment_id"], {"expected_revision": 1, "amount": 500, "effective_at": e, "reason": "fix"}, token=t["ada"], key=k())
        self.assertEqual(cr.status, 201, cr)
        again = call("POST", "/payments", {"to_handle": "bob", "amount": 1234, "note": "n"}, token=t["ada"], key=pk)
        self.assertEqual((again.status, again.body["payment_id"], again.body["amount"]), (200, paid.body["payment_id"], 1234))
        s2 = call("POST", "/settlements", {"transfers": [T("ada", "bob", 100), T("bob", "cy", 50), T("ada", "dee", 20)]}, token=t["op"], key=sk)
        self.assertEqual((s2.status, norm(s2.body)), (200, norm(st.body)))
        self.assertEqual(sum(me_at(n, "1970-01-01T00:00:00+00:00").body["balance"] for n in NAMES), BASE_TOTAL)


@unittest.skipUnless(STAGE2_URL, "set STAGE2_URL to a running STAGE-2 service")
class FromStage2(unittest.TestCase):
    def s2(self, method, path, body=None, token=None, key=None):
        return call(method, path, body, token=token, key=key, base=STAGE2_URL, timeout=10, headers={"Accept": "application/json"})

    def test_stage2_export_with_captures_and_holds(self):
        "[IM4-20] a stage-2 export imports: captures stay immutable and refundable (hold/authorization untouched), open holds hold, settlement membership and statements are right"
        self.assertEqual(call("POST", "/_test/reset", azfixture([seed_auth("a_1", "ada", "cy", 1000)]), timeout=10, base=STAGE2_URL).status, 204)
        t = {n: self.s2("POST", "/auth/login", {"email": n + "@example.com", "password": PW}).body["token"] for n in ("ada", "bob", "cy", "op")}
        a1 = self.s2("POST", "/authorizations", {"to_handle": "bob", "amount": 800}, token=t["ada"], key=k()).body
        a2 = self.s2("POST", "/authorizations", {"to_handle": "bob", "amount": 600}, token=t["ada"], key=k()).body
        c1 = self.s2("POST", "/authorizations/%s/capture" % a2["authorization_id"], {"amount": 250}, token=t["bob"], key=k()).body
        st = self.s2("POST", "/settlements", {"transfers": [T("ada", "bob", 40), T("bob", "cy", 10)]}, token=t["op"], key=k()).body
        cur = {n: self.s2("GET", "/me", token=t[n]).body for n in ("ada", "bob", "cy")}
        exp = call("GET", "/_test/export", base=STAGE2_URL, timeout=10)
        self.assertEqual(exp.status, 200)
        reset(base_fixture(users=[user("zed", 5)], payments=[], requests=[]))
        self.assertEqual(call("POST", "/_test/import", exp.body, timeout=10).status, 204)
        for n in ("ada", "bob", "cy"):
            m = call("GET", "/me", token=t[n]).body
            self.assertEqual({f: m[f] for f in ("balance", "total", "available", "held")}, {f: cur[n][f] for f in ("balance", "total", "available", "held")}, n)
        e = iso6(ago(seconds=2))
        # capture: immutable under single corrections and batches
        cr = call("POST", "/payments/%s/corrections" % c1["payment_id"], {"expected_revision": 1, "amount": 1, "effective_at": e, "reason": "x"}, token=t["ada"], key=k())
        self.assertEqual((cr.status, cr.code), (422, "linked_payment_immutable"))
        bt = call("POST", "/correction-batches", {"corrections": [citem(c1["payment_id"], 1, 1, e)]}, token=t["op"], key=k())
        self.assertEqual((bt.status, bt.code), (422, "linked_payment_immutable"))
        # refunding the capture works and restores nothing
        held = call("GET", "/me", token=t["ada"]).body["held"]
        rf = call("POST", "/payments/%s/refunds" % c1["payment_id"], {"amount": 100}, token=t["bob"], key=k())
        self.assertEqual(rf.status, 201, rf)
        self.assertEqual((rf.body["authorization_id"], rf.body["refund_of"]), (None, c1["payment_id"]))
        self.assertEqual(call("GET", "/me", token=t["ada"]).body["held"], held)
        row = [x for x in call("GET", "/authorizations?limit=200", token=t["ada"], headers={"Accept": "application/json"}).body["authorizations"] if x["authorization_id"] == a2["authorization_id"]][0]
        self.assertEqual((row["status"], row["captured_amount"], row["remaining_amount"]), ("captured", 250, 0))
        self.assertEqual(call("POST", "/payments/%s/refunds" % c1["payment_id"], {"amount": 151}, token=t["bob"], key=k()).code, "refund_exceeds_payment")
        # statements: the capture appears once, holds never
        ents = call("GET", "/statement?limit=200", token=t["ada"]).body["entries"]
        self.assertEqual(len([x for x in ents if x["payment"]["payment_id"] == c1["payment_id"]]), 1)
        # settlement membership
        ids = [m["payment_id"] for m in st["payments"]]
        inc = call("POST", "/correction-batches", {"corrections": [citem(ids[0], 1, 30, e)]}, token=t["op"], key=k())
        self.assertEqual((inc.status, inc.code), (422, "incomplete_settlement"))
        self.assertEqual(call("POST", "/correction-batches", {"corrections": [citem(ids[0], 1, 30, e), citem(ids[1], 1, 5, e)]}, token=t["op"], key=k()).status, 201)
        # the open hold is alive
        cap = call("POST", "/authorizations/%s/capture" % a1["authorization_id"], {}, token=t["bob"], key=k())
        self.assertEqual(cap.status, 201, cap)
        self.assertEqual(call("GET", "/me", token=t["ada"]).body["held"], 1000)   # only the seeded a_1 remains


@unittest.skipUnless(STAGE3_URL, "set STAGE3_URL to a running STAGE-3 service")
class FromStage3(unittest.TestCase):
    def s3(self, method, path, body=None, token=None, key=None):
        return call(method, path, body, token=token, key=key, base=STAGE3_URL, timeout=10, headers={"Accept": "application/json"})

    def test_stage3_export_with_corrections_and_snapshots(self):
        "[IM4-30] a stage-3 export imports: corrections (revisions, recorded times, replays), settlement membership, historical views and statement SNAPSHOT tokens minted in stage 3 (page the same frozen entries)"
        self.assertEqual(call("POST", "/_test/reset", base_fixture(), timeout=10, base=STAGE3_URL).status, 204)
        t = {n: self.s3("POST", "/auth/login", {"email": n + "@example.com", "password": PW}).body["token"] for n in ("ada", "bob", "cy", "op")}
        p = self.s3("POST", "/payments", {"to_handle": "bob", "amount": 900, "note": "x"}, token=t["ada"], key=k()).body
        st = self.s3("POST", "/settlements", {"transfers": [T("ada", "bob", 100), T("bob", "cy", 50)]}, token=t["op"], key=k()).body
        ck = k()
        cbody = {"expected_revision": 1, "amount": 600, "effective_at": iso6(ago(seconds=3)), "reason": "fix"}
        c = self.s3("POST", "/payments/%s/corrections" % p["payment_id"], cbody, token=t["ada"], key=ck)
        self.assertEqual(c.status, 201, c)
        snaps = {}
        for who in ("ada", "bob"):
            first = self.s3("GET", "/statement?limit=2", token=t[who]).body
            snaps[who] = (first["snapshot"], self.s3("GET", "/statement?" + q(snapshot=first["snapshot"], limit=200), token=t[who]).body)
        revs = self.s3("GET", "/payments/%s/revisions" % p["payment_id"], token=t["ada"]).body
        st_before = {n: self.s3("GET", "/statement?limit=200", token=t[n]).body["entries"] for n in ("ada", "bob")}
        exp = self.s3("GET", "/_test/export")
        self.assertEqual(exp.status, 200)
        reset(base_fixture(users=[user("zed", 5)], payments=[], requests=[]))
        self.assertEqual(call("POST", "/_test/import", exp.body, timeout=10).status, 204)
        self.assertEqual(norm(call("GET", "/payments/%s/revisions" % p["payment_id"], token=t["ada"]).body), norm(revs))
        for n in ("ada", "bob"):
            now = call("GET", "/statement?limit=200", token=t[n]).body["entries"]
            self.assertEqual(norm(now), norm(st_before[n]), n)
            tokn, frozen = snaps[n]
            r = call("GET", "/statement?" + q(snapshot=tokn, limit=200), token=t[n])
            self.assertEqual(r.status, 200, "a statement snapshot token minted before the stage-3 export was lost by the import: %r" % r)
            self.assertEqual(norm(nosnap(r.body)), norm(nosnap(frozen)), n)
        rep = call("POST", "/payments/%s/corrections" % p["payment_id"], cbody, token=t["ada"], key=ck)
        self.assertEqual((rep.status, rep.body), (200, c.body))
        ids = [m["payment_id"] for m in st["payments"]]
        e = iso6(ago(seconds=2))
        self.assertEqual(call("POST", "/correction-batches", {"corrections": [citem(ids[0], 1, 90, e)]}, token=t["op"], key=k()).code, "incomplete_settlement")
        ok = call("POST", "/correction-batches", {"corrections": [citem(ids[0], 1, 90, e), citem(ids[1], 1, 40, e), citem(p["payment_id"], 2, 500, e)]}, token=t["op"], key=k())
        self.assertEqual(ok.status, 201, ok)
        rf = call("POST", "/payments/%s/refunds" % p["payment_id"], {"amount": 200}, token=t["bob"], key=k())
        self.assertEqual(rf.status, 201, rf)
        self.assertEqual(sum(me_at(n, "1970-01-01T00:00:00+00:00").body["balance"] for n in NAMES), BASE_TOTAL)


if __name__ == "__main__":
    unittest.main()
