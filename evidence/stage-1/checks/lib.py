"""Black-box helpers for Pocketful stage-1 acceptance checks.

Only the HTTP interface is used. Standard library only (Python 3.9+).
Target service: env BASE_URL (default http://127.0.0.1:8080).
"""
import http.client
import json
import os
import re
import threading
import time
import unittest
import urllib.parse
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

BASE = os.environ.get("BASE_URL", "http://127.0.0.1:8080").rstrip("/")
_U = urllib.parse.urlparse(BASE)
PW = "correct horse"
TS_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:\d{2})$")


class R:
    def __init__(self, status, text, headers, ms, error=None):
        self.status, self.text, self.headers, self.ms, self.error = status, text, headers, ms, error
        try:
            self.body = json.loads(text) if text else None
        except ValueError:
            self.body = None

    def __repr__(self):
        return "<R %s %s %s>" % (self.status, self.text[:300], self.error or "")

    @property
    def code(self):
        b = self.body
        return b.get("error", {}).get("code") if isinstance(b, dict) and isinstance(b.get("error"), dict) else None


def call(method, path, body=None, token=None, key=None, headers=None, raw=None, timeout=5):
    """body: JSON-serialisable value (None = send no body). raw: bytes/str sent verbatim."""
    h = {"Connection": "close"}
    data = None
    if raw is not None:
        data = raw.encode() if isinstance(raw, str) else raw
    elif body is not None:
        data = json.dumps(body).encode()
    if data is not None:
        h["Content-Type"] = "application/json"
    if token:
        h["Authorization"] = "Bearer " + token
    if key is not None:
        h["Idempotency-Key"] = key
    if headers:
        h.update(headers)
    t0 = time.time()
    conn = http.client.HTTPConnection(_U.hostname, _U.port or 80, timeout=timeout)
    try:
        conn.request(method, path, body=data, headers=h)
        resp = conn.getresponse()
        text = resp.read().decode("utf-8", "replace")
        return R(resp.status, text, {k.lower(): v for k, v in resp.getheaders()}, (time.time() - t0) * 1000)
    finally:
        conn.close()


def safe_call(*a, **kw):
    try:
        return call(*a, **kw)
    except Exception as e:  # timeouts / resets are failures, but must not abort a whole batch
        return R(0, "", {}, 0, error="%s: %s" % (type(e).__name__, e))


def parallel(fns, barrier=True):
    """Run zero-arg callables concurrently (<=50 at once), starting together. Returns results in order."""
    n = len(fns)
    assert n <= 50
    bar = threading.Barrier(n) if barrier and n > 1 else None

    def wrap(f):
        def run():
            if bar:
                try:
                    bar.wait(timeout=10)
                except threading.BrokenBarrierError:
                    pass
            try:
                return f()
            except Exception as e:
                return R(0, "", {}, 0, error="%s: %s" % (type(e).__name__, e))
        return run

    with ThreadPoolExecutor(max_workers=n) as ex:
        futs = [ex.submit(wrap(f)) for f in fns]
        return [f.result() for f in futs]


# ---------------------------------------------------------------- fixtures
def user(name, balance=0, **kw):
    d = {"id": "u_" + name, "email": name + "@example.com", "password": PW,
         "display_name": name.title(), "handle": name, "balance": balance}
    d.update(kw)
    return d


def base_fixture(**over):
    """ada 10000, bob 2500, cy 1000, dee 0, eve 5000, op 0 (operator), op2 0 (operator). total 18500."""
    fx = {
        "currency": "EUR", "minor_units": 2,
        "users": [user("ada", 10000), user("bob", 2500), user("cy", 1000), user("dee", 0),
                  user("eve", 5000), user("op", 0), user("op2", 0)],
        "payments": [
            {"id": "p_1", "from_user_id": "u_ada", "to_user_id": "u_bob", "amount": 500, "note": "coffee", "visibility": "public"},
            {"id": "p_2", "from_user_id": "u_ada", "to_user_id": "u_cy", "amount": 300, "note": "secret", "visibility": "private"},
            {"id": "p_3", "from_user_id": "u_bob", "to_user_id": "u_cy", "amount": 200, "note": "", "visibility": "private"},
        ],
        "requests": [
            {"id": "rq_1", "requester_id": "u_bob", "payer_id": "u_ada", "amount": 1200, "note": "taxi", "status": "pending"},
            {"id": "rq_2", "requester_id": "u_cy", "payer_id": "u_eve", "amount": 100, "note": "", "status": "pending"},
            {"id": "rq_4", "requester_id": "u_dee", "payer_id": "u_ada", "amount": 70, "note": "", "status": "declined"},
            {"id": "rq_5", "requester_id": "u_dee", "payer_id": "u_ada", "amount": 80, "note": "", "status": "cancelled"},
        ],
        "settlement_operator_ids": ["u_op", "u_op2"],
    }
    fx.update(over)
    return fx


BASE_TOTAL = 18500
BASE_NAMES = ["ada", "bob", "cy", "dee", "eve", "op", "op2"]

_tokens = {}


def reset(fx=None, expect=204):
    r = call("POST", "/_test/reset", fx if fx is not None else base_fixture(), timeout=10)
    assert r.status == expect, "reset returned %s %s" % (r.status, r.text[:300])
    _tokens.clear()
    return r


def login(name, email=None):
    r = call("POST", "/auth/login", {"email": email or name + "@example.com", "password": PW})
    assert r.status == 200, "login %s -> %s %s" % (name, r.status, r.text[:200])
    return r.body["token"]


def tok(name):
    if name not in _tokens:
        _tokens[name] = login(name)
    return _tokens[name]


def me(name):
    r = call("GET", "/me", token=tok(name))
    assert r.status == 200, r
    return r.body


def bal(name):
    return me(name)["balance"]


def total(names=BASE_NAMES):
    return sum(bal(n) for n in names)


def k():
    return "k-" + uuid.uuid4().hex


def pay(frm, to, amount, key=None, token=None, **extra):
    body = {"to_handle": to, "amount": amount}
    body.update(extra)
    return call("POST", "/payments", body, token=token or tok(frm), key=key if key is not None else k())


def req(requester, payer, amount, key=None, **extra):
    body = {"payer_handle": payer, "amount": amount}
    body.update(extra)
    return call("POST", "/requests", body, token=tok(requester), key=key if key is not None else k())


def pay_req(payer, rid, key=None, body=None):
    return call("POST", "/requests/%s/pay" % rid, {} if body is None else body, token=tok(payer),
                key=key if key is not None else k())


def activity(name, **q):
    qs = ("?" + urllib.parse.urlencode(q)) if q else ""
    r = call("GET", "/activity" + qs, token=tok(name))
    assert r.status == 200, r
    return r.body


def feed_ids(name, **q):
    q.setdefault("limit", 200)
    return [p["payment_id"] for p in activity(name, **q)["payments"]]


def requests_of(name, **q):
    qs = ("?" + urllib.parse.urlencode(q)) if q else ""
    r = call("GET", "/requests" + qs, token=tok(name))
    assert r.status == 200, r
    return r.body


def settle(operator, transfers, key=None, token=None):
    return call("POST", "/settlements", {"transfers": transfers}, token=token or tok(operator),
                key=key if key is not None else k())


def signup(email, password=PW, display_name="Tester", **extra):
    b = {"email": email, "password": password, "display_name": display_name}
    b.update(extra)
    return call("POST", "/auth/signup", b)


def uniq_email(prefix="u"):
    return "%s%s@example.com" % (prefix, uuid.uuid4().hex[:10])


# ---------------------------------------------------------------- assertions
class Base(unittest.TestCase):
    maxDiff = None

    def setUp(self):
        reset()

    def err(self, r, status, code):
        self.assertEqual(r.status, status, "expected %s %s got %r" % (status, code, r))
        self.assertIsInstance(r.body, dict, r)
        e = r.body.get("error")
        self.assertIsInstance(e, dict, r)
        self.assertEqual(e.get("code"), code, r)
        self.assertIsInstance(e.get("message"), str, r)

    def err4xx(self, r, codes=None):
        self.assertTrue(400 <= r.status < 500, r)
        self.assertIsInstance(r.body, dict, r)
        self.assertIsInstance(r.body.get("error"), dict, r)
        self.assertIsInstance(r.body["error"].get("code"), str, r)
        if codes:
            self.assertIn(r.body["error"]["code"], codes, r)

    def is_int(self, v):
        self.assertTrue(isinstance(v, int) and not isinstance(v, bool), "not an integer: %r" % (v,))

    def ts(self, s):
        self.assertIsInstance(s, str)
        self.assertRegex(s, TS_RE)
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        self.assertIsNotNone(dt.tzinfo)
        self.assertLess(abs(dt.timestamp() - time.time()), 600, "timestamp far from now: " + s)
        return dt

    def payment_shape(self, p, frm=None, to=None, amount=None, note=None, vis=None, request_id=None,
                      currency="EUR", strict_settlement=False):
        for f in ("payment_id", "from_user_id", "from_handle", "to_user_id", "to_handle", "amount",
                  "currency", "note", "visibility", "request_id", "created_at"):
            self.assertIn(f, p, "payment lacks %s: %r" % (f, p))
        self.assertLessEqual(len(p["payment_id"]), 64)
        self.assertIsInstance(p["payment_id"], str)
        self.is_int(p["amount"])
        self.ts(p["created_at"])
        self.assertEqual(p["currency"], currency)
        if frm is not None:
            self.assertEqual((p["from_handle"], p["from_user_id"]), (frm, "u_" + frm))
        if to is not None:
            self.assertEqual((p["to_handle"], p["to_user_id"]), (to, "u_" + to))
        if amount is not None:
            self.assertEqual(p["amount"], amount)
        if note is not None:
            self.assertEqual(p["note"], note)
        if vis is not None:
            self.assertEqual(p["visibility"], vis)
        self.assertEqual(p["request_id"], request_id)
        if strict_settlement:
            self.assertIn("settlement_id", p)
            self.assertIsNone(p["settlement_id"])

    def request_shape(self, q, requester=None, payer=None, amount=None, status=None, note=None):
        for f in ("request_id", "requester_id", "requester_handle", "payer_id", "payer_handle", "amount",
                  "currency", "note", "status", "payment_id", "created_at"):
            self.assertIn(f, q, "request lacks %s: %r" % (f, q))
        self.assertNotIn("visibility", q, "a request carries no visibility of its own")
        self.assertLessEqual(len(q["request_id"]), 64)
        self.is_int(q["amount"])
        self.ts(q["created_at"])
        if requester:
            self.assertEqual((q["requester_handle"], q["requester_id"]), (requester, "u_" + requester))
        if payer:
            self.assertEqual((q["payer_handle"], q["payer_id"]), (payer, "u_" + payer))
        if amount is not None:
            self.assertEqual(q["amount"], amount)
        if status:
            self.assertEqual(q["status"], status)
        if note is not None:
            self.assertEqual(q["note"], note)
        if q["status"] == "paid":
            self.assertIsInstance(q["payment_id"], str)
        else:
            self.assertIsNone(q["payment_id"])
