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


def call(method, path, body=None, token=None, key=None, headers=None, raw=None, timeout=5, base=None):
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
    u = urllib.parse.urlparse(base.rstrip("/")) if base else _U
    conn = http.client.HTTPConnection(u.hostname, u.port or 80, timeout=timeout)
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
                      currency="EUR", strict_settlement=False, near_now=True):
        for f in ("payment_id", "from_user_id", "from_handle", "to_user_id", "to_handle", "amount",
                  "currency", "note", "visibility", "request_id", "created_at"):
            self.assertIn(f, p, "payment lacks %s: %r" % (f, p))
        self.assertLessEqual(len(p["payment_id"]), 64)
        self.assertIsInstance(p["payment_id"], str)
        self.is_int(p["amount"])
        if near_now:
            self.ts(p["created_at"])
        else:
            self.assertRegex(p["created_at"], TS_RE)
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


# ================================================================ stage 2 additions
from datetime import timedelta


def iso(ts):
    """epoch seconds -> RFC 3339 UTC string"""
    return datetime.fromtimestamp(ts, timezone.utc).isoformat(timespec="seconds")


def seed_auth(aid, frm, to, amount, status="open", expires_in=7200, note="", visibility="public", **kw):
    d = {"id": aid, "from_user_id": "u_" + frm, "to_user_id": "u_" + to, "amount": amount, "note": note,
         "visibility": visibility, "status": status, "expires_at": iso(time.time() + expires_in)}
    d.update(kw)
    return d


def azfixture(auths=None, ttl=None, **over):
    fx = base_fixture(**over)
    if auths is not None:
        fx["authorizations"] = auths
    if ttl is not None:
        fx["authorization_ttl_seconds"] = ttl
    return fx


def authorize(frm, to, amount, key=None, **extra):
    body = {"to_handle": to, "amount": amount}
    body.update(extra)
    return call("POST", "/authorizations", body, token=tok(frm), key=key if key is not None else k())


def capture(who, aid, amount=None, key=None, body=None, **extra):
    if body is None:
        body = {}
        if amount is not None:
            body["amount"] = amount
        body.update(extra)
    return call("POST", "/authorizations/%s/capture" % aid, body, token=tok(who), key=key if key is not None else k())


def void(who, aid):
    return call("POST", "/authorizations/%s/void" % aid, token=tok(who))


def auths_of(name, **q):
    qs = ("?" + urllib.parse.urlencode(q)) if q else ""
    r = call("GET", "/authorizations" + qs, token=tok(name), headers={"Accept": "application/json"})
    assert r.status == 200, r
    return r.body


def az_get(name, aid):
    for a in auths_of(name, limit=200)["authorizations"]:
        if a["authorization_id"] == aid:
            return a
    raise AssertionError("authorization %s not visible to %s" % (aid, name))


def parse_ts(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


class AzBase(Base):
    """stage-2 base: reset to the base fixture, plus authorization-shape helpers"""

    def az_shape(self, a, frm=None, to=None, amount=None, status=None, captured=None, remaining=None, note=None, vis=None):
        for f in ("authorization_id", "from_user_id", "from_handle", "to_user_id", "to_handle", "amount", "captured_amount",
                  "remaining_amount", "currency", "note", "visibility", "status", "expires_at", "payment_id", "payment_ids", "created_at"):
            self.assertIn(f, a, "authorization lacks %s: %r" % (f, a))
        self.assertIsInstance(a["authorization_id"], str)
        self.assertLessEqual(len(a["authorization_id"]), 64)
        for f in ("amount", "captured_amount", "remaining_amount"):
            self.is_int(a[f])
        self.assertIsInstance(a["payment_ids"], list)
        self.ts(a["created_at"])
        self.assertRegex(a["expires_at"], TS_RE)
        self.assertEqual(a["currency"], "EUR")
        if frm:
            self.assertEqual((a["from_handle"], a["from_user_id"]), (frm, "u_" + frm))
        if to:
            self.assertEqual((a["to_handle"], a["to_user_id"]), (to, "u_" + to))
        if amount is not None:
            self.assertEqual(a["amount"], amount)
        if status:
            self.assertEqual(a["status"], status)
        if captured is not None:
            self.assertEqual(a["captured_amount"], captured)
        if remaining is not None:
            self.assertEqual(a["remaining_amount"], remaining)
        if note is not None:
            self.assertEqual(a["note"], note)
        if vis:
            self.assertEqual(a["visibility"], vis)
        if a["payment_ids"]:
            self.assertEqual(a["payment_id"], a["payment_ids"][-1])
        else:
            self.assertIsNone(a["payment_id"])
        if a["status"] != "open":
            self.assertEqual(a["remaining_amount"], 0)

    def me_inv(self, name, held=None, available=None, total_=None):
        m = me(name)
        self.is_int(m["total"])
        self.assertEqual(m["balance"], m["total"])
        self.assertEqual(m["available"], m["total"] - m["held"], m)
        self.assertGreaterEqual(m["available"], 0, m)
        self.assertGreaterEqual(m["held"], 0, m)
        if held is not None:
            self.assertEqual(m["held"], held, m)
        if available is not None:
            self.assertEqual(m["available"], available, m)
        if total_ is not None:
            self.assertEqual(m["total"], total_, m)
        return m


# ================================================================ stage 3 additions
UTC = timezone.utc
EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
INF = datetime(9999, 1, 1, tzinfo=UTC)
TS_STRICT = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:\d{2})$")


def dt(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(UTC)


def iso6(d):
    """aware datetime -> RFC 3339 with microseconds and +00:00"""
    return d.astimezone(UTC).isoformat(timespec="microseconds")


def now_utc():
    return datetime.now(UTC)


def ago(**kw):
    return now_utc() - timedelta(**kw)


def seed_pay(pid, frm, to, amount, created=None, note="", visibility="public", **kw):
    d = {"id": pid, "from_user_id": "u_" + frm, "to_user_id": "u_" + to, "amount": amount, "note": note, "visibility": visibility}
    if created is not None:
        d["created_at"] = created if isinstance(created, str) else iso6(created)
    d.update(kw)
    return d


def q(**kw):
    return urllib.parse.urlencode({k: v for k, v in kw.items() if v is not None})


def me_at(name, as_of=None, known_at=None, **extra):
    qs = q(as_of=as_of, known_at=known_at, **extra)
    return call("GET", "/me" + ("?" + qs if qs else ""), token=tok(name))


def statement(name, **kw):
    qs = q(**kw)
    return call("GET", "/statement" + ("?" + qs if qs else ""), token=tok(name))


def correct(who, pid, expected_revision, amount, effective_at, reason="fix", key=None, **extra):
    body = {"expected_revision": expected_revision, "amount": amount,
            "effective_at": effective_at if isinstance(effective_at, str) else iso6(effective_at), "reason": reason}
    body.update(extra)
    return call("POST", "/payments/%s/corrections" % pid, body, token=tok(who), key=key if key is not None else k())


def revisions(who, pid):
    return call("GET", "/payments/%s/revisions" % pid, token=tok(who))


def all_statement(name, **kw):
    """follow pages until exhausted; returns (first_response_body, entries)"""
    first = statement(name, limit=200, **kw)
    assert first.status == 200, first
    entries, off, body = [], 0, first.body
    while True:
        entries += body["entries"]
        if not body["has_more"]:
            break
        off += 200
        body = statement(name, limit=200, offset=off, **kw).body
    return first.body, entries


from oracle import Ledger, Hold  # noqa: E402

OPEN0 = {"ada": 10000, "bob": 2000, "cy": 1000, "dee": 0, "eve": 5000, "op": 0, "op2": 0}


def hist(specs, openings=None, now0=None, **over):
    """Build (fixture, ledger, now0) from payment specs [(pid, from, to, amount, hours_ago)] and opening balances.
    Fixture balances are the ENDING balances after all seeded payments, as the spec defines them."""
    now0 = now0 or now_utc()
    op = dict(OPEN0)
    op.update(openings or {})
    led = Ledger({"u_" + n: v for n, v in op.items()})
    pays = []
    end = dict(op)
    for pid, f, t, a, hrs in specs:
        c = now0 - timedelta(hours=hrs)
        pays.append(seed_pay(pid, f, t, a, c))
        led.add_payment(pid, "u_" + f, "u_" + t, a, c)
        end[f] -= a
        end[t] += a
    users = [user(n, end[n]) for n in op]
    fx = base_fixture(users=users, payments=pays, requests=[], **over)
    return fx, led, now0


H_SPECS = [("p_001", "ada", "bob", 500, 10), ("p_002", "bob", "cy", 200, 8), ("p_003", "cy", "ada", 100, 6), ("p_004", "ada", "dee", 1000, 4)]
H_TOTAL = sum(OPEN0.values())
