import json, sys, urllib.request
from playwright.sync_api import sync_playwright
BASE = sys.argv[1]
def api(m, p, b=None, t=None, h=None):
    r = urllib.request.Request(BASE + p, data=json.dumps(b).encode() if b is not None else None, method=m, headers=dict({"Content-Type": "application/json"}, **({"Authorization": "Bearer " + t} if t else {}), **(h or {})))
    try: x = urllib.request.urlopen(r); return x.status, json.loads(x.read() or b"null")
    except urllib.error.HTTPError as e: return e.code, json.loads(e.read() or b"null")
fx = {"currency": "EUR", "minor_units": 2, "users": [{"id": "u_ada", "email": "ada@example.com", "password": "correct horse", "display_name": "Ada", "handle": "ada", "balance": 10000}, {"id": "u_bob", "email": "bob@example.com", "password": "correct horse", "display_name": "Bob", "handle": "bob", "balance": 2500}],
      "payments": [{"id": "p_1", "from_user_id": "u_ada", "to_user_id": "u_bob", "amount": 500, "note": "coffee", "visibility": "public"}],
      "requests": [{"id": "rq_1", "requester_id": "u_bob", "payer_id": "u_ada", "amount": 1200, "note": "taxi", "status": "pending"}],
      "authorizations": [{"id": "a_1", "from_user_id": "u_ada", "to_user_id": "u_bob", "amount": 2000, "note": "deposit", "visibility": "public", "status": "open", "expires_at": "2099-01-01T00:00:00+00:00"}]}
assert api("POST", "/_test/reset", fx)[0] == 204
with sync_playwright() as p:
    b = p.chromium.launch()
    for w, h in ((375, 800), (1280, 900)):
        ctx = b.new_context(viewport={"width": w, "height": h}); pg = ctx.new_page()
        pg.goto(BASE + "/login"); pg.screenshot(path=f"shots/login-{w}.png", full_page=True)
        pg.fill("[data-testid=login-email]", "ada@example.com"); pg.fill("[data-testid=login-password]", "correct horse"); pg.click("[data-testid=login-submit]"); pg.wait_for_selector("[data-testid=wallet-balance]")
        pg.screenshot(path=f"shots/home-{w}.png", full_page=True)
        for route in ("/requests", "/split", "/authorizations"):
            pg.goto(BASE + route); pg.wait_for_timeout(500); pg.screenshot(path=f"shots/{route[1:]}-{w}.png", full_page=True)
        print(w, "scrollWidth", pg.evaluate("document.documentElement.scrollWidth"), "clientWidth", pg.evaluate("document.documentElement.clientWidth"))
        ctx.close()
    pg = b.new_context(viewport={"width": 1280, "height": 900}).new_page(); pg.goto(BASE + "/login")
    print(pg.evaluate("document.body.innerText")[:300])
    b.close()
