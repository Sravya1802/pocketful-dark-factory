#!/usr/bin/env python3
"""Export from process A (new revision), import into a DIFFERENT process B (also new revision), then log in as seeded and signup
users, use old tokens, replay keys. Spec §10: no dependency on the source process. usage: cross_process_import.py URL_A URL_B (RESETS both)"""
import sys, json, http.client, urllib.parse
def call(base, m, p, body=None, h=None):
    u = urllib.parse.urlparse(base); c = http.client.HTTPConnection(u.hostname, u.port, timeout=30)
    c.request(m, p, body=json.dumps(body) if body is not None else None, headers=dict({"Content-Type": "application/json"}, **(h or {})))
    r = c.getresponse(); t = r.read(); c.close()
    try: return r.status, json.loads(t) if t else None
    except Exception: return r.status, t
A, B = sys.argv[1], sys.argv[2]; fails = []
def chk(n, ok, d=""):
    print(("PASS " if ok else "FAIL ") + n + ("" if ok else " :: " + str(d)[:200])); ok or fails.append(n)
fx = {"currency": "EUR", "minor_units": 2, "settlement_operator_ids": ["u_op"], "users": [
    {"id": "u_ada", "email": "ada@example.com", "password": "correct horse", "display_name": "Ada", "handle": "ada", "balance": 10000},
    {"id": "u_bob", "email": "bob@example.com", "password": "other pass word", "display_name": "Bob", "handle": "bob", "balance": 2500},
    {"id": "u_op", "email": "op@example.com", "password": "correct horse", "display_name": "Op", "handle": "op", "balance": 500}]}
assert call(A, "POST", "/_test/reset", fx)[0] == 204
assert call(B, "POST", "/_test/reset", {"currency": "USD", "minor_units": 2, "users": []})[0] == 204
tok = call(A, "POST", "/auth/login", {"email": "ada@example.com", "password": "correct horse"})[1]["token"]
s, pay = call(A, "POST", "/payments", {"to_handle": "bob", "amount": 100}, {"Authorization": "Bearer " + tok, "Idempotency-Key": "k1"})
s, su = call(A, "POST", "/auth/signup", {"email": "new@x.com", "password": "newnewnew", "display_name": "N"})
s, ex = call(A, "GET", "/_test/export")
print("   seeded hash format:", [u["password_hash"][:28] for u in ex["state"]["users"]][:3])
chk("import A-export into process B -> 204", call(B, "POST", "/_test/import", ex)[0] == 204)
for email, pw in (("ada@example.com", "correct horse"), ("bob@example.com", "other pass word"), ("op@example.com", "correct horse"), ("new@x.com", "newnewnew")):
    chk(f"B: login {email}", call(B, "POST", "/auth/login", {"email": email, "password": pw})[0] == 200)
chk("B: wrong password rejected", call(B, "POST", "/auth/login", {"email": "ada@example.com", "password": "Correct horse"})[0] == 401)
chk("B: A's bearer tokens valid", call(B, "GET", "/me", None, {"Authorization": "Bearer " + tok})[0] == 200 and call(B, "GET", "/me", None, {"Authorization": "Bearer " + su["token"]})[0] == 200)
chk("B: replay of A's payment 200 identical", call(B, "POST", "/payments", {"to_handle": "bob", "amount": 100}, {"Authorization": "Bearer " + tok, "Idempotency-Key": "k1"}) == (200, pay))
eb = call(B, "GET", "/_test/export")[1]
strip = lambda e: {k: v for k, v in e["state"].items() if k != "tokens"}
chk("B: re-export equals A's export (ignoring tokens minted by the logins above)", strip(eb) == strip(ex) and all(t in eb["state"]["tokens"] for t in ex["state"]["tokens"]))
print("FAILED" if fails else "ALL PASS"); sys.exit(1 if fails else 0)
