"""Shared Playwright helpers for the stage-2 UI attacks. Run with the harness venv python."""
import json, sys, time, urllib.request, re
from playwright.sync_api import sync_playwright
RES = []
def check(n, ok, d=""):
    RES.append((n, bool(ok))); print(("PASS " if ok else "FAIL ") + n + ("" if ok else "  :: " + str(d)[:400]), flush=True)
def api(base, m, p, b=None, t=None, h=None):
    r = urllib.request.Request(base + p, data=json.dumps(b).encode() if b is not None else None, method=m, headers=dict({"Content-Type": "application/json"}, **({"Authorization": "Bearer " + t} if t else {}), **(h or {})))
    try: x = urllib.request.urlopen(r); return x.status, json.loads(x.read() or b"null")
    except urllib.error.HTTPError as e:
        raw = e.read(); 
        try: return e.code, json.loads(raw or b"null")
        except Exception: return e.code, raw
def U(email, pw="correct horse", uid=None, name=None, handle=None, bal=10000): return {"id": uid or "u_" + handle, "email": email, "password": pw, "display_name": name or handle.title(), "handle": handle, "balance": bal}
def fixture(**kw):
    f = {"currency": "EUR", "minor_units": 2, "users": [U("ada@example.com", handle="ada", bal=10000), U("bob@example.com", handle="bob", bal=2500), U("cy@example.com", handle="cy", bal=0)]}
    f.update(kw); return f
def reset(base, f=None):
    s, j = api(base, "POST", "/_test/reset", f or fixture()); assert s == 204, (s, j)
def tok(base, email, pw="correct horse"): return api(base, "POST", "/auth/login", {"email": email, "password": pw})[1]["token"]
def ui_login(pg, base, email="ada@example.com", pw="correct horse", path="/"):
    pg.goto(base + "/login"); pg.fill("[data-testid=login-email]", email); pg.fill("[data-testid=login-password]", pw); pg.click("[data-testid=login-submit]")
    pg.wait_for_selector("[data-testid=wallet-balance]", timeout=8000)
    if path != "/": pg.goto(base + path)
def txt(pg, tid): 
    e = pg.query_selector(f"[data-testid={tid}]"); return e.inner_text().strip() if e else None
def amt(pg, tid):
    e = pg.query_selector(f"[data-testid={tid}]"); return int(e.get_attribute("data-amount")) if e and e.get_attribute("data-amount") is not None else None
def present(pg, tid): return pg.query_selector(f"[data-testid={tid}]") is not None
def visible(pg, tid): 
    e = pg.query_selector(f"[data-testid={tid}]"); return bool(e and e.is_visible())
def fill_pay(pg, handle="bob", amount="1.00", note="", vis=None):
    pg.fill("[data-testid=pay-handle]", handle); pg.fill("[data-testid=pay-amount]", amount); pg.fill("[data-testid=pay-note]", note)
    if vis: pg.select_option("[data-testid=pay-visibility]", vis)
def watch_posts(pg, path_re=r"/payments$"):
    seen = []
    def on_req(r):
        if r.method == "POST" and re.search(path_re, r.url.split("?")[0]): seen.append({"url": r.url, "key": r.headers.get("idempotency-key"), "body": r.post_data})
    pg.on("request", on_req); return seen
def summary():
    bad = [r for r in RES if not r[1]]; print(f"\n{len(RES) - len(bad)} passed, {len(bad)} failed"); return 1 if bad else 0
