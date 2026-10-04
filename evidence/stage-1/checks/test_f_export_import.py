"""GET /_test/export and POST /_test/import."""
import copy
import json
import threading
import time
import unittest

from lib import *


def T(f, t, a, **kw):
    d = {"from_handle": f, "to_handle": t, "amount": a}
    d.update(kw)
    return d


def export():
    r = call("GET", "/_test/export", timeout=10)
    assert r.status == 200, r
    return r.body


def do_import(obj, expect=204):
    r = call("POST", "/_test/import", obj, timeout=10)
    assert r.status == expect, r
    return r


def snapshot(tokens):
    """Everything observable for the given {name: token}: me, full feed, all requests."""
    out = {}
    for n, t in tokens.items():
        me_ = call("GET", "/me", token=t)
        act = call("GET", "/activity?limit=200", token=t)
        rq = call("GET", "/requests?limit=200", token=t)
        out[n] = (me_.status, me_.body, act.status, act.body, rq.status, rq.body)
    return out


class ExportImport(Base):
    def populate(self):
        """Build a rich state; returns dict of artefacts used to verify restoration."""
        a = {}
        a["pk"] = k()
        a["pay"] = pay("ada", "bob", 1234, key=a["pk"], note="café ☕", visibility="private")
        a["pub"] = pay("bob", "cy", 200, note="pub")
        a["failkey"] = k()
        a["fail"] = pay("dee", "bob", 99, key=a["failkey"])  # insufficient_funds: key stays reusable
        a["rq_paid"] = req("bob", "ada", 400).body
        a["rq_paid_key"] = k()
        a["rq_pay"] = pay_req("ada", a["rq_paid"]["request_id"], key=a["rq_paid_key"], body={"visibility": "private"})
        a["rq_pending"] = req("cy", "eve", 5000, note="pending-one").body
        a["rq_declined"] = req("cy", "ada", 7).body
        call("POST", "/requests/%s/decline" % a["rq_declined"]["request_id"], token=tok("ada"))
        a["rq_cancelled"] = req("dee", "ada", 8).body
        call("POST", "/requests/%s/cancel" % a["rq_cancelled"]["request_id"], token=tok("dee"))
        a["req_key"] = k()
        a["req_create"] = call("POST", "/requests", {"payer_handle": "eve", "amount": 9}, token=tok("bob"), key=a["req_key"])
        a["split_key"] = k()
        a["split"] = call("POST", "/splits", {"amount": 10, "participant_handles": ["ada", "bob", "cy"], "note": "s"}, token=tok("ada"),
                          key=a["split_key"])
        a["set_key"] = k()
        a["set"] = settle("op", [T("ada", "dee", 100, note="m1", visibility="private"), T("dee", "cy", 60, note="m2")], key=a["set_key"])
        a["signup_email"] = "imp.user@example.com"
        a["signup"] = signup(a["signup_email"], password="long passphrase")
        a["signup_token"] = a["signup"].body["token"]
        pay("ada", "imp_user", 300)
        self.assertEqual([a["pay"].status, a["rq_pay"].status, a["split"].status, a["set"].status, a["signup"].status], [201] * 5)
        self.assertEqual(a["fail"].status, 409)
        return a

    def toks(self, a):
        t = {n: tok(n) for n in BASE_NAMES}
        t["imp_user"] = a["signup_token"]
        return t

    def test_export_shape(self):
        "[EXP-01] export: 200, track 'pocketful', format_version 1, state object; no auth needed"
        r = call("GET", "/_test/export", timeout=10)
        self.assertEqual(r.status, 200)
        self.assertEqual(r.body["track"], "pocketful")
        self.assertEqual(r.body["format_version"], 1)
        self.assertTrue(isinstance(r.body["format_version"], int) and not isinstance(r.body["format_version"], bool))
        self.assertIsInstance(r.body["state"], dict)
        self.assertTrue(r.headers.get("content-type", "").startswith("application/json"))

    def test_roundtrip_restores_everything(self):
        "[EXP-02] import of an unchanged export restores accounts, tokens, balances, payments, requests, idempotent replies, operators, ids and timestamps"
        a = self.populate()
        t = self.toks(a)
        before = snapshot(t)
        exp = export()
        # --- mutate heavily after the export
        post_key = k()
        pay("ada", "cy", 777, key=post_key)
        signup("after.export@example.com")
        call("POST", "/requests/%s/cancel" % a["rq_pending"]["request_id"], token=tok("cy"))
        settle("op", [T("eve", "bob", 1000)])
        self.assertNotEqual(snapshot(t), before)
        # --- restore
        self.assertEqual(call("POST", "/_test/import", exp, timeout=10).status, 204)
        self.assertEqual(snapshot(t), before)
        self.assertEqual(total() + 300, BASE_TOTAL)  # imp_user holds 300
        # tokens issued before the export survive; user created after it is gone
        self.assertEqual(call("GET", "/me", token=a["signup_token"]).status, 200)
        self.err(call("POST", "/auth/login", {"email": "after.export@example.com", "password": PW}), 401, "unauthenticated")
        # hashed-password login of a signed-up and a seeded account
        self.assertEqual(call("POST", "/auth/login", {"email": a["signup_email"], "password": "long passphrase"}).status, 200)
        self.assertEqual(call("POST", "/auth/login", {"email": "ada@example.com", "password": PW}).status, 200)
        self.err(call("POST", "/auth/login", {"email": "ada@example.com", "password": "wrong password"}), 401, "unauthenticated")
        # replays: original responses, no new money moves
        b0 = [bal(n) for n in BASE_NAMES]
        r = pay("ada", "bob", 1234, key=a["pk"], note="café ☕", visibility="private")
        self.assertEqual((r.status, r.body), (200, a["pay"].body))
        r = pay_req("ada", a["rq_paid"]["request_id"], key=a["rq_paid_key"], body={"visibility": "private"})
        self.assertEqual((r.status, r.body), (200, a["rq_pay"].body))
        r = call("POST", "/requests", {"payer_handle": "eve", "amount": 9}, token=tok("bob"), key=a["req_key"])
        self.assertEqual((r.status, r.body), (200, a["req_create"].body))
        r = call("POST", "/splits", {"amount": 10, "participant_handles": ["ada", "bob", "cy"], "note": "s"}, token=tok("ada"), key=a["split_key"])
        self.assertEqual((r.status, r.body), (200, a["split"].body))
        r = settle("op", [T("ada", "dee", 100, note="m1", visibility="private"), T("dee", "cy", 60, note="m2")], key=a["set_key"])
        self.assertEqual((r.status, r.body), (200, a["set"].body))
        self.assertEqual([bal(n) for n in BASE_NAMES], b0)
        # different body under an imported key is still a reuse
        self.err(pay("ada", "bob", 1235, key=a["pk"], note="café ☕", visibility="private"), 409, "idempotency_key_reuse")
        # failed key still reusable; key used only after the export is a first use again
        self.assertEqual(bal("dee"), 40)
        self.assertEqual(pay("dee", "bob", 40, key=a["failkey"]).status, 201)  # key of an earlier 409 is a first use
        r = pay("ada", "cy", 777, key=post_key)
        self.assertEqual(r.status, 201)
        # operator permission preserved
        self.assertEqual(settle("op", [T("ada", "bob", 1)]).status, 201)
        self.err(settle("ada", [T("ada", "bob", 1)]), 403, "forbidden")

    def test_import_is_replacement_and_repeatable(self):
        "[EXP-03] import replaces (not merges) and can be repeated without duplicating anything"
        a = self.populate()
        t = self.toks(a)
        exp = export()
        before = snapshot(t)
        for _ in range(3):
            self.assertEqual(call("POST", "/_test/import", exp, timeout=10).status, 204)
            self.assertEqual(snapshot(t), before)
        self.assertEqual(total() + 300, BASE_TOTAL)

    def test_import_removes_destination_data_and_credentials(self):
        "[EXP-04] state and tokens that exist only in the destination are gone after import; export from a different fixture world"
        exp = export()  # base world
        s = signup("dest.only@example.com")
        pay("ada", "dest_only", 50)
        reset(base_fixture(currency="JPY", minor_units=0, users=[user("ada", 5), user("zed", 9)], payments=[], requests=[],
                           settlement_operator_ids=[]))
        s2 = signup("dest.only2@example.com")
        self.assertEqual(call("POST", "/_test/import", exp, timeout=10).status, 204)
        self.assertEqual(call("GET", "/me", token=s2.body["token"]).status, 401)
        self.assertEqual(call("GET", "/me", token=s.body["token"]).status, 401)  # was created *after* the export in a world since replaced
        self.assertEqual(call("POST", "/auth/login", {"email": "zed@example.com", "password": PW}).status, 401)
        self.assertEqual(call("POST", "/auth/login", {"email": "dest.only2@example.com", "password": PW}).status, 401)
        m = me("ada")
        self.assertEqual((m["balance"], m["currency"], m["minor_units"]), (10000, "EUR", 2))
        self.assertEqual(total(), BASE_TOTAL)

    def test_imported_world_is_fully_live(self):
        "[EXP-05] after import: new ids do not collide, balances behave, old payments keep ids/timestamps, new activity works"
        a = self.populate()
        exp = export()
        old = {p["payment_id"]: p for n in ("ada", "bob", "cy", "dee") for p in activity(n, limit=200)["payments"]}
        reset()
        self.assertEqual(call("POST", "/_test/import", exp, timeout=10).status, 204)
        now = {p["payment_id"]: p for n in ("ada", "bob", "cy", "dee") for p in activity(n, limit=200)["payments"]}
        self.assertEqual(now, old)
        newp = pay("ada", "bob", 1).body
        self.assertNotIn(newp["payment_id"], old)
        known = {q["request_id"] for n in ("ada", "bob", "cy", "dee", "eve") for q in requests_of(n, limit=200)["requests"]}
        newr = req("bob", "ada", 5).body
        self.assertNotIn(newr["request_id"], known)
        newset = settle("op", [T("ada", "bob", 1)]).body
        self.assertNotEqual(newset["settlement_id"], a["set"].body["settlement_id"])
        self.assertTrue(all(p["payment_id"] not in old for p in newset["payments"]))
        # pending request from the export is still payable once, by its payer
        self.assertEqual(pay_req("eve", a["rq_pending"]["request_id"]).status, 201)
        self.err(pay_req("eve", a["rq_pending"]["request_id"]), 409, "request_not_pending")

    def test_settlement_membership_survives(self):
        "[EXP-06] settlement membership, constituent visibility and replay survive export/import"
        a = self.populate()
        sid = a["set"].body["settlement_id"]
        exp = export()
        reset(base_fixture(settlement_operator_ids=[]))
        do_import(exp)
        mem = [p for n in ("ada", "dee", "cy") for p in activity(n, limit=200)["payments"] if p.get("settlement_id") == sid]
        self.assertEqual({p["payment_id"] for p in mem}, {p["payment_id"] for p in a["set"].body["payments"]})
        priv = a["set"].body["payments"][0]["payment_id"]
        self.assertNotIn(priv, feed_ids("eve"))
        self.assertIn(priv, feed_ids("dee"))
        r = settle("op", [T("ada", "dee", 100, note="m1", visibility="private"), T("dee", "cy", 60, note="m2")], key=a["set_key"])
        self.assertEqual((r.status, r.body), (200, a["set"].body))

    def test_reset_after_import_clears_imported_state(self):
        "[EXP-07] reset clears everything, including imported state"
        a = self.populate()
        exp = export()
        do_import(exp)
        reset(base_fixture(users=[user("ada", 1), user("bob", 2)], payments=[], requests=[], settlement_operator_ids=[]))
        self.assertEqual(call("GET", "/me", token=a["signup_token"]).status, 401)
        self.assertEqual(call("POST", "/auth/login", {"email": a["signup_email"], "password": "long passphrase"}).status, 401)
        self.assertEqual(activity("ada")["payments"], [])
        self.assertEqual(pay("ada", "bob", 1, key=a["pk"]).status, 201)  # imported key forgotten

    def test_invalid_import_is_422_and_changes_nothing(self):
        "[EXP-08] missing fields, wrong track/version or invalid state -> 422 validation_failed; destination untouched"
        a = self.populate()
        t = self.toks(a)
        before = snapshot(t)
        good = export()
        bad = [{}, {"track": "pocketful"}, {"track": "pocketful", "format_version": 1},
               {"format_version": 1, "state": good["state"]}, {"track": "pocketful", "state": good["state"]},
               {**good, "track": "tablekeeper"}, {**good, "track": ""}, {**good, "track": None}, {**good, "track": 1},
               {**good, "format_version": 2}, {**good, "format_version": 0}, {**good, "format_version": "1"}, {**good, "format_version": None},
               {**good, "state": None}, {**good, "state": "x"}, {**good, "state": 5}, {**good, "state": []}, {**good, "state": [1, 2]},
               {**good, "state": True}]
        for b in bad:
            self.err(call("POST", "/_test/import", b, timeout=10), 422, "validation_failed")
            self.assertEqual(snapshot(t), before)
        self.assertEqual(do_import(good).status, 204)
        self.assertEqual(snapshot(t), before)

    def test_import_garbage_body_400(self):
        "[EXP-09] unparseable import body -> 400 malformed_request, destination untouched"
        a = self.populate()
        t = self.toks(a)
        before = snapshot(t)
        for raw in (b"{", b"", b"\xff", b"nope"):
            self.err(call("POST", "/_test/import", raw=raw, timeout=10), 400, "malformed_request")
        for raw in (b"null", b"[]", b"5", b'"s"'):
            r = call("POST", "/_test/import", raw=raw, timeout=10)
            self.assertIn(r.status, (400, 422))
            self.err4xx(r, {"malformed_request", "validation_failed"})
        self.assertEqual(snapshot(t), before)

    def test_corrupted_state_rejected_atomically(self):
        "[EXP-10] a structurally damaged state is either rejected (422, no change) or accepted whole - never half-applied"
        a = self.populate()
        t = self.toks(a)
        before = snapshot(t)
        good = export()
        damaged = copy.deepcopy(good)
        st = damaged["state"]
        # blank out every top-level member of the opaque state in turn
        for key in list(st.keys()):
            d2 = copy.deepcopy(good)
            d2["state"][key] = "corrupt"
            r = call("POST", "/_test/import", d2, timeout=10)
            if r.status == 204:
                do_import(good)  # accepted: restore and move on
            else:
                self.err(r, 422, "validation_failed")
                self.assertEqual(snapshot(t), before)
        do_import(good)
        self.assertEqual(snapshot(t), before)

    def test_export_is_read_only_and_snapshot(self):
        "[EXP-11] export has no side effects; later writes never change an earlier export (importing it yields the earlier world)"
        t = {n: tok(n) for n in BASE_NAMES}
        s0 = snapshot(t)
        e1 = export()
        e2 = export()
        self.assertEqual(snapshot(t), s0)
        pay("ada", "bob", 100)
        call("POST", "/requests/rq_1/cancel", token=tok("bob"))
        do_import(e1)
        self.assertEqual(snapshot(t), s0)
        pay("ada", "bob", 100)
        do_import(e2)
        self.assertEqual(snapshot(t), s0)

    def test_export_atomic_under_concurrent_writes(self):
        "[EXP-12] every export taken while money is moving is a consistent snapshot (sum of balances = seeded total, none negative)"
        stop = threading.Event()
        names = ["ada", "bob", "cy", "eve"]
        toks = {n: tok(n) for n in BASE_NAMES}  # log everyone in before the first export so tokens survive every import

        def writer(i):
            j = 0
            while not stop.is_set():
                a, b = names[(i + j) % 4], names[(i + j + 1) % 4]
                safe_call("POST", "/payments", {"to_handle": b, "amount": 1 + (j % 50)}, token=toks[a], key=k())
                j += 1

        ws = [threading.Thread(target=writer, args=(i,), daemon=True) for i in range(8)]
        for w in ws:
            w.start()
        exps = []
        for _ in range(6):
            r = call("GET", "/_test/export", timeout=10)
            self.assertEqual(r.status, 200)
            exps.append(r.body)
            time.sleep(0.15)
        stop.set()
        for w in ws:
            w.join(timeout=10)
        for e in exps:
            do_import(e)
            bs = [bal(n) for n in BASE_NAMES]
            self.assertEqual(sum(bs), BASE_TOTAL)
            self.assertTrue(all(b >= 0 for b in bs), bs)

    def test_snapshot_balances_match_payment_history(self):
        "[EXP-13] import never replays seeded history against a balance, and never double-applies payments"
        pay("ada", "bob", 1000)
        pay("bob", "cy", 500)
        exp = export()
        for _ in range(3):
            do_import(exp)
        self.assertEqual([bal(n) for n in ("ada", "bob", "cy")], [9000, 3000, 1500])
        # a fresh reset with the same fixture then import gives the same balances (no net-on-net replay)
        reset()
        do_import(exp)
        self.assertEqual([bal(n) for n in ("ada", "bob", "cy")], [9000, 3000, 1500])

    def test_import_needs_no_auth_and_returns_empty_204(self):
        "[EXP-14] import/export are unauthenticated test endpoints; import returns an empty 204"
        e = call("GET", "/_test/export", timeout=10)
        r = call("POST", "/_test/import", e.body, timeout=10)
        self.assertEqual((r.status, r.text), (204, ""))

    def test_export_contains_no_plaintext_passwords_after_import(self):
        "[EXP-15] hashed passwords round-trip: no plaintext appears in a re-export either"
        signup("hash.rt@example.com", password="Another-Unmistakable-Pw-9182")
        e = call("GET", "/_test/export", timeout=10)
        do_import(e.body)
        e2 = call("GET", "/_test/export", timeout=10)
        self.assertNotIn("Another-Unmistakable-Pw-9182", e2.text)
        self.assertEqual(call("POST", "/auth/login", {"email": "hash.rt@example.com", "password": "Another-Unmistakable-Pw-9182"}).status, 200)

    def test_unknown_fields_in_import_envelope_ignored(self):
        "[EXP-16] unknown fields beside track/format_version/state are ignored"
        e = export()
        e["note"] = "extra"
        self.assertEqual(call("POST", "/_test/import", e, timeout=10).status, 204)

    def test_roundtrip_across_currencies(self):
        "[EXP-17] an exported JPY (minor_units 0) world imports back as JPY even when the destination is EUR"
        reset(base_fixture(currency="JPY", minor_units=0))
        pay("ada", "bob", 123)
        e = export()
        reset()
        do_import(e)
        m = me("ada")
        self.assertEqual((m["currency"], m["minor_units"], m["balance"]), ("JPY", 0, 9877))


if __name__ == "__main__":
    unittest.main()
