"""Stage 3: GET /statement and stable pagination (snapshots)."""
import json
import threading
import time
import unittest

from lib import *


def entry_sig(e):
    return (e["payment"]["payment_id"], e["delta"], e["balance_after"], e["revision"], dt(e["effective_at"]), dt(e["recorded_at"]), e["payment"]["amount"])


class Statement(Base):
    def setUp(self):
        self.fx, self.led, self.now0 = hist(H_SPECS)
        reset(self.fx)

    def t(self, h):
        return iso6(self.now0 - timedelta(hours=h))

    def test_shape_default_window(self):
        "[ST-01] default window = the whole life: opening balance, entries oldest first with delta and balance_after, closing, has_more"
        r = statement("ada")
        self.assertEqual(r.status, 200, r)
        b = r.body
        self.assertEqual(b["opening_balance"], 10000)
        self.assertEqual(b["closing_balance"], 8600)
        self.assertEqual([(e["payment"]["payment_id"], e["delta"], e["balance_after"]) for e in b["entries"]],
                         [("p_001", -500, 9500), ("p_003", 100, 9600), ("p_004", -1000, 8600)])
        self.assertIs(b["has_more"], False)
        self.assertIsInstance(b["snapshot"], str)
        for e in b["entries"]:
            for f in ("payment", "delta", "balance_after", "revision", "effective_at", "recorded_at"):
                self.assertIn(f, e)
            self.payment_shape(e["payment"], near_now=False)
            self.assertEqual(e["revision"], 1)
            self.assertEqual(dt(e["effective_at"]), dt(e["recorded_at"]))
            self.assertEqual(dt(e["effective_at"]), dt(e["payment"]["created_at"]))
        self.assertEqual(statement("dee").body["opening_balance"], 0)

    def test_arithmetic_and_directions(self):
        "[ST-02] opening + all deltas == closing; sent payments negative, received positive; consistent for every user"
        for n in ("ada", "bob", "cy", "dee", "eve"):
            b, entries = all_statement(n)
            self.assertEqual(b["opening_balance"] + sum(e["delta"] for e in entries), b["closing_balance"], n)
            run = b["opening_balance"]
            for e in entries:
                run += e["delta"]
                self.assertEqual(e["balance_after"], run)
                p = e["payment"]
                self.assertEqual(e["delta"], -p["amount"] if p["from_handle"] == n else p["amount"])
            self.assertEqual(b["closing_balance"], me(n)["balance"])
        self.assertEqual(statement("eve").body["entries"], [])
        self.assertEqual(statement("eve").body["opening_balance"], 5000)
        self.assertEqual(statement("eve").body["closing_balance"], 5000)

    def test_half_open_window(self):
        "[ST-03] window is [from, to): a payment at exactly from is in, at exactly to is out; opening = balance before from; closing = balance before to"
        # p_003 at -6h (cy->ada 100): ada 9500 before, 9600 after
        b = statement("ada", **{"from": self.t(6), "to": self.t(4)}).body
        self.assertEqual([e["payment"]["payment_id"] for e in b["entries"]], ["p_003"])      # p_004 at exactly `to` is out
        self.assertEqual((b["opening_balance"], b["closing_balance"]), (9500, 9600))
        b = statement("ada", **{"from": self.t(4)}).body
        self.assertEqual([e["payment"]["payment_id"] for e in b["entries"]], ["p_004"])      # at exactly `from` is in
        self.assertEqual((b["opening_balance"], b["closing_balance"]), (9600, 8600))
        b = statement("ada", **{"to": self.t(10)}).body
        self.assertEqual(b["entries"], [])
        self.assertEqual((b["opening_balance"], b["closing_balance"]), (10000, 10000))
        b = statement("ada", **{"from": self.t(10), "to": self.t(10)}).body                  # empty window
        self.assertEqual(b["entries"], [])
        self.assertEqual((b["opening_balance"], b["closing_balance"]), (10000, 10000))
        b = statement("ada", **{"from": self.t(9), "to": self.t(5)}).body
        self.assertEqual((b["opening_balance"], b["closing_balance"], [e["payment"]["payment_id"] for e in b["entries"]]), (9500, 9600, ["p_003"]))
        b = statement("ada", **{"from": self.t(100)}).body
        self.assertEqual(b["opening_balance"], 10000)
        b = statement("ada", **{"from": iso6(now_utc() + timedelta(days=1))}).body       # window after everything
        self.assertEqual((b["entries"], b["opening_balance"], b["closing_balance"]), ([], 8600, 8600))

    def test_inverted_window_is_empty_or_rejected(self):
        "[ST-04] from after to: no entries (opening == closing); never 5xx"
        r = statement("ada", **{"from": self.t(4), "to": self.t(10)})
        self.assertLess(r.status, 500)
        if r.status == 200:
            self.assertEqual(r.body["entries"], [])
        else:
            self.err(r, 422, "validation_failed")

    def test_only_own_payments(self):
        "[ST-05] only payments the caller sent or received, however public others are; no feed visibility rules"
        pay("bob", "cy", 10, visibility="public", note="pub")
        pay("bob", "cy", 11, visibility="private", note="prv")
        for who, want in (("ada", set()), ("eve", set()), ("bob", {"pub", "prv"}), ("cy", {"pub", "prv"})):
            notes = {e["payment"]["note"] for e in all_statement(who)[1]} & {"pub", "prv"}
            self.assertEqual(notes, want, who)
        # a private payment appears in BOTH parties' statements
        self.assertEqual(len([e for e in all_statement("bob")[1] if e["payment"]["note"] == "prv"]), 1)
        # statement is not the feed: p_002 (public) between bob/cy is not in ada's statement
        self.assertNotIn("p_002", [e["payment"]["payment_id"] for e in all_statement("ada")[1]])

    def test_ordering_and_ties(self):
        "[ST-06] entries ordered by effective time ascending then payment id ascending; ties give a progressive balance_after"
        t = self.now0 - timedelta(hours=1)
        specs = [("p_010", "ada", "bob", 10, 1), ("p_007", "ada", "cy", 20, 1), ("p_009", "bob", "ada", 5, 1), ("p_008", "cy", "ada", 1, 2)]
        fx, led, n0 = hist(specs, now0=self.now0)
        reset(fx)
        b = statement("ada").body
        ids = [e["payment"]["payment_id"] for e in b["entries"]]
        self.assertEqual(ids, ["p_008", "p_007", "p_009", "p_010"])
        self.assertEqual([e["balance_after"] for e in b["entries"]], [10001, 9981, 9986, 9976])
        self.assertEqual(b["closing_balance"], 9976)

    def test_pagination_does_not_change_values(self):
        "[ST-07] limit/offset never change balance_after or the window's opening/closing; has_more exact on partial, full and beyond-end pages"
        for i in range(7):
            pay("ada" if i % 2 == 0 else "bob", "bob" if i % 2 == 0 else "ada", 10 + i)
        full_b, full = all_statement("ada")
        n = len(full)
        self.assertGreaterEqual(n, 9)
        for limit in (1, 2, 3, 5, n, n + 5):
            seen, off = [], 0
            while True:
                r = statement("ada", limit=limit, offset=off)
                self.assertEqual(r.status, 200, r)
                self.assertEqual((r.body["opening_balance"], r.body["closing_balance"]), (full_b["opening_balance"], full_b["closing_balance"]))
                seen += r.body["entries"]
                self.assertEqual(r.body["has_more"], off + limit < n, (limit, off))
                self.assertLessEqual(len(r.body["entries"]), limit)
                if not r.body["has_more"]:
                    break
                off += limit
            self.assertEqual([entry_sig(e) for e in seen], [entry_sig(e) for e in full], limit)
        r = statement("ada", limit=5, offset=n + 10)
        self.assertEqual((r.body["entries"], r.body["has_more"]), ([], False))
        self.assertEqual((r.body["opening_balance"], r.body["closing_balance"]), (full_b["opening_balance"], full_b["closing_balance"]))
        r = statement("ada", limit=3, offset=n - 1)
        self.assertEqual((len(r.body["entries"]), r.body["has_more"]), (1, False))
        r = statement("ada", limit=1, offset=n - 1)
        self.assertEqual((len(r.body["entries"]), r.body["has_more"]), (1, False))
        r = statement("ada", limit=1, offset=n - 2)
        self.assertEqual((len(r.body["entries"]), r.body["has_more"]), (1, True))

    def test_pagination_inside_a_window(self):
        "[ST-08] pagination inside a [from,to) window keeps the window's opening and closing and the carried-in balance_after"
        for i in range(6):
            pay("ada", "bob", 5)
        b, full = all_statement("ada", **{"from": self.t(5)})
        p = statement("ada", limit=2, offset=2, **{"from": self.t(5)}).body
        self.assertEqual(p["opening_balance"], b["opening_balance"])
        self.assertEqual(p["closing_balance"], b["closing_balance"])
        self.assertEqual([entry_sig(e) for e in p["entries"]], [entry_sig(e) for e in full[2:4]])
        self.assertEqual(p["entries"][0]["balance_after"], b["opening_balance"] + sum(e["delta"] for e in full[:3]))

    def test_limit_offset_validation(self):
        "[ST-09] limit 1..200 / offset >= 0 plain digits, exactly as GET /requests"
        for qs in ("limit=0", "limit=201", "limit=-1", "limit=x", "limit=1e2", "limit=4.0", "limit=+4", "limit=", "offset=-1", "offset=x", "offset=1.0", "offset="):
            self.err(call("GET", "/statement?" + qs, token=tok("ada")), 422, "validation_failed")
        for qs in ("limit=1", "limit=200", "offset=0", "offset=999999", "foo=bar&limit=3"):
            self.assertEqual(call("GET", "/statement?" + qs, token=tok("ada")).status, 200, qs)

    def test_from_to_validation(self):
        "[ST-10] from/to must be RFC 3339 instants with an offset; naive, date-only, empty -> 422"
        for key in ("from", "to", "known_at"):
            for bad in ("2026-09-24T13:20:00", "2026-09-24", "", "garbage", "2026-13-40T00:00:00Z"):
                r = call("GET", "/statement?" + urllib.parse.urlencode({key: bad}), token=tok("ada"))
                self.err(r, 422, "validation_failed")
        self.err(call("GET", "/statement?from=", token=tok("ada")), 422, "validation_failed")

    def test_auth_required(self):
        "[ST-11] 401 without a token"
        self.err(call("GET", "/statement"), 401, "unauthenticated")
        self.err(call("GET", "/statement?snapshot=abc"), 401, "unauthenticated")

    def test_default_to_is_now_and_new_payments_appear(self):
        "[ST-12] default to = now: payments made after an earlier read appear in the next one"
        n0 = len(all_statement("ada")[1])
        pay("ada", "bob", 9, note="newest")
        b, ents = all_statement("ada")
        self.assertEqual(len(ents), n0 + 1)
        self.assertEqual(ents[-1]["payment"]["note"], "newest")
        self.assertEqual(b["closing_balance"], me("ada")["balance"])
        self.assertEqual(ents[-1]["balance_after"], me("ada")["balance"])

    def test_amounts_and_payment_objects(self):
        "[ST-13] entries embed the full payment (handles, note, visibility, request_id, authorization_id, settlement_id)"
        rid = req("bob", "ada", 50, note="q").body["request_id"]
        pr = pay_req("ada", rid).body
        e = [x for x in all_statement("ada")[1] if x["payment"]["payment_id"] == pr["payment_id"]][0]
        self.assertEqual(e["payment"]["request_id"], rid)
        self.assertEqual((e["payment"]["from_handle"], e["payment"]["to_handle"], e["delta"]), ("ada", "bob", -50))
        st = settle("op", [{"from_handle": "ada", "to_handle": "dee", "amount": 3, "note": "s"}]).body
        e = [x for x in all_statement("ada")[1] if x["payment"]["payment_id"] == st["payments"][0]["payment_id"]][0]
        self.assertEqual(e["payment"]["settlement_id"], st["settlement_id"])
        self.assertEqual((dt(e["effective_at"]), dt(e["recorded_at"])), (dt(st["committed_at"]), dt(st["committed_at"])))

    def test_sum_of_closing_balances(self):
        "[ST-14] across all wallets the closing balances of any window sum to the seeded total (money is conserved in every view)"
        names = ["ada", "bob", "cy", "dee", "eve", "op", "op2"]
        for to in (self.t(9), self.t(5), self.t(1), None):
            kw = {"to": to} if to else {}
            self.assertEqual(sum(statement(n, **kw).body["closing_balance"] for n in names), H_TOTAL, to)
            self.assertEqual(sum(statement(n, **{"from": self.t(7), **kw}).body["opening_balance"] for n in names), H_TOTAL)


class Snapshots(Base):
    def setUp(self):
        self.fx, self.led, self.now0 = hist(H_SPECS)
        reset(self.fx)
        for i in range(6):
            pay("ada", "bob", 10 + i, note="s%d" % i)
        time.sleep(0.05)

    def test_snapshot_token_and_paging(self):
        "[SN-01] every first response carries an opaque snapshot token; paging the token returns exactly the frozen result"
        first = statement("ada", limit=2)
        self.assertEqual(first.status, 200)
        tokn = first.body["snapshot"]
        self.assertIsInstance(tokn, str)
        self.assertTrue(0 < len(tokn) <= 4096)
        full_b, full = all_statement("ada")
        got, off = [], 0
        while True:
            r = statement("ada", snapshot=tokn, limit=3, offset=off)
            self.assertEqual(r.status, 200, r)
            self.assertEqual((r.body["opening_balance"], r.body["closing_balance"]), (full_b["opening_balance"], full_b["closing_balance"]))
            got += r.body["entries"]
            self.assertEqual(r.body["has_more"], off + 3 < len(full))
            if not r.body["has_more"]:
                break
            off += 3
        self.assertEqual([entry_sig(e) for e in got], [entry_sig(e) for e in full])
        r = statement("ada", snapshot=tokn, limit=5, offset=len(full) + 7)
        self.assertEqual((r.body["entries"], r.body["has_more"]), ([], False))
        # default limit on a snapshot
        self.assertEqual(len(statement("ada", snapshot=tokn).body["entries"]), len(full))
        self.assertNotEqual(statement("ada").body["snapshot"], tokn)  # each fresh read is its own snapshot

    def test_frozen_after_payments(self):
        "[SN-02] after new payments the snapshot still returns the frozen entries, balances and (default) window end"
        first = statement("ada", limit=3).body
        tokn = first["snapshot"]
        _, full_before = all_statement("ada")
        for i in range(4):
            pay("ada", "bob", 3, note="late%d" % i)
            pay("bob", "ada", 2)
        replay_all, off = [], 0
        while True:
            r = statement("ada", snapshot=tokn, limit=4, offset=off).body
            replay_all += r["entries"]
            self.assertEqual((r["opening_balance"], r["closing_balance"]), (first["opening_balance"], first["closing_balance"]))
            if not r["has_more"]:
                break
            off += 4
        self.assertEqual([entry_sig(e) for e in replay_all], [entry_sig(e) for e in full_before])
        self.assertNotIn("late0", [e["payment"]["note"] for e in replay_all])
        self.assertGreater(len(all_statement("ada")[1]), len(full_before))

    def test_only_limit_and_offset_may_accompany(self):
        "[SN-03] supplying from, to or known_at with a snapshot -> 422 validation_failed; unrecognised params are ignored"
        tokn = statement("ada").body["snapshot"]
        for extra in ({"from": iso6(now_utc())}, {"to": iso6(now_utc())}, {"known_at": iso6(now_utc())}, {"from": "x"}):
            self.err(statement("ada", snapshot=tokn, **extra), 422, "validation_failed")
        self.assertEqual(statement("ada", snapshot=tokn, foo="bar", as_of="whatever").status, 200)
        self.err(statement("ada", snapshot=tokn, limit=0), 422, "validation_failed")
        self.err(statement("ada", snapshot=tokn, offset=-1), 422, "validation_failed")

    def test_unknown_other_user_and_pre_reset(self):
        "[SN-04] unknown token, another user's token, a token from before reset -> 404 not_found"
        tokn = statement("ada").body["snapshot"]
        self.err(statement("ada", snapshot="no-such-token"), 404, "not_found")
        self.err(statement("ada", snapshot=tokn + "x"), 404, "not_found")
        self.err(statement("bob", snapshot=tokn), 404, "not_found")
        self.err(statement("eve", snapshot=tokn), 404, "not_found")
        self.assertEqual(statement("ada", snapshot=tokn).status, 200)
        reset(self.fx)
        self.err(statement("ada", snapshot=tokn), 404, "not_found")
        fresh_tok = statement("ada").body["snapshot"]
        self.assertNotEqual(fresh_tok, tokn)
        reset(self.fx)
        self.err(statement("ada", snapshot=fresh_tok), 404, "not_found")

    def test_snapshot_of_a_window(self):
        "[SN-05] the first read's from/to define the frozen window; later pages need no parameters; the half-open end holds"
        t = lambda h: iso6(self.now0 - timedelta(hours=h))
        first = statement("ada", **{"from": t(11), "to": t(3)}, limit=1).body
        self.assertEqual([e["payment"]["payment_id"] for e in first["entries"]], ["p_001"])
        self.assertTrue(first["has_more"])
        nxt = statement("ada", snapshot=first["snapshot"], limit=5, offset=1).body
        self.assertEqual([e["payment"]["payment_id"] for e in nxt["entries"]], ["p_003", "p_004"])
        self.assertFalse(nxt["has_more"])
        self.assertEqual((nxt["opening_balance"], nxt["closing_balance"]), (first["opening_balance"], first["closing_balance"]))
        self.assertEqual((first["opening_balance"], first["closing_balance"]), (10000, 8600))
        narrow = statement("ada", **{"from": t(11), "to": t(4)}).body  # p_004 sits exactly at `to`: out
        ids = [e["payment"]["payment_id"] for e in statement("ada", snapshot=narrow["snapshot"], limit=200).body["entries"]]
        self.assertEqual(ids, ["p_001", "p_003"])
        self.assertEqual(narrow["closing_balance"], 9600)

    def test_snapshot_is_per_read_and_immutable_object(self):
        "[SN-06] re-reading the same snapshot repeatedly yields identical bytes"
        tokn = statement("ada").body["snapshot"]
        a = statement("ada", snapshot=tokn, limit=4, offset=2)
        pay("ada", "bob", 1)
        b = statement("ada", snapshot=tokn, limit=4, offset=2)
        self.assertEqual(a.body, b.body)

    def test_snapshot_survives_a_correction_and_hold_lifecycle(self):
        "[SN-07] corrections and authorization lifecycle actions never alter an existing snapshot"
        pid = [e["payment"]["payment_id"] for e in all_statement("ada")[1] if e["payment"]["note"] == "s2"][0]
        tokn = statement("ada").body["snapshot"]
        before = statement("ada", snapshot=tokn, limit=200).body
        r = correct("ada", pid, 1, 1, ago(minutes=30))
        self.assertEqual(r.status, 201, r)
        a = authorize("ada", "cy", 100).body["authorization_id"]
        capture("cy", a, 40, final=False)
        void("ada", a)
        after = statement("ada", snapshot=tokn, limit=200).body
        self.assertEqual(before, after)
        fresh_b = statement("ada", limit=200).body
        self.assertNotEqual([entry_sig(e) for e in fresh_b["entries"]], [entry_sig(e) for e in before["entries"]])

    def test_concurrent_writes_do_not_disturb_paging(self):
        "[SN-08] while payments and corrections run concurrently, every page of an existing snapshot is the frozen one"
        tokn = statement("ada").body["snapshot"]
        frozen = statement("ada", snapshot=tokn, limit=200).body
        stop, bad = threading.Event(), []
        pids = [e["payment"]["payment_id"] for e in frozen["entries"] if e["payment"]["note"].startswith("s")]

        def writer(i):
            j = 0
            while not stop.is_set():
                safe_call("POST", "/payments", {"to_handle": "bob", "amount": 1}, token=tok("ada"), key=k())
                safe_call("POST", "/payments", {"to_handle": "ada", "amount": 1}, token=tok("bob"), key=k())
                if j % 3 == 0:
                    safe_call("POST", "/payments/%s/corrections" % pids[(i + j) % len(pids)], {"expected_revision": 1, "amount": 5, "effective_at": iso6(ago(minutes=5)),
                                                                                            "reason": "c"}, token=tok("ada"), headers={"Idempotency-Key": k()})
                j += 1

        ws = [threading.Thread(target=writer, args=(i,), daemon=True) for i in range(4)]
        for w in ws:
            w.start()
        for _ in range(25):
            for off in (0, 2, 5):
                r = safe_call("GET", "/statement?" + q(snapshot=tokn, limit=3, offset=off), token=tok("ada"))
                if r.status != 200:
                    bad.append(r)
                    continue
                if [entry_sig(e) for e in r.body["entries"]] != [entry_sig(e) for e in frozen["entries"][off:off + 3]]:
                    bad.append(("entries differ", off))
                if (r.body["opening_balance"], r.body["closing_balance"]) != (frozen["opening_balance"], frozen["closing_balance"]):
                    bad.append("balances differ")
        stop.set()
        for w in ws:
            w.join(timeout=10)
        self.assertEqual(bad, [])


if __name__ == "__main__":
    unittest.main()
