"""Stage 2: content negotiation, seven-path idempotency, export/import (stage 1 -> 2 and stage 2 -> 2), static no-network scan."""
import json
import os
import re
import unittest
import urllib.parse

from lib import *

BROWSER_ACCEPT = "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8"
STAGE1_URL = os.environ.get("STAGE1_URL")


def T(f, t, a, **kw):
    d = {"from_handle": f, "to_handle": t, "amount": a}
    d.update(kw)
    return d


class Negotiation(AzBase):
    def test_html_for_ui_routes(self):
        "[NEG-01] the five UI routes plus /authorizations are reachable by URL and return HTML (no token needed to load the page)"
        for path in ("/", "/requests", "/split", "/signup", "/login", "/authorizations"):
            r = call("GET", path, headers={"Accept": BROWSER_ACCEPT})
            self.assertEqual(r.status, 200, (path, r.text[:200]))
            self.assertTrue(r.headers.get("content-type", "").lower().startswith("text/html"), (path, r.headers))
            self.assertRegex(r.text.lower(), r"<!doctype html|<html", path)

    def test_json_for_api_clients_on_shared_routes(self):
        "[NEG-02] GET /requests and /authorizations without text/html in Accept return JSON (no header, application/json, */*)"
        t = tok("ada")
        for path, key in (("/requests", "requests"), ("/authorizations", "authorizations")):
            for hdr in (None, {"Accept": "application/json"}, {"Accept": "*/*"}):
                r = call("GET", path, token=t, headers=hdr)
                self.assertEqual(r.status, 200, (path, hdr, r.text[:200]))
                self.assertTrue(r.headers.get("content-type", "").lower().startswith("application/json"), (path, hdr, r.headers))
                self.assertIn(key, r.body)
                self.assertIn("has_more", r.body)
            self.err(call("GET", path, headers={"Accept": "application/json"}), 401, "unauthenticated")

    def test_html_negotiation_with_bearer_still_html(self):
        "[NEG-03] an Accept: text/html request is the UI even when a bearer token is present; query params do not break it"
        for path in ("/requests", "/authorizations"):
            r = call("GET", path + "?direction=incoming", token=tok("ada"), headers={"Accept": BROWSER_ACCEPT})
            self.assertEqual(r.status, 200)
            self.assertTrue(r.headers.get("content-type", "").lower().startswith("text/html"))

    def test_writes_stay_json(self):
        "[NEG-04] POST /requests and POST /authorizations return JSON whatever the Accept header says"
        r = call("POST", "/requests", {"payer_handle": "bob", "amount": 5}, token=tok("ada"), key=k(), headers={"Accept": BROWSER_ACCEPT})
        self.assertEqual(r.status, 201)
        self.assertIsInstance(r.body, dict)
        r = call("POST", "/authorizations", {"to_handle": "bob", "amount": 5}, token=tok("ada"), key=k(), headers={"Accept": "text/html"})
        self.assertEqual(r.status, 201)
        self.assertIsInstance(r.body, dict)

    def test_pages_have_no_external_resources(self):
        "[NEG-05] served pages and their same-origin assets reference no external origin (no CDN fonts/scripts/images at run time)"
        seen, bad = set(), []
        base = urllib.parse.urlparse(BASE)

        def scan(url_path, depth=0):
            if url_path in seen or depth > 2:
                return
            seen.add(url_path)
            r = call("GET", url_path, headers={"Accept": BROWSER_ACCEPT})
            if r.status != 200:
                return
            text = r.text
            for m in re.finditer(r"""(?:src|href|action|poster)\s*=\s*["']\s*((?:https?:)?//[^"'\s>]+)""", text, re.I):
                if urllib.parse.urlparse(m.group(1)).netloc not in ("", base.netloc):
                    bad.append((url_path, m.group(1)))
            for m in re.finditer(r"""url\(\s*["']?((?:https?:)?//[^)"'\s]+)""", text, re.I):
                if urllib.parse.urlparse(m.group(1)).netloc not in ("", base.netloc):
                    bad.append((url_path, m.group(1)))
            for m in re.finditer(r"""@import\s+["']((?:https?:)?//[^"']+)""", text, re.I):
                bad.append((url_path, m.group(1)))
            for m in re.finditer(r"(?:fonts\.googleapis|fonts\.gstatic|cdn\.jsdelivr|unpkg\.com|cdnjs\.cloudflare|ajax\.googleapis|use\.typekit|bootstrapcdn)", text, re.I):
                bad.append((url_path, m.group(0)))
            for m in re.finditer(r"""(?:src|href)\s*=\s*["'](/[^"'#?]+\.(?:js|css|mjs))""", text, re.I):
                scan(m.group(1), depth + 1)
            for m in re.finditer(r"""url\(\s*["']?(/[^)"'\s]+)""", text, re.I):
                scan(m.group(1), depth + 1)

        for p in ("/", "/requests", "/split", "/signup", "/login", "/authorizations"):
            scan(p)
        self.assertEqual(bad, [])

    def test_seven_write_paths_need_a_key(self):
        "[NEG-06] all seven idempotent write paths: missing or empty key -> 400 missing_idempotency_key; no effect"
        aid = authorize("ada", "bob", 10).body["authorization_id"]
        t, tb, top = tok("ada"), tok("bob"), tok("op")
        ops = [("/payments", {"to_handle": "bob", "amount": 5}, t), ("/requests", {"payer_handle": "bob", "amount": 5}, t),
               ("/requests/rq_1/pay", {}, t), ("/splits", {"amount": 9, "participant_handles": ["bob"]}, t),
               ("/settlements", {"transfers": [T("ada", "bob", 1)]}, top), ("/authorizations", {"to_handle": "bob", "amount": 5}, t),
               ("/authorizations/%s/capture" % aid, {}, tb)]
        for path, body, token in ops:
            self.err(call("POST", path, body, token=token), 400, "missing_idempotency_key")
            self.err(call("POST", path, body, token=token, key=""), 400, "missing_idempotency_key")
        self.assertEqual((bal("ada"), bal("bob")), (10000, 2500))
        self.me_inv("ada", held=10)

    def test_seven_paths_replay_independently(self):
        "[NEG-07] one key string used on all seven paths by one user: every path treats it as a first use, then replays independently"
        key = k()
        t, tb, top = tok("ada"), tok("bob"), tok("op")
        aid = authorize("ada", "cy", 10).body["authorization_id"]
        first = [call("POST", "/payments", {"to_handle": "bob", "amount": 5}, token=t, key=key),
                 call("POST", "/requests", {"payer_handle": "bob", "amount": 5}, token=t, key=key),
                 call("POST", "/requests/rq_1/pay", {}, token=t, key=key),
                 call("POST", "/splits", {"amount": 9, "participant_handles": ["bob"]}, token=t, key=key),
                 call("POST", "/authorizations", {"to_handle": "bob", "amount": 5}, token=t, key=key)]
        self.assertEqual([r.status for r in first], [201] * 5, first)
        op = call("POST", "/settlements", {"transfers": [T("ada", "bob", 1)]}, token=top, key=key)
        cap = call("POST", "/authorizations/%s/capture" % aid, {}, token=tok("cy"), key=key)
        self.assertEqual((op.status, cap.status), (201, 201))
        again = [call("POST", "/payments", {"to_handle": "bob", "amount": 5}, token=t, key=key),
                 call("POST", "/requests", {"payer_handle": "bob", "amount": 5}, token=t, key=key),
                 call("POST", "/authorizations", {"to_handle": "bob", "amount": 5}, token=t, key=key)]
        self.assertEqual([r.status for r in again], [200] * 3)
        self.assertEqual([r.body for r in again], [first[0].body, first[1].body, first[4].body])


class UpgradeStage2Roundtrip(AzBase):
    def test_roundtrip_with_holds(self):
        "[UPG-01] stage-2 export/import preserves holds, partial captures, expiry instants, tokens and replay of authorize/capture keys"
        k1, k2, k3 = k(), k(), k()
        a1 = call("POST", "/authorizations", {"to_handle": "bob", "amount": 2000, "note": "d"}, token=tok("ada"), key=k1)
        a2 = authorize("ada", "cy", 1000)
        c1 = capture("bob", a1.body["authorization_id"], 500, key=k2, final=False)
        void("ada", a2.body["authorization_id"])
        a3 = call("POST", "/authorizations", {"to_handle": "eve", "amount": 300}, token=tok("ada"), key=k3)
        before = {n: (me(n), auths_of(n, limit=200), activity(n, limit=200), requests_of(n, limit=200)) for n in BASE_NAMES}
        exp = call("GET", "/_test/export", timeout=10).body
        capture("bob", a1.body["authorization_id"], 100, final=False)  # post-export mutation
        void("ada", a3.body["authorization_id"])
        authorize("ada", "bob", 700)
        self.assertEqual(call("POST", "/_test/import", exp, timeout=10).status, 204)
        after = {n: (me(n), auths_of(n, limit=200), activity(n, limit=200), requests_of(n, limit=200)) for n in BASE_NAMES}
        self.assertEqual(after, before)
        r = call("POST", "/authorizations", {"to_handle": "bob", "amount": 2000, "note": "d"}, token=tok("ada"), key=k1)
        self.assertEqual((r.status, r.body), (200, a1.body))
        r = capture("bob", a1.body["authorization_id"], 500, key=k2, final=False)
        self.assertEqual((r.status, r.body), (200, c1.body))
        self.me_inv("ada", held=1500 + 300)
        # the world is live: remaining hold can still be captured, the post-export key is a first use again
        self.assertEqual(capture("bob", a1.body["authorization_id"], 1500).status, 201)
        self.assertEqual(void("ada", a3.body["authorization_id"]).status, 200)

    def test_expiry_survives_import(self):
        "[UPG-02] a hold that expires after import still expires on the clock; its capture records survive"
        reset(azfixture(ttl=3))
        aid = authorize("ada", "bob", 1000).body["authorization_id"]
        capture("bob", aid, 200, final=False)
        exp = call("GET", "/_test/export", timeout=10).body
        self.assertEqual(call("POST", "/_test/import", exp, timeout=10).status, 204)
        row = az_get("ada", aid)
        self.assertEqual((row["status"], row["captured_amount"], row["remaining_amount"]), ("open", 200, 800))
        time.sleep(3.6)
        row = az_get("ada", aid)
        self.assertEqual((row["status"], row["captured_amount"], row["remaining_amount"]), ("expired", 200, 0))
        self.me_inv("ada", held=0, available=9800, total_=9800)

    def test_expired_while_exported(self):
        "[UPG-03] a hold already expired by the clock at import time is imported as expired, not resurrected"
        reset(azfixture(ttl=2))
        aid = authorize("ada", "bob", 1000).body["authorization_id"]
        exp = call("GET", "/_test/export", timeout=10).body
        time.sleep(3.2)
        self.assertEqual(call("POST", "/_test/import", exp, timeout=10).status, 204)
        self.assertEqual(az_get("ada", aid)["status"], "expired")
        self.me_inv("ada", held=0, available=10000)

    def test_import_still_rejects_invalid(self):
        "[UPG-04] stage-1 import validation still holds (wrong track/version -> 422, nothing changed)"
        good = call("GET", "/_test/export", timeout=10).body
        authorize("ada", "bob", 100)
        for bad in ({**good, "track": "x"}, {**good, "format_version": 2}, {**good, "state": None}, {}):
            self.err(call("POST", "/_test/import", bad, timeout=10), 422, "validation_failed")
        self.me_inv("ada", held=100)


@unittest.skipUnless(STAGE1_URL, "set STAGE1_URL=http://host:port to a running STAGE-1 service to run the upgrade-by-import checks")
class UpgradeFromStage1(unittest.TestCase):
    def s1(self, method, path, body=None, token=None, key=None, **kw):
        return call(method, path, body, token=token, key=key, base=STAGE1_URL, timeout=10, **kw)

    def setUp(self):
        r = call("POST", "/_test/reset", base_fixture(), timeout=10, base=STAGE1_URL)
        self.assertEqual(r.status, 204, r)
        self.s1_tok = {}
        for n in ("ada", "bob", "cy", "op"):
            self.s1_tok[n] = self.s1("POST", "/auth/login", {"email": n + "@example.com", "password": PW}).body["token"]

    def test_stage1_export_imports_into_stage2(self):
        "[UPG-10] a stage-1 export imports into stage 2: tokens, balances (available == total, held 0), replays, pending requests, failed keys, operators"
        t = self.s1_tok
        pk, fk, sk = k(), k(), k()
        paid = self.s1("POST", "/payments", {"to_handle": "bob", "amount": 1234, "note": "x"}, token=t["ada"], key=pk)
        failed = self.s1("POST", "/payments", {"to_handle": "bob", "amount": 10**8}, token=t["ada"], key=fk)
        pending = self.s1("POST", "/requests", {"payer_handle": "ada", "amount": 400, "note": "taxi"}, token=t["bob"], key=k())
        settled = self.s1("POST", "/settlements", {"transfers": [T("ada", "cy", 50)]}, token=t["op"], key=sk)
        signed = self.s1("POST", "/auth/signup", {"email": "upg.user@example.com", "password": "long passphrase", "display_name": "Upg"})
        self.assertEqual((paid.status, failed.status, pending.status, settled.status, signed.status), (201, 409, 201, 201, 201))
        feed_before = self.s1("GET", "/activity?limit=200", token=t["ada"]).body
        exp = self.s1("GET", "/_test/export")
        self.assertEqual(exp.status, 200)
        r = call("POST", "/_test/import", exp.body, timeout=10)
        self.assertEqual(r.status, 204, r)
        # sessions survive
        m = call("GET", "/me", token=t["ada"])
        self.assertEqual(m.status, 200, m)
        self.assertEqual((m.body["balance"], m.body["total"], m.body["available"], m.body["held"]), (8716, 8716, 8716, 0))
        self.assertEqual(call("GET", "/me", token=signed.body["token"]).status, 200)
        self.assertEqual(call("POST", "/auth/login", {"email": "upg.user@example.com", "password": "long passphrase"}).status, 200)
        # feed identical (ids, timestamps)
        after = call("GET", "/activity?limit=200", token=t["ada"]).body["payments"]
        for p in after:  # stage 2 adds authorization_id (null for pre-existing payments); everything else must be identical
            self.assertIsNone(p.pop("authorization_id", "absent-ok") if "authorization_id" in p else None)
        self.assertEqual(after, feed_before["payments"])
        # retries after the upgrade replay the original responses
        again = call("POST", "/payments", {"to_handle": "bob", "amount": 1234, "note": "x"}, token=t["ada"], key=pk)
        self.assertEqual((again.status, again.body), (200, paid.body))
        s2 = call("POST", "/settlements", {"transfers": [T("ada", "cy", 50)]}, token=t["op"], key=sk)
        self.assertEqual((s2.status, s2.body), (200, settled.body))
        self.assertEqual(call("GET", "/me", token=t["ada"]).body["balance"], 8716)
        # a failed key is still reusable
        self.assertEqual(call("POST", "/payments", {"to_handle": "bob", "amount": 10}, token=t["ada"], key=fk).status, 201)
        # the pending request is payable, once
        rid = pending.body["request_id"]
        pr = call("POST", "/requests/%s/pay" % rid, {}, token=t["ada"], key=k())
        self.assertEqual(pr.status, 201, pr)
        self.assertIsNone(pr.body["authorization_id"])
        self.assertEqual(call("POST", "/requests/%s/pay" % rid, {}, token=t["ada"], key=k()).status, 409)  # once only
        # stage-2 features work on the upgraded accounts
        a = call("POST", "/authorizations", {"to_handle": "cy", "amount": 1000}, token=t["ada"], key=k())
        self.assertEqual(a.status, 201, a)
        m = call("GET", "/me", token=t["ada"]).body
        self.assertEqual(m["held"], 1000)
        self.assertEqual(m["available"], m["total"] - 1000)
        c = call("POST", "/authorizations/%s/capture" % a.body["authorization_id"], {}, token=t["cy"], key=k())
        self.assertEqual(c.status, 201, c)
        # operators keep their rights
        self.assertEqual(call("POST", "/settlements", {"transfers": [T("ada", "bob", 1)]}, token=t["op"], key=k()).status, 201)
        total_ = sum(call("GET", "/me", token=call("POST", "/auth/login", {"email": n + "@example.com", "password": PW}).body["token"]).body["total"]
                     for n in BASE_NAMES)
        self.assertEqual(total_, BASE_TOTAL + 0)

    def test_lost_response_committed_before_export_is_retryable(self):
        "[UPG-11] a payment whose response was lost (committed in stage 1) retries after import with the same key and body -> 200 original, money once"
        t = self.s1_tok
        key = k()
        orig = self.s1("POST", "/payments", {"to_handle": "bob", "amount": 777, "note": "lost"}, token=t["ada"], key=key)
        self.assertEqual(orig.status, 201)
        exp = self.s1("GET", "/_test/export")
        self.assertEqual(call("POST", "/_test/import", exp.body, timeout=10).status, 204)
        r = call("POST", "/payments", {"to_handle": "bob", "amount": 777, "note": "lost"}, token=t["ada"], key=key)
        self.assertEqual((r.status, r.body), (200, orig.body))
        self.assertEqual(call("GET", "/me", token=t["ada"]).body["balance"], 10000 - 777)

    def test_lost_response_never_committed_before_export(self):
        "[UPG-12] a payment that never committed before the export is created exactly once by the retry after import"
        t = self.s1_tok
        exp = self.s1("GET", "/_test/export")
        self.assertEqual(call("POST", "/_test/import", exp.body, timeout=10).status, 204)
        key = k()
        a = call("POST", "/payments", {"to_handle": "bob", "amount": 321}, token=t["ada"], key=key)
        b = call("POST", "/payments", {"to_handle": "bob", "amount": 321}, token=t["ada"], key=key)
        self.assertEqual((a.status, b.status), (201, 200))
        self.assertEqual(call("GET", "/me", token=t["ada"]).body["balance"], 10000 - 321)


if __name__ == "__main__":
    unittest.main()
