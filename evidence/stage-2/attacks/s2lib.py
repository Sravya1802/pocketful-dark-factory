"""Shared helpers for the stage-2 attack scripts (stdlib only)."""
import json, time, threading, http.client, urllib.parse
from concurrent.futures import ThreadPoolExecutor
TL = threading.local(); RES = []
def raw(base, m, p, body=None, headers=None, token=None, rawbody=None, timeout=60):
    u = urllib.parse.urlparse(base); h = {"Content-Type": "application/json"}
    if token: h["Authorization"] = "Bearer " + token
    h.update(headers or {})
    data = rawbody if rawbody is not None else (json.dumps(body) if body is not None else None)
    if isinstance(data, str): data = data.encode()
    small = data is None or len(data) < 100000; t = time.time()
    for attempt in (0, 1):
        pl = getattr(TL, "p", None) or {}; TL.p = pl
        c = pl.get(base) if small else None
        if c is None: c = http.client.HTTPConnection(u.hostname, u.port, timeout=timeout)
        try:
            try: c.request(m, p, body=data, headers=h)
            except (BrokenPipeError, ConnectionResetError):
                if small: raise
            r = c.getresponse(); txt = r.read()
            if small and r.getheader("Connection", "").lower() != "close": pl[base] = c
            else: pl.pop(base, None); c.close()
            break
        except (http.client.HTTPException, ConnectionError, OSError):
            pl.pop(base, None)
            try: c.close()
            except Exception: pass
            if attempt or not small: raise
    try: j = json.loads(txt) if txt else None
    except Exception: j = None
    return r.status, j, txt, time.time() - t
def req(base, *a, **k): s, j, t, d = raw(base, *a, **k); return s, j
def check(n, ok, d=""):
    RES.append((n, bool(ok))); print(("PASS " if ok else "FAIL ") + n + ("" if ok else "  :: " + str(d)[:400]), flush=True)
def idem(k): return {"Idempotency-Key": k}
def code(j): return (j or {}).get("error", {}).get("code") if isinstance(j, dict) else None
def pool(fn, n, w=50):
    def safe(i):
        try: return fn(i)
        except Exception as e: return (repr(e)[:40], None, b"", 0.0)
    with ThreadPoolExecutor(w) as ex: return list(ex.map(safe, range(n)))
def fx(**kw):
    f = {"currency": "EUR", "minor_units": 2, "settlement_operator_ids": ["u_op"], "users": [
        {"id": "u_ada", "email": "ada@example.com", "password": "correct horse", "display_name": "Ada", "handle": "ada", "balance": 10000},
        {"id": "u_bob", "email": "bob@example.com", "password": "correct horse", "display_name": "Bob", "handle": "bob", "balance": 2500},
        {"id": "u_cy", "email": "cy@example.com", "password": "hunter2hunter2", "display_name": "Cy", "handle": "cy", "balance": 0},
        {"id": "u_op", "email": "op@example.com", "password": "correct horse", "display_name": "Op", "handle": "op", "balance": 100000}]}
    f.update(kw); return f
SEED_TOTAL = 112500
def reset(b, f=None):
    s, j = req(b, "POST", "/_test/reset", f or fx(), timeout=60); assert s == 204, (s, j)
def login(b, e, p="correct horse"):
    s, j = req(b, "POST", "/auth/login", {"email": e, "password": p}); assert s == 200, (s, j); return j["token"]
def toks(b): return {"ada": login(b, "ada@example.com"), "bob": login(b, "bob@example.com"), "cy": login(b, "cy@example.com", "hunter2hunter2"), "op": login(b, "op@example.com")}
def me(b, t): return req(b, "GET", "/me", token=t)[1]
def inv_ok(m): return m["balance"] == m["total"] and m["held"] >= 0 and m["available"] >= 0 and m["available"] == m["total"] - m["held"] and m["held"] <= m["total"]
def summary():
    bad = [r for r in RES if not r[1]]; print(f"\n{len(RES) - len(bad)} passed, {len(bad)} failed"); return 1 if bad else 0
