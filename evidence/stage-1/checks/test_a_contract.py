"""Runtime contract, reset, conventions, errors, auth, currencies. IDs in [brackets] map to coverage.md."""
import json
import unittest

from lib import *


class Health(Base):
    def test_health(self):
        "[HLT-01] GET /health -> 200 {status: ok}, no auth"
        r = call("GET", "/health")
        self.assertEqual(r.status, 200)
        self.assertEqual(r.body, {"status": "ok"})
        self.assertTrue(r.headers.get("content-type", "").lower().startswith("application/json"))

    def test_unknown_query_params_ignored(self):
        "[CNV-03] unknown query parameters are ignored"
        self.assertEqual(call("GET", "/health?x=1&limit=zzz").status, 200)
        self.assertEqual(call("GET", "/me?foo=bar", token=tok("ada")).status, 200)
        self.assertEqual(call("GET", "/activity?foo=bar&direction=zzz&status=zzz", token=tok("ada")).status, 200)
        self.assertEqual(call("GET", "/requests?foo=bar", token=tok("ada")).status, 200)


class Reset(Base):
    def test_reset_204_and_state_is_exactly_fixture(self):
        "[RST-01] reset returns 204 and later requests see only the fixture"
        r = call("POST", "/_test/reset", base_fixture(), timeout=10)
        self.assertEqual(r.status, 204)
        self.assertEqual(r.text, "")
        m = me("ada")
        self.assertEqual((m["user_id"], m["handle"], m["balance"], m["currency"], m["minor_units"]),
                         ("u_ada", "ada", 10000, "EUR", 2))
        self.assertEqual(m["display_name"], "Ada")
        self.assertEqual(total(), BASE_TOTAL)

    def test_reset_replaces_everything(self):
        "[RST-02] a second reset wipes users, tokens, payments, requests and idempotency keys"
        s = signup("temp.user@example.com")
        self.assertEqual(s.status, 201)
        old_token = tok("ada")
        key = k()
        self.assertEqual(pay("ada", "bob", 100, key=key).status, 201)
        reset(base_fixture(users=[user("ada", 700), user("zed", 5)], payments=[], requests=[], settlement_operator_ids=[]))
        self.assertEqual(call("GET", "/me", token=s.body["token"]).status, 401)
        self.assertEqual(call("GET", "/me", token=old_token).status, 401)
        self.assertEqual(call("POST", "/auth/login", {"email": "temp.user@example.com", "password": PW}).status, 401)
        self.assertEqual(call("POST", "/auth/login", {"email": "bob@example.com", "password": PW}).status, 401)
        self.assertEqual(bal("ada"), 700)
        self.assertEqual(activity("ada")["payments"], [])
        # same key in the new world is a first use (201), not a replay
        r = pay("ada", "zed", 100, key=key)
        self.assertEqual(r.status, 201)
        self.assertEqual(bal("ada"), 600)

    def test_reset_negative_balance_is_422_and_changes_nothing(self):
        "[RST-03] negative fixture balance -> 422 validation_failed, previous state untouched"
        self.assertEqual(pay("ada", "bob", 100).status, 201)
        before = (bal("ada"), bal("bob"))
        bad = base_fixture(users=[user("ada", 5), user("bob", -1)], payments=[], requests=[])
        r = call("POST", "/_test/reset", bad, timeout=10)
        self.err(r, 422, "validation_failed")
        self.assertEqual((bal("ada"), bal("bob")), before)
        self.assertEqual(call("GET", "/me", token=tok("ada")).status, 200)

    def test_reset_is_repeatable_and_needs_no_auth(self):
        "[RST-04] repeated resets supported; no authentication"
        for _ in range(5):
            self.assertEqual(call("POST", "/_test/reset", base_fixture(), timeout=10).status, 204)
        self.assertEqual(total(), BASE_TOTAL)

    def test_reset_unparseable_body_400(self):
        "[RST-05] malformed reset body follows section 5"
        r = call("POST", "/_test/reset", raw=b"{not json", timeout=10)
        self.err(r, 400, "malformed_request")
        self.assertEqual(bal("ada"), 10000)

    def test_reset_ignores_unknown_fields(self):
        "[RST-06] unknown fields in fixture ignored"
        fx = base_fixture(whatever={"x": 1})
        fx["users"][0]["favourite_colour"] = "blue"
        self.assertEqual(call("POST", "/_test/reset", fx, timeout=10).status, 204)

    def test_seeded_users_can_log_in_immediately_with_exact_state(self):
        "[RST-07] seeded users log in with their password; seeded payments/requests present with fixture ids"
        for n in BASE_NAMES:
            r = call("POST", "/auth/login", {"email": n + "@example.com", "password": PW})
            self.assertEqual(r.status, 200, r)
            self.assertEqual(r.body["user_id"], "u_" + n)
            self.assertEqual(r.body["display_name"], n.title())
            self.assertIsInstance(r.body["token"], str)
        ids = feed_ids("ada")
        self.assertIn("p_1", ids)
        self.assertIn("p_2", ids)
        self.assertNotIn("p_3", ids)  # private bob->cy, ada is a third party
        rq = {q["request_id"]: q for q in requests_of("ada")["requests"]}
        self.assertEqual(set(rq), {"rq_1", "rq_4", "rq_5"})
        self.request_shape(rq["rq_1"], requester="bob", payer="ada", amount=1200, status="pending", note="taxi")
        self.assertEqual(rq["rq_4"]["status"], "declined")
        self.assertEqual(rq["rq_5"]["status"], "cancelled")

    def test_balance_is_post_seed_and_not_replayed(self):
        "[RST-08] fixture balance is final; seeded payments are not replayed against it"
        self.assertEqual(bal("ada"), 10000)
        self.assertEqual(bal("bob"), 2500)
        self.assertEqual(bal("cy"), 1000)

    def test_reset_timing(self):
        "[LIM-03] reset completes well inside its 10 s budget"
        r = call("POST", "/_test/reset", base_fixture(), timeout=10)
        self.assertEqual(r.status, 204)
        self.assertLess(r.ms, 9000)


class Conventions(Base):
    def test_json_content_type_on_success_and_errors(self):
        "[CNV-01] responses are application/json; charset=utf-8"
        for r in (call("GET", "/me", token=tok("ada")), call("GET", "/me"),
                  call("POST", "/payments", {"to_handle": "bob", "amount": 1}, token=tok("ada"), key=k())):
            ct = r.headers.get("content-type", "").lower().replace(" ", "")
            self.assertTrue(ct.startswith("application/json"), r.headers)
            self.assertIn("charset=utf-8", ct)

    def test_error_body_on_unknown_route_and_method(self):
        "[ERR-01] every 4xx carries {error:{code,message}} (also unknown routes/methods)"
        for method, path in (("GET", "/nope"), ("DELETE", "/payments"), ("PUT", "/me"), ("GET", "/requests/rq_1/pay")):
            r = call(method, path, token=tok("ada"))
            self.err4xx(r)

    def test_401_variants(self):
        "[ERR-02] missing/malformed/unknown bearer token -> 401 unauthenticated on every authenticated endpoint"
        eps = [("GET", "/me", None), ("GET", "/activity", None), ("GET", "/requests", None),
               ("POST", "/payments", {"to_handle": "bob", "amount": 1}),
               ("POST", "/requests", {"payer_handle": "bob", "amount": 1}),
               ("POST", "/requests/rq_1/pay", {}), ("POST", "/requests/rq_1/decline", None),
               ("POST", "/requests/rq_1/cancel", None),
               ("POST", "/splits", {"amount": 1, "participant_handles": ["bob"]}),
               ("POST", "/settlements", {"transfers": [{"from_handle": "ada", "to_handle": "bob", "amount": 1}]})]
        for method, path, body in eps:
            for hdr in (None, {"Authorization": "Bearer"}, {"Authorization": "Bearer nonsense-token"},
                        {"Authorization": "Basic YWRhOnB3"}, {"Authorization": "Bearer "}):
                r = call(method, path, body, key=k(), headers=hdr)
                self.err(r, 401, "unauthenticated")

    def test_amount_integral_numeric_forms(self):
        "[MDL-01] 1000, 1000.0 and 1e3 are the same valid amount"
        for form in ("1000", "1000.0", "1e3", "1E3", "10e2"):
            reset()
            raw = '{"to_handle":"bob","amount":%s}' % form
            r = call("POST", "/payments", raw=raw, token=tok("ada"), key=k())
            self.assertEqual(r.status, 201, (form, r))
            self.assertEqual(r.body["amount"], 1000)
            self.is_int(r.body["amount"])
            self.assertEqual(bal("ada"), 9000)

    def test_amount_invalid_types_and_values(self):
        "[MDL-02] booleans, strings, fractions, out-of-range amounts are 422 validation_failed and move nothing"
        for form in ("true", "false", '"1000"', '"abc"', "1000.5", "0.5", "1e-1", "0", "0.0", "-1", "-0", "1000000001",
                     "1e10", "99999999999999999999999", "1e999", '""'):
            raw = '{"to_handle":"bob","amount":%s}' % form
            r = call("POST", "/payments", raw=raw, token=tok("ada"), key=k())
            self.err(r, 422, "validation_failed")
        self.assertEqual(bal("ada"), 10000)

    def test_amount_other_wrong_types_rejected_without_5xx(self):
        "[MDL-03] null/array/object amount is a 4xx (400 or 422), never 5xx"
        for form in ("null", "[]", "[1000]", "{}", '{"a":1}'):
            raw = '{"to_handle":"bob","amount":%s}' % form
            r = call("POST", "/payments", raw=raw, token=tok("ada"), key=k())
            self.err4xx(r, {"malformed_request", "validation_failed"})
        self.assertEqual(bal("ada"), 10000)

    def test_ids_are_opaque_strings_max_64(self):
        "[CNV-04] ids are strings of at most 64 characters"
        p = pay("ada", "bob", 1)
        q = req("bob", "ada", 5)
        self.assertEqual((p.status, q.status), (201, 201))
        for v in (p.body["payment_id"], q.body["request_id"], me("ada")["user_id"], tok("ada") and "x"):
            self.assertIsInstance(v, str)
            self.assertTrue(1 <= len(v) <= 64)
        s = signup(uniq_email())
        self.assertLessEqual(len(s.body["user_id"]), 64)

    def test_integers_are_serialised_as_integers(self):
        "[MDL-04] amounts/balances are JSON integers (no 1500.0, no strings)"
        pay("ada", "bob", 1500)
        self.is_int(me("ada")["balance"])
        self.is_int(me("ada")["minor_units"])
        for p in activity("ada")["payments"]:
            self.is_int(p["amount"])
        self.assertEqual(call("GET", "/me", token=tok("ada")).text.count("."), 0)


class Auth(Base):
    def test_signup_shape_and_token_works(self):
        "[AUT-01] signup 201 {user_id, display_name, token}; new user balance 0; token authenticates"
        r = signup("a.b@example.com", display_name="Ab")
        self.assertEqual(r.status, 201, r)
        self.assertEqual(set(["user_id", "display_name", "token"]) - set(r.body), set())
        self.assertEqual(r.body["display_name"], "Ab")
        m = call("GET", "/me", token=r.body["token"])
        self.assertEqual(m.status, 200)
        self.assertEqual(m.body["balance"], 0)
        self.assertEqual(m.body["user_id"], r.body["user_id"])
        self.assertEqual((m.body["currency"], m.body["minor_units"]), ("EUR", 2))

    def test_handle_derivation(self):
        "[AUT-02] handle = local part lowercased, chars outside [a-z0-9_] -> _, truncated to 20"
        cases = {
            "Ada.Lovelace+X@example.com": "ada_lovelace_x",
            "UPPER-Case9@example.com": "upper_case9",
            "x_y_1@example.com": "x_y_1",
            "a" * 30 + "@example.com": "a" * 20,
        }
        for email, handle in cases.items():
            r = signup(email)
            self.assertEqual(r.status, 201, (email, r))
            m = call("GET", "/me", token=r.body["token"]).body
            self.assertEqual(m["handle"], handle, email)
            self.assertRegex(m["handle"], r"^[a-z0-9_]{1,20}$")
        # derived handle is usable as a payment recipient
        self.assertEqual(pay("ada", "ada_lovelace_x", 10).status, 201)
        self.assertEqual(call("GET", "/me", token=signup("sign.ignores.handle@example.com", handle="zzz").body["token"]).body["handle"],
                         "sign_ignores_handle")  # 'handle' in body is an unknown field -> ignored

    def test_handle_truncation_then_collision(self):
        "[AUT-03] truncation happens before the taken-check: two long locals with equal first 20 chars collide"
        a = signup("abcdefghijklmnopqrst1@example.com")
        b = signup("abcdefghijklmnopqrst2@example.com")
        self.assertEqual(a.status, 201)
        self.err(b, 409, "handle_taken")

    def test_handle_taken_creates_no_account(self):
        "[AUT-04] derived handle taken (seeded 'ada') -> 409 handle_taken and no account"
        r = signup("ada@other.org")
        self.err(r, 409, "handle_taken")
        self.assertEqual(call("POST", "/auth/login", {"email": "ada@other.org", "password": PW}).status, 401)
        r2 = signup("ada@other.org")  # still no account => still handle_taken, not email_taken
        self.err(r2, 409, "handle_taken")
        self.assertEqual(bal("ada"), 10000)

    def test_email_taken(self):
        "[AUT-05] already registered email -> 409 email_taken"
        e = uniq_email()
        self.assertEqual(signup(e).status, 201)
        self.err(signup(e), 409, "email_taken")
        self.err(signup("bob@example.com"), 409, "email_taken")  # seeded email; handle 'bob' also taken - email wins (ambiguity A-03)

    def test_password_length(self):
        "[AUT-06] password shorter than 8 -> 422; 8 accepted"
        self.err(signup(uniq_email(), password="1234567"), 422, "validation_failed")
        self.err(signup(uniq_email(), password=""), 422, "validation_failed")
        self.assertEqual(signup(uniq_email(), password="12345678").status, 201)
        self.assertEqual(signup(uniq_email(), password="pässwörd").status, 201)

    def test_email_format(self):
        "[AUT-07] email not local@domain -> 422"
        for e in ("", "plain", "a@", "@b.com", "a b@c.com", "a@@b.com"):
            self.err(signup(e), 422, "validation_failed")

    def test_signup_missing_and_wrong_type_fields(self):
        "[AUT-08] missing field -> 422; wrong JSON type -> 400"
        self.err(call("POST", "/auth/signup", {"email": uniq_email(), "password": PW}), 422, "validation_failed")
        self.err(call("POST", "/auth/signup", {"password": PW, "display_name": "x"}), 422, "validation_failed")
        self.err(call("POST", "/auth/signup", {"email": uniq_email(), "display_name": "x"}), 422, "validation_failed")
        self.err(call("POST", "/auth/signup", {"email": 5, "password": PW, "display_name": "x"}), 400, "malformed_request")
        self.err(call("POST", "/auth/signup", {"email": uniq_email(), "password": 12345678, "display_name": "x"}), 400, "malformed_request")
        self.err(call("POST", "/auth/signup", raw=b"{oops"), 400, "malformed_request")

    def test_login_failures(self):
        "[AUT-09] wrong password / unknown email -> 401 unauthenticated"
        self.err(call("POST", "/auth/login", {"email": "ada@example.com", "password": "wrong password"}), 401, "unauthenticated")
        self.err(call("POST", "/auth/login", {"email": "nobody@example.com", "password": PW}), 401, "unauthenticated")
        self.err(call("POST", "/auth/login", {"email": "ada@example.com", "password": "correct horsE"}), 401, "unauthenticated")
        self.err(call("POST", "/auth/login", raw=b"[["), 400, "malformed_request")

    def test_login_after_signup_and_multiple_tokens(self):
        "[AUT-10] signup credentials log in; several tokens for one account are valid concurrently; tokens do not expire between uses"
        e = uniq_email()
        s = signup(e, password="a long password")
        l1 = call("POST", "/auth/login", {"email": e, "password": "a long password"})
        l2 = call("POST", "/auth/login", {"email": e, "password": "a long password"})
        self.assertEqual((l1.status, l2.status), (200, 200))
        self.assertEqual(l1.body["user_id"], s.body["user_id"])
        for t in (s.body["token"], l1.body["token"], l2.body["token"]):
            self.assertEqual(call("GET", "/me", token=t).status, 200)
        # issuing a new login does not invalidate earlier tokens (re-check the first after the others)
        self.assertEqual(call("GET", "/me", token=s.body["token"]).status, 200)

    def test_endpoints_exempt_from_auth(self):
        "[AUT-11] /health, /_test/reset, signup, login need no token"
        self.assertEqual(call("GET", "/health").status, 200)
        self.assertEqual(call("POST", "/_test/reset", base_fixture(), timeout=10).status, 204)
        self.assertEqual(signup(uniq_email()).status, 201)
        self.assertEqual(call("POST", "/auth/login", {"email": "ada@example.com", "password": PW}).status, 200)

    def test_unknown_signup_fields_ignored(self):
        "[CNV-02] unknown fields in request bodies are ignored, never an error"
        r = call("POST", "/auth/signup", {"email": uniq_email(), "password": PW, "display_name": "X", "role": "admin", "x": [1]})
        self.assertEqual(r.status, 201)
        r = call("POST", "/auth/login", {"email": "ada@example.com", "password": PW, "extra": 1})
        self.assertEqual(r.status, 200)

    def test_signup_cannot_become_operator(self):
        "[SET-14] signup with settlement/role fields does not grant operator rights"
        s = call("POST", "/auth/signup", {"email": uniq_email(), "password": PW, "display_name": "X",
                                          "is_operator": True, "role": "operator", "settlement_operator": True})
        r = settle(None, [{"from_handle": "ada", "to_handle": "bob", "amount": 1}], token=s.body["token"])
        self.err(r, 403, "forbidden")

    def test_concurrent_signup_same_email(self):
        "[AUT-12] concurrent signups of one email: exactly one 201, the rest 409 email_taken"
        e = uniq_email("race")
        rs = parallel([lambda: signup(e) for _ in range(12)])
        self.assertEqual(sorted(r.status for r in rs), [201] + [409] * 11, rs)
        self.assertTrue(all(r.code == "email_taken" for r in rs if r.status == 409), rs)

    def test_concurrent_signup_same_derived_handle(self):
        "[AUT-13] concurrent signups with different emails but one derived handle: exactly one account"
        local = "same.handle.%s" % uuid.uuid4().hex[:3]
        emails = ["%s@d%d.example.com" % (local, i) for i in range(10)]
        rs = parallel([lambda e=e: signup(e) for e in emails])
        self.assertEqual(sorted(r.status for r in rs), [201] + [409] * 9, rs)
        self.assertTrue(all(r.code == "handle_taken" for r in rs if r.status == 409), rs)

    def test_new_user_pays_and_receives(self):
        "[AUT-14] a signed-up user starts at 0, can receive immediately, then can spend it"
        s = signup("fresh.one@example.com")
        h = "fresh_one"
        self.assertEqual(pay("ada", h, 500).status, 201)
        m = call("GET", "/me", token=s.body["token"]).body
        self.assertEqual(m["balance"], 500)
        r = call("POST", "/payments", {"to_handle": "bob", "amount": 501}, token=s.body["token"], key=k())
        self.err(r, 409, "insufficient_funds")
        r = call("POST", "/payments", {"to_handle": "bob", "amount": 500}, token=s.body["token"], key=k())
        self.assertEqual(r.status, 201)
        self.assertEqual(total(BASE_NAMES), BASE_TOTAL)
        # can also be asked for money immediately
        self.assertEqual(req("bob", h, 10).status, 201)

    def test_export_has_no_plaintext_password(self):
        "[AUT-15] passwords are hashed: exported state never contains a plaintext password"
        e = uniq_email()
        pw = "Zq9-unmistakable-plaintext-%s" % uuid.uuid4().hex
        signup(e, password=pw)
        r = call("GET", "/_test/export", timeout=10)
        self.assertEqual(r.status, 200)
        self.assertNotIn(pw, r.text)
        self.assertNotIn("correct horse", r.text)

    def test_signup_cases_on_handle_collision_with_signup(self):
        "[AUT-16] a seeded handle cannot be taken over by signing up with a differently-cased email local part"
        self.err(signup("ADA@another.org"), 409, "handle_taken")
        self.assertEqual(me("ada")["user_id"], "u_ada")


class Currencies(Base):
    def _check(self, cur, mu):
        fx = base_fixture(currency=cur, minor_units=mu)
        reset(fx)
        m = me("ada")
        self.assertEqual((m["currency"], m["minor_units"], m["balance"]), (cur, mu, 10000))
        p = pay("ada", "bob", 1000)
        self.assertEqual(p.status, 201)
        self.assertEqual(p.body["currency"], cur)
        self.assertEqual(p.body["amount"], 1000)
        self.assertEqual(bal("ada"), 9000)
        self.assertEqual(bal("bob"), 3500)
        q = req("bob", "ada", 7)
        self.assertEqual(q.body["currency"], cur)
        self.assertEqual(q.body["amount"], 7)
        sp = call("POST", "/splits", {"amount": 10, "participant_handles": ["bob", "cy", "dee"]}, token=tok("bob"), key=k())
        self.assertEqual(sp.body["currency"], cur)
        self.assertEqual([s["amount"] for s in sp.body["shares"]], [4, 3, 3])
        # fraction of a minor unit is never valid, whatever the currency
        r = call("POST", "/payments", raw='{"to_handle":"bob","amount":10.5}', token=tok("ada"), key=k())
        self.err(r, 422, "validation_failed")

    def test_eur(self):
        "[MDL-05] EUR (2)"
        self._check("EUR", 2)

    def test_jpy(self):
        "[MDL-06] JPY (0)"
        self._check("JPY", 0)

    def test_bhd(self):
        "[MDL-07] BHD (3)"
        self._check("BHD", 3)


if __name__ == "__main__":
    unittest.main()
