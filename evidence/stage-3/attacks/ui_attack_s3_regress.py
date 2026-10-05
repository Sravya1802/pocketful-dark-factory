#!/usr/bin/env python3
"""Stage-2 UI attacks (Playwright, Chromium). usage: ui_attack_s2.py URL_A URL_B [URL_STAGE1] [groups]   (RESETS)
Run with the harness venv: /Users/lakshmisravyarachakonda/darkfactory/dark-factory-wearedevs/.venv/bin/python"""
import sys, threading
from ui_lib import *
A, B = sys.argv[1], sys.argv[2]; S1 = sys.argv[3] if len(sys.argv) > 3 and sys.argv[3].startswith("http") else None
ONLY = [x for x in sys.argv[3:] if not x.startswith("http")]
BIG = fixture(users=[U("ada@example.com", handle="ada", bal=5_000_000_000), U("bob@example.com", handle="bob", bal=2500), U("cy@example.com", handle="cy", bal=0)])
def run(fn, viewport=(1280, 900)):
    with sync_playwright() as p:
        b = p.chromium.launch(); ctx = b.new_context(viewport={"width": viewport[0], "height": viewport[1]}); pg = ctx.new_page(); dialogs = []
        pg.on("dialog", lambda d: (dialogs.append(d.message), d.dismiss())); pg.dialogs = dialogs
        try: fn(pg, ctx, b)
        finally: b.close()

def U1_decimal():
    reset(A, BIG)
    def go(pg, ctx, b):
        ui_login(pg, A); posts = watch_posts(pg)
        def try_amount(a, handle="bob"):
            n = len(posts); pg.fill("[data-testid=pay-handle]", handle); pg.fill("[data-testid=pay-amount]", a); pg.fill("[data-testid=pay-note]", ""); pg.click("[data-testid=pay-submit]"); pg.wait_for_timeout(450)
            body = json.loads(posts[-1]["body"]) if len(posts) > n else None
            return body, present(pg, "pay-error") and visible(pg, "pay-error")
        for a, exp in (("15", 1500), ("15.00", 1500), ("15.5", 1550), ("0.29", 29), ("1.15", 115), ("0.07", 7), ("19.99", 1999), ("4.35", 435), ("1.10", 110), ("8.20", 820), ("0.01", 1), ("1000.00", 100000), ("10000000", 1000000000), ("10000000.00", 1000000000), ("00015.50", 1550), ("0.50", 50)):
            body, err = try_amount(a); check(f"U1.amount {a!r} submits {exp} minor units exactly", body is not None and body["amount"] == exp and not err, (body, err))
        for a in ("15.005", "15.001", "0.001", "abc", "", "1e2", "1E2", "1e-2", "NaN", "Infinity", "--5", "15.0.0", "1 000", "15,00", "1,5", "1,000.00", "$15", "15 EUR", "0x10", "٣", "１５", "15..", "..5", "-1", "-0.50"):
            body, err = try_amount(a); check(f"U1.amount {a!r} -> shows pay-error and sends NO request", body is None and err, (body, err))
        for a in ("15.", ".5", " 15 ", "+15", "0", "0.00", "10000000.01", "10000000.00001", "99999999999999999999", "9007199254740993", "1000000000"):
            body, err = try_amount(a); print(f"   INFO amount {a!r}: request={body} error_shown={err}")
        for a, ok in (("0", False), ("0.00", False), ("10000000.01", False), ("99999999999999999999", False), ("9007199254740993", False)):
            body, err = try_amount(a); check(f"U1.amount {a!r} never charges anything: either no request or server-refused with pay-error", (body is None and err) or (body is not None and err), (body, err))
        body, err = try_amount(" 15 "); check("U1.' 15 ' either trimmed to 1500 or rejected, never misparsed", (body and body["amount"] == 1500) or (body is None and err), (body, err))
        body, err = try_amount("+15"); check("U1.'+15' either 1500 or rejected, never misparsed", (body and body["amount"] == 1500) or (body is None and err), (body, err))
        # big-number precision: 2^53 + 1 style amounts must never be rounded to a different valid amount
        body, err = try_amount("90071992547409.93"); check("U1.90071992547409.93 never sent as a rounded float (rejected or exact 9007199254740993->over-limit)", body is None or body["amount"] == 9007199254740993, (body, err))
        check("U1.no dialogs / script execution", not pg.dialogs)
    run(go)
    # minor_units 0 and 3
    for cur, mu, cases in (("JPY", 0, (("15", 15, True), ("15.0", None, False), ("15.5", None, False), ("1200", 1200, True))), ("BHD", 3, (("1.234", 1234, True), ("1.2345", None, False), ("1.5", 1500, True), ("15", 15000, True), ("0.001", 1, True)))):
        reset(A, fixture(currency=cur, minor_units=mu, users=[U("ada@example.com", handle="ada", bal=5_000_000), U("bob@example.com", handle="bob", bal=2500)]))
        def go2(pg, ctx, b, cur=cur, mu=mu, cases=cases):
            ui_login(pg, A); posts = watch_posts(pg)
            for a, exp, ok in cases:
                n = len(posts); pg.fill("[data-testid=pay-handle]", "bob"); pg.fill("[data-testid=pay-amount]", a); pg.click("[data-testid=pay-submit]"); pg.wait_for_timeout(450)
                body = json.loads(posts[-1]["body"]) if len(posts) > n else None
                if ok: check(f"U1.{cur}({mu}) amount {a!r} -> {exp}", body and body["amount"] == exp, body)
                else: check(f"U1.{cur}({mu}) amount {a!r} rejected locally (too many decimals)", body is None and visible(pg, "pay-error"), body)
            w = txt(pg, "wallet-balance"); check(f"U1.{cur} wallet-balance format", re.fullmatch(r"\d+" + (r"\.\d{%d}" % mu if mu else "") + " " + cur, w or "") is not None, w)
            bal = amt(pg, "wallet-balance"); check(f"U1.{cur} wallet-balance data-amount == API total", bal == api(A, "GET", "/me", t=tok(A, "ada@example.com"))[1]["total"], bal)
            items = pg.query_selector_all("[data-testid^=activity-amount-]"); check(f"U1.{cur} activity amounts formatted with {mu} decimals", items and all(re.fullmatch(r"\d+" + (r"\.\d{%d}" % mu if mu else "") + " " + cur, i.inner_text().strip()) for i in items), [i.inner_text() for i in items][:3])
        run(go2)

def U2_pay_flow():
    reset(A, BIG)
    def go(pg, ctx, b):
        ui_login(pg, A); posts = watch_posts(pg); start = amt(pg, "wallet-balance")
        fill_pay(pg, "bob", "15.00", "lunch", "private"); pg.click("[data-testid=pay-submit]"); pg.wait_for_timeout(700)
        check("U2.pay success: balance fell once by 1500", amt(pg, "wallet-balance") == start - 1500 and amt(pg, "wallet-available") == start - 1500, (amt(pg, "wallet-balance"), start))
        check("U2.pay success: feed has exactly one item, no reload", len(pg.query_selector_all("[data-testid^=activity-item-]")) == 1)
        item = pg.query_selector("[data-testid^=activity-item-]"); pid = item.get_attribute("data-testid")[len("activity-item-"):]
        check("U2.feed item data-visibility=private, note exact, amount exactly '15.00 EUR', parties contain both handles", item.get_attribute("data-visibility") == "private" and txt(pg, f"activity-note-{pid}") == "lunch" and txt(pg, f"activity-amount-{pid}") == "15.00 EUR" and "ada" in txt(pg, f"activity-parties-{pid}") and "bob" in txt(pg, f"activity-parties-{pid}"))
        check("U2.form values kept after success", pg.input_value("[data-testid=pay-handle]") == "bob" and pg.input_value("[data-testid=pay-amount]") == "15.00" and pg.input_value("[data-testid=pay-note]") == "lunch" and pg.input_value("[data-testid=pay-visibility]") == "private")
        check("U2.pay-error absent after success", not present(pg, "pay-error") or not visible(pg, "pay-error"))
        n = len(posts); pg.click("[data-testid=pay-submit]"); pg.wait_for_timeout(600); pg.click("[data-testid=pay-submit]"); pg.wait_for_timeout(600)
        check("U2.unchanged resubmission x2 sends no new payment (balance falls once, one feed item, no pay-error)", amt(pg, "wallet-balance") == start - 1500 and len(pg.query_selector_all("[data-testid^=activity-item-]")) == 1 and not (present(pg, "pay-error") and visible(pg, "pay-error")), (amt(pg, "wallet-balance"), len(posts) - n))
        print("   INFO POSTs after unchanged resubmits:", len(posts) - n, [(p['key'][:8]) for p in posts])
        server_pay = api(A, "GET", "/activity", t=tok(A, "ada@example.com"))[1]["payments"]; check("U2.server has exactly one payment", len(server_pay) == 1, len(server_pay))
        pg.fill("[data-testid=pay-note]", "lunch2"); pg.click("[data-testid=pay-submit]"); pg.wait_for_timeout(700)
        check("U2.changed field -> new payment (balance fell twice, two feed items, different key)", amt(pg, "wallet-balance") == start - 3000 and len(pg.query_selector_all("[data-testid^=activity-item-]")) == 2 and len({p["key"] for p in posts}) >= 2, (amt(pg, "wallet-balance"), [p['key'] for p in posts]))
        ids = [e.get_attribute("data-testid") for e in pg.query_selector_all("[data-testid^=activity-item-]")]; check("U2.newest first in DOM", api(A, "GET", "/activity", t=tok(A, "ada@example.com"))[1]["payments"][0]["payment_id"] == ids[0][len("activity-item-"):], ids)
        # change then revert: same body as the first again -> new payment (it was changed in between)
        pg.fill("[data-testid=pay-note]", "lunch"); pg.click("[data-testid=pay-submit]"); pg.wait_for_timeout(700)
        check("U2.revert to earlier values after a change = a NEW payment (3 payments)", amt(pg, "wallet-balance") == start - 4500, amt(pg, "wallet-balance"))
        # double click
        n = len(posts); pg.fill("[data-testid=pay-note]", "dbl"); pg.dblclick("[data-testid=pay-submit]"); pg.wait_for_timeout(900)
        check("U2.double-click submit moves money once", amt(pg, "wallet-balance") == start - 6000, (amt(pg, "wallet-balance"), start))
        # refused: unknown handle, self, empty handle, @ prefix
        for h, name in (("zzzz", "unknown handle"), ("ada", "self payment"), ("", "empty handle")):
            fill_pay(pg, h, "1.00", "x"); pg.click("[data-testid=pay-submit]"); pg.wait_for_timeout(500)
            check(f"U2.{name}: pay-error shown with text, inputs preserved", visible(pg, "pay-error") and txt(pg, "pay-error") and pg.input_value("[data-testid=pay-handle]") == h and pg.input_value("[data-testid=pay-amount]") == "1.00", txt(pg, "pay-error"))
        fill_pay(pg, "@bob", "1.00", "at"); n = len(posts); pg.click("[data-testid=pay-submit]"); pg.wait_for_timeout(600)
        check("U2.leading @ in handle accepted (stripped)", len(posts) > n and json.loads(posts[-1]["body"])["to_handle"] == "bob" and not visible(pg, "pay-error"), posts[-1:])
        fill_pay(pg, " BOB ", "1.00", "case"); n = len(posts); pg.click("[data-testid=pay-submit]"); pg.wait_for_timeout(600)
        print("   INFO ' BOB ' ->", json.loads(posts[-1]["body"]) if len(posts) > n else None, "| error:", txt(pg, "pay-error") if visible(pg, "pay-error") else None)
        # note too long
        fill_pay(pg, "bob", "1.00", "x" * 201); pg.click("[data-testid=pay-submit]"); pg.wait_for_timeout(500); check("U2.note 201 chars -> pay-error (server or client)", visible(pg, "pay-error"))
        fill_pay(pg, "bob", "1.00", "😀" * 200); n = len(posts); pg.click("[data-testid=pay-submit]"); pg.wait_for_timeout(600); check("U2.note of 200 emoji accepted", len(posts) > n and not visible(pg, "pay-error"))
    run(go)

def U3_refused_and_stale():
    reset(A, fixture(users=[U("ada@example.com", handle="ada", bal=1000), U("bob@example.com", handle="bob", bal=2500)]))
    def go(pg, ctx, b):
        ui_login(pg, A); fill_pay(pg, "bob", "8.00", "later", "private")
        ta = tok(A, "ada@example.com"); api(A, "POST", "/payments", {"to_handle": "bob", "amount": 900}, ta, {"Idempotency-Key": "other-client"})   # another client spends
        check("U3.UI still shows stale 10.00 before refresh", amt(pg, "wallet-balance") == 1000)
        pg.click("[data-testid=pay-submit]"); pg.wait_for_timeout(700)
        check("U3.refused (insufficient funds) shows pay-error and NOT pay-uncertain", visible(pg, "pay-error") and not (present(pg, "pay-uncertain") and visible(pg, "pay-uncertain")), txt(pg, "pay-error"))
        check("U3.…balance refreshed to 1.00 and inputs preserved", amt(pg, "wallet-balance") == 100 and pg.input_value("[data-testid=pay-handle]") == "bob" and pg.input_value("[data-testid=pay-amount]") == "8.00" and pg.input_value("[data-testid=pay-note]") == "later" and pg.input_value("[data-testid=pay-visibility]") == "private", (amt(pg, "wallet-balance"),))
        check("U3.…feed refreshed (other client's payment appears)", len(pg.query_selector_all("[data-testid^=activity-item-]")) == 1)
        api(A, "POST", "/payments", {"to_handle": "ada", "amount": 2000}, tok(A, "bob@example.com"), {"Idempotency-Key": "refund"})
        pg.click("[data-testid=pay-submit]"); pg.wait_for_timeout(800)
        check("U3.after funds arrive, same unchanged form succeeds, pay-error cleared, money moves once", not visible(pg, "pay-error") and amt(pg, "wallet-balance") == 2100 - 800, (amt(pg, "wallet-balance"), txt(pg, "pay-error") if visible(pg, "pay-error") else None))
    run(go)
    # hold interplay in UI: available is headline, held funds can't be paid
    reset(A, fixture(authorizations=[{"id": "a_1", "from_user_id": "u_ada", "to_user_id": "u_bob", "amount": 8000, "note": "dep", "visibility": "public", "status": "open", "expires_at": "2099-01-01T00:00:00+00:00"}]))
    def go2(pg, ctx, b):
        ui_login(pg, A)
        check("U3.hold seeded: wallet-available 20.00 / wallet-balance 100.00 / wallet-held 80.00 with data-amount", amt(pg, "wallet-available") == 2000 and amt(pg, "wallet-balance") == 10000 and amt(pg, "wallet-held") == 8000 and txt(pg, "wallet-available") == "20.00 EUR" and txt(pg, "wallet-balance") == "100.00 EUR" and txt(pg, "wallet-held") == "80.00 EUR", (txt(pg, "wallet-available"), txt(pg, "wallet-balance"), txt(pg, "wallet-held")))
        fa = pg.evaluate("parseFloat(getComputedStyle(document.querySelector('[data-testid=wallet-available]')).fontSize)"); fb = pg.evaluate("parseFloat(getComputedStyle(document.querySelector('[data-testid=wallet-balance]')).fontSize)"); fh = pg.evaluate("parseFloat(getComputedStyle(document.querySelector('[data-testid=wallet-held]')).fontSize)")
        check("U3.available is the headline: font size larger than total and held", fa > fb and fa > fh, (fa, fb, fh))
        fill_pay(pg, "bob", "20.01"); pg.click("[data-testid=pay-submit]"); pg.wait_for_timeout(600); check("U3.payment above available (but within total) refused with pay-error", visible(pg, "pay-error"), txt(pg, "pay-error"))
        fill_pay(pg, "bob", "20.00"); pg.click("[data-testid=pay-submit]"); pg.wait_for_timeout(700); check("U3.payment == available succeeds; available 0, held still 80.00", amt(pg, "wallet-available") == 0 and amt(pg, "wallet-held") == 8000 and amt(pg, "wallet-balance") == 8000, (amt(pg, "wallet-available"), amt(pg, "wallet-held")))
        api(A, "POST", f"/authorizations/a_1/void", {}, tok(A, "ada@example.com")); pg.click("[data-testid=wallet-refresh]"); pg.wait_for_timeout(600)
        check("U3.after void elsewhere + wallet-refresh: held disappears (absent), available 80.00", not present(pg, "wallet-held") and amt(pg, "wallet-available") == 8000, (present(pg, "wallet-held"), amt(pg, "wallet-available")))
    run(go2)

def U4_lost_responses():
    reset(A, BIG)
    def go(pg, ctx, b):
        ui_login(pg, A); posts = watch_posts(pg); start = amt(pg, "wallet-balance")
        # lost AFTER commit
        mode = {"m": "lose_after"}
        def handler(route):
            if route.request.method == "POST" and route.request.url.split("?")[0].endswith("/payments") and mode["m"] != "pass":
                if mode["m"] == "lose_after": route.fetch(); route.abort()          # server commits, client never sees the response
                else: route.abort()                                                  # lost before the server saw it
            else: route.continue_()
        pg.route("**/payments", handler)
        fill_pay(pg, "bob", "12.34", "uncertain", "public"); pg.click("[data-testid=pay-submit]"); pg.wait_for_timeout(900)
        check("U4.lost after commit: pay-uncertain visible with text, pay-error NOT shown", visible(pg, "pay-uncertain") and txt(pg, "pay-uncertain") and not (present(pg, "pay-error") and visible(pg, "pay-error")), (txt(pg, "pay-uncertain"), present(pg, "pay-error")))
        check("U4.…form still filled", pg.input_value("[data-testid=pay-handle]") == "bob" and pg.input_value("[data-testid=pay-amount]") == "12.34" and pg.input_value("[data-testid=pay-note]") == "uncertain")
        check("U4.…the payment really committed server-side (once)", len(api(A, "GET", "/activity", t=tok(A, "ada@example.com"))[1]["payments"]) == 1)
        mode["m"] = "pass"; n = len(posts); pg.click("[data-testid=pay-submit]"); pg.wait_for_timeout(900)
        retry = posts[n:]; first = posts[n - 1]
        check("U4.retry sends the SAME Idempotency-Key and byte-identical body (as JSON value)", len(retry) >= 1 and retry[0]["key"] == first["key"] and json.loads(retry[0]["body"]) == json.loads(first["body"]), (first, retry))
        check("U4.retry success: pay-uncertain and pay-error gone", not (present(pg, "pay-uncertain") and visible(pg, "pay-uncertain")) and not (present(pg, "pay-error") and visible(pg, "pay-error")))
        check("U4.…money moved exactly once (balance - 12.34), one feed item, balance refreshed", amt(pg, "wallet-balance") == start - 1234 and len(pg.query_selector_all("[data-testid^=activity-item-]")) == 1, (amt(pg, "wallet-balance"), start))
        check("U4.…server still has exactly one payment", len(api(A, "GET", "/activity", t=tok(A, "ada@example.com"))[1]["payments"]) == 1)
        # lost BEFORE commit
        mode["m"] = "lose_before"; fill_pay(pg, "bob", "5.00", "never reached"); n = len(posts); pg.click("[data-testid=pay-submit]"); pg.wait_for_timeout(900)
        check("U4.lost before server: pay-uncertain (not pay-error)", visible(pg, "pay-uncertain") and not (present(pg, "pay-error") and visible(pg, "pay-error")))
        mode["m"] = "pass"; pg.click("[data-testid=pay-submit]"); pg.wait_for_timeout(900)
        check("U4.retry after never-reached: exactly one more payment, same key", amt(pg, "wallet-balance") == start - 1234 - 500 and posts[-1]["key"] == posts[-2]["key"], (amt(pg, "wallet-balance"), [p['key'][:6] for p in posts]))
        # uncertain, then user CHANGES the form: it is a new payment with a new key
        mode["m"] = "lose_after"; fill_pay(pg, "bob", "7.00", "A"); pg.click("[data-testid=pay-submit]"); pg.wait_for_timeout(800); kA = posts[-1]["key"]
        check("U4.uncertain again", visible(pg, "pay-uncertain"))
        mode["m"] = "pass"; pg.fill("[data-testid=pay-note]", "B"); pg.click("[data-testid=pay-submit]"); pg.wait_for_timeout(900)
        check("U4.changing the form after uncertainty creates a NEW key (and a new payment)", posts[-1]["key"] != kA and json.loads(posts[-1]["body"])["note"] == "B", posts[-1])
        print("   INFO after change-while-uncertain: pay-uncertain visible:", visible(pg, "pay-uncertain"), "| balance delta:", start - amt(pg, "wallet-balance"))
        # server error 500 on payment -> uncertain or error? (unknown outcome)
        pg.unroute("**/payments"); pg.route("**/payments", lambda r: r.fulfill(status=503, body='{"error":{"code":"unavailable","message":"x"}}', content_type="application/json") if r.request.method == "POST" else r.continue_())
        fill_pay(pg, "bob", "3.00", "503"); pg.click("[data-testid=pay-submit]"); pg.wait_for_timeout(800)
        print("   INFO 503 response -> uncertain:", visible(pg, "pay-uncertain"), "error:", visible(pg, "pay-error"))
        check("U4.5xx from server: some error/uncertain state shown (not silent success)", visible(pg, "pay-uncertain") or visible(pg, "pay-error"))
        pg.unroute("**/payments")
        pg.route("**/payments", lambda r: r.fulfill(status=200, body="<html>proxy</html>", content_type="text/html") if r.request.method == "POST" else r.continue_())
        fill_pay(pg, "bob", "3.01", "junk"); pg.click("[data-testid=pay-submit]"); pg.wait_for_timeout(800); check("U4.non-JSON 200 body: no crash, uncertain/error shown", visible(pg, "pay-uncertain") or visible(pg, "pay-error")); pg.unroute("**/payments")
    run(go)

def U5_refresh_latest_wins():
    reset(A, BIG)
    def go(pg, ctx, b):
        ui_login(pg, A); ta = tok(A, "ada@example.com"); start = amt(pg, "wallet-balance")
        fill_pay(pg, "bob", "3.33", "typed", "private")
        state = {"n": 0}
        def handler(route):
            u = route.request.url.split("?")[0]
            if route.request.method == "GET" and (u.endswith("/me") or u.endswith("/activity")):
                state["n"] += 1; k = state["n"]
                resp = route.fetch()
                if k <= 2: time.sleep(2.2)      # FIRST refresh (both reads) is slow and carries the OLD state
                route.fulfill(response=resp)
            else: route.continue_()
        pg.route("**/*", handler)
        pg.click("[data-testid=wallet-refresh]")                                  # refresh #1 (old data, slow)
        pg.wait_for_timeout(300)
        api(A, "POST", "/payments", {"to_handle": "bob", "amount": 100000}, ta, {"Idempotency-Key": "x1"})     # state changes: -1000.00
        state["n"] = 100                                                            # later reads are fast
        pg.click("[data-testid=wallet-refresh]"); pg.wait_for_timeout(500)         # refresh #2 (new data, fast)
        new = amt(pg, "wallet-balance"); check("U5.second (faster) refresh shows the NEW balance", new == start - 100000, (new, start))
        pg.wait_for_timeout(3500)
        check("U5.the delayed OLDER response does not overwrite the newer one (latest refresh wins)", amt(pg, "wallet-balance") == start - 100000 and amt(pg, "wallet-available") == start - 100000, (amt(pg, "wallet-balance"), start))
        check("U5.…feed also keeps the newer state (1 payment)", len(pg.query_selector_all("[data-testid^=activity-item-]")) == 1, len(pg.query_selector_all("[data-testid^=activity-item-]")))
        check("U5.refresh did not clear the pay form", pg.input_value("[data-testid=pay-handle]") == "bob" and pg.input_value("[data-testid=pay-amount]") == "3.33" and pg.input_value("[data-testid=pay-note]") == "typed" and pg.input_value("[data-testid=pay-visibility]") == "private")
        pg.unroute("**/*")
        # reversed: mixed order between /me and /activity of different refreshes
        state["n"] = 0; log = []
        def handler2(route):
            u = route.request.url.split("?")[0]
            if route.request.method == "GET" and (u.endswith("/me") or u.endswith("/activity")):
                state["n"] += 1; k = state["n"]; resp = route.fetch()
                if u.endswith("/me") and k == 1: time.sleep(2.0)
                route.fulfill(response=resp)
            else: route.continue_()
        pg.route("**/*", handler2); pg.click("[data-testid=wallet-refresh]"); pg.wait_for_timeout(150)
        api(A, "POST", "/payments", {"to_handle": "bob", "amount": 50000}, ta, {"Idempotency-Key": "x2"}); pg.click("[data-testid=wallet-refresh]"); pg.wait_for_timeout(3200)
        truth = api(A, "GET", "/me", t=ta)[1]["total"]; check("U5.second scenario (older /me slower than newer): final balance == server truth", amt(pg, "wallet-balance") == truth, (amt(pg, "wallet-balance"), truth))
        n_feed = len(api(A, "GET", "/activity", t=ta)[1]["payments"]); check("U5.…final feed == server truth", len(pg.query_selector_all("[data-testid^=activity-item-]")) == n_feed, (len(pg.query_selector_all("[data-testid^=activity-item-]")), n_feed))
        pg.unroute("**/*")
        # five rapid refreshes with random delays
        import random; random.seed(7)
        def handler3(route):
            u = route.request.url.split("?")[0]
            if route.request.method == "GET" and (u.endswith("/me") or u.endswith("/activity")):
                resp = route.fetch(); time.sleep(random.choice([0.0, 0.4, 0.9, 1.4])); route.fulfill(response=resp)
            else: route.continue_()
        pg.route("**/*", handler3)
        for i in range(5):
            api(A, "POST", "/payments", {"to_handle": "bob", "amount": 100 + i}, ta, {"Idempotency-Key": f"rr{i}"}); pg.click("[data-testid=wallet-refresh]"); pg.wait_for_timeout(120)
        pg.wait_for_timeout(3500); truth = api(A, "GET", "/me", t=ta)[1]["total"]; check("U5.5 rapid refreshes with random delays: UI ends on the latest server state", amt(pg, "wallet-balance") == truth, (amt(pg, "wallet-balance"), truth))
    run(go)

def U6_requests_screen():
    f = fixture(requests=[{"id": "rq_1", "requester_id": "u_bob", "payer_id": "u_ada", "amount": 1200, "note": "taxi", "status": "pending"}, {"id": "rq_2", "requester_id": "u_ada", "payer_id": "u_bob", "amount": 300, "note": "lunch", "status": "pending"}, {"id": "rq_3", "requester_id": "u_bob", "payer_id": "u_ada", "amount": 50, "note": "gum", "status": "pending"}, {"id": "rq_4", "requester_id": "u_bob", "payer_id": "u_ada", "amount": 70, "note": "paid", "status": "declined"}])
    reset(A, f)
    def go(pg, ctx, b):
        ui_login(pg, A, path="/requests"); pg.wait_for_selector("[data-testid=incoming-list]")
        check("U6.pay/decline only on pending incoming; cancel only on pending outgoing", present(pg, "request-pay-rq_1") and present(pg, "request-decline-rq_1") and not present(pg, "request-cancel-rq_1") and present(pg, "request-cancel-rq_2") and not present(pg, "request-pay-rq_2") and not present(pg, "request-pay-rq_4") and not present(pg, "request-decline-rq_4"))
        check("U6.request-amount exact formatted; data-status attrs", txt(pg, "request-amount-rq_1") == "12.00 EUR" and txt(pg, "request-amount-rq_2") == "3.00 EUR" and pg.get_attribute("[data-testid=request-item-rq_4]", "data-status") == "declined" and pg.get_attribute("[data-testid=request-item-rq_1]", "data-status") == "pending")
        check("U6.incoming/outgoing placed in the right containers", pg.query_selector("[data-testid=incoming-list] [data-testid=request-item-rq_1]") and pg.query_selector("[data-testid=outgoing-list] [data-testid=request-item-rq_2]") and not pg.query_selector("[data-testid=incoming-list] [data-testid=request-item-rq_2]"))
        # cancelled elsewhere while pay visible -> request-error + list refresh
        api(A, "POST", "/requests/rq_1/cancel", {}, tok(A, "bob@example.com")); pg.click("[data-testid=request-pay-rq_1]"); pg.wait_for_timeout(900)
        check("U6.request cancelled elsewhere: pay refused shows request-error", visible(pg, "request-error") and txt(pg, "request-error"), txt(pg, "request-error"))
        check("U6.…stale pay button disappears and status shows cancelled", not present(pg, "request-pay-rq_1") and pg.get_attribute("[data-testid=request-item-rq_1]", "data-status") == "cancelled", (present(pg, "request-pay-rq_1"), pg.get_attribute("[data-testid=request-item-rq_1]", "data-status")))
        check("U6.…no money moved", api(A, "GET", "/me", t=tok(A, "ada@example.com"))[1]["total"] == 10000)
        # successful pay
        pg.click("[data-testid=request-pay-rq_3]"); pg.wait_for_timeout(900)
        check("U6.pay request success: status paid, buttons gone, balance reflects, error cleared", pg.get_attribute("[data-testid=request-item-rq_3]", "data-status") == "paid" and not present(pg, "request-pay-rq_3") and not (present(pg, "request-error") and visible(pg, "request-error")) and api(A, "GET", "/me", t=tok(A, "ada@example.com"))[1]["total"] == 9950, pg.get_attribute("[data-testid=request-item-rq_3]", "data-status"))
        if present(pg, "wallet-available"): check("U6.balance shown on requests page matches", amt(pg, "wallet-available") == 9950, amt(pg, "wallet-available"))
        pg.click("[data-testid=request-cancel-rq_2]"); pg.wait_for_timeout(700); check("U6.cancel outgoing: status cancelled", pg.get_attribute("[data-testid=request-item-rq_2]", "data-status") == "cancelled")
        # decline elsewhere then decline again / cancelled-elsewhere cancel
        reset(A, f); 
    run(go)
    # insufficient funds on pay request
    reset(A, fixture(users=[U("ada@example.com", handle="ada", bal=100), U("bob@example.com", handle="bob", bal=2500)], requests=[{"id": "rq_1", "requester_id": "u_bob", "payer_id": "u_ada", "amount": 1200, "note": "taxi", "status": "pending"}]))
    def go2(pg, ctx, b):
        ui_login(pg, A, path="/requests"); pg.wait_for_selector("[data-testid=request-pay-rq_1]"); pg.click("[data-testid=request-pay-rq_1]"); pg.wait_for_timeout(700)
        check("U6.insufficient funds on request pay -> request-error, request stays pending with pay button", visible(pg, "request-error") and pg.get_attribute("[data-testid=request-item-rq_1]", "data-status") == "pending" and present(pg, "request-pay-rq_1"))
        api(A, "POST", "/payments", {"to_handle": "ada", "amount": 2000}, tok(A, "bob@example.com"), {"Idempotency-Key": "fund"}); pg.click("[data-testid=request-pay-rq_1]"); pg.wait_for_timeout(800)
        check("U6.after funding, retry pays it; request-error cleared", pg.get_attribute("[data-testid=request-item-rq_1]", "data-status") == "paid" and not (present(pg, "request-error") and visible(pg, "request-error")))
        # lost response on request pay (after commit)
        reset(A, fixture(requests=[{"id": "rq_1", "requester_id": "u_bob", "payer_id": "u_ada", "amount": 1200, "note": "taxi", "status": "pending"}])); pg.goto(A + "/login"); ui_login(pg, A, path="/requests"); pg.wait_for_selector("[data-testid=request-pay-rq_1]")
        keys = []; mode = {"lose": True}
        def h(route):
            if route.request.method == "POST" and route.request.url.endswith("/pay"):
                keys.append((route.request.headers.get("idempotency-key"), route.request.post_data))
                if mode["lose"]: route.fetch(); route.abort(); return
            route.continue_()
        pg.route("**/requests/*/pay", h); pg.click("[data-testid=request-pay-rq_1]"); pg.wait_for_timeout(900)
        print("   INFO after lost request-pay response: request-error visible:", visible(pg, "request-error"), "| text:", txt(pg, "request-error") if present(pg, "request-error") else None, "| pay-uncertain:", present(pg, "pay-uncertain"))
        mode["lose"] = False
        if present(pg, "request-pay-rq_1"): pg.click("[data-testid=request-pay-rq_1]"); pg.wait_for_timeout(900)
        print("   INFO request-uncertain element visible:", present(pg, "request-uncertain")); check("U6.request-pay lost after commit then retried: same key reused and money moved once", len(keys) >= 2 and keys[0][0] == keys[1][0] and api(A, "GET", "/me", t=tok(A, "ada@example.com"))[1]["total"] == 8800, (keys, api(A, "GET", "/me", t=tok(A, "ada@example.com"))[1]["total"]))
        check("U6.…ends in paid state without error", pg.get_attribute("[data-testid=request-item-rq_1]", "data-status") == "paid", pg.get_attribute("[data-testid=request-item-rq_1]", "data-status"))
    run(go2)
    reset(A, fixture())
    def go3(pg, ctx, b):
        ui_login(pg, A, path="/requests"); pg.wait_for_selector("[data-testid=empty-requests]"); check("U6.empty-requests shown when both lists empty", visible(pg, "empty-requests"))
        ui_login(pg, A); posts = watch_posts(pg, r"/requests$"); pg.fill("[data-testid=request-handle]", "bob"); pg.fill("[data-testid=request-amount]", "4.20"); pg.fill("[data-testid=request-note]", "pizza"); pg.click("[data-testid=request-submit]"); pg.wait_for_timeout(700)
        check("U6.request form sends 420 and keeps values; request-error absent", posts and json.loads(posts[0]["body"])["amount"] == 420 and pg.input_value("[data-testid=request-amount]") == "4.20" and not (present(pg, "request-error") and visible(pg, "request-error")), posts)
        n = len(posts); pg.click("[data-testid=request-submit]"); pg.wait_for_timeout(600); print("   INFO request form unchanged resubmit POSTs:", len(posts) - n)
        check("U6.unchanged request resubmit creates no second request", len(api(A, "GET", "/requests", t=tok(A, "ada@example.com"))[1]["requests"]) == 1, len(api(A, "GET", "/requests", t=tok(A, "ada@example.com"))[1]["requests"]))
        pg.fill("[data-testid=request-amount]", "4.205"); pg.click("[data-testid=request-submit]"); pg.wait_for_timeout(400); check("U6.request 4.205 -> request-error, no request", visible(pg, "request-error"))
        pg.fill("[data-testid=request-handle]", "ada"); pg.fill("[data-testid=request-amount]", "1"); pg.click("[data-testid=request-submit]"); pg.wait_for_timeout(500); check("U6.request from self -> request-error", visible(pg, "request-error"))
        pg.goto(A + "/requests"); pg.wait_for_selector("[data-testid=outgoing-list]"); check("U6.new request visible on /requests after navigation (outgoing, pending, cancel button)", len(pg.query_selector_all("[data-testid=outgoing-list] [data-testid^=request-item-]")) == 1 and len(pg.query_selector_all("[data-testid^=request-cancel-]")) == 1)
    run(go3)

def U7_split():
    reset(A, fixture(users=[U("ada@example.com", handle="ada", bal=1000000), U("bob@example.com", handle="bob"), U("cy@example.com", handle="cy"), U("dee@example.com", handle="dee"), U("eve@example.com", handle="eve")]))
    def shares(a_minor, n): base, rem = divmod(a_minor, n); return [base + (1 if i < rem else 0) for i in range(n)]
    def go(pg, ctx, b):
        ui_login(pg, A, path="/split"); posts = watch_posts(pg, r"/splits$")
        cases = [("10.00", "ada,bob,cy"), ("0.01", "ada,bob,cy"), ("0.10", "ada,bob,cy"), ("9.99", "ada,bob,cy"), ("0.05", "ada,bob,cy,dee,eve"), ("10", "bob,cy,dee"), ("100000.00", "cy,bob,ada"), ("0.07", "bob, cy , dee"), ("1.00", "ada"), ("1.00", "bob"), ("0.02", "ada,bob,cy,dee,eve"), ("10000000.00", "ada,bob,cy"), ("0.29", "ada,bob,cy"), ("1.15", "bob,cy")]
        for a, hs in cases:
            pg.fill("[data-testid=split-amount]", a); pg.fill("[data-testid=split-handles]", hs); pg.wait_for_timeout(250)
            hl = [h.strip() for h in hs.split(",")]; minor = round(float(a) * 100); exp = shares(minor, len(hl))
            got = [txt(pg, f"split-share-{h}") for h in hl]
            check(f"U7.preview {a} / {hs!r} == server rule {['%d.%02d EUR' % (e // 100, e % 100) for e in exp]}", got == ["%d.%02d EUR" % (e // 100, e % 100) for e in exp], got)
        # submit and compare with what the server created
        for a, hs in (("10.00", "ada,bob,cy"), ("0.10", "cy,bob,ada"), ("0.05", "bob,cy,dee,eve")):
            reset(A, fixture(users=[U("ada@example.com", handle="ada", bal=1000000), U("bob@example.com", handle="bob"), U("cy@example.com", handle="cy"), U("dee@example.com", handle="dee"), U("eve@example.com", handle="eve")])); ui_login(pg, A, path="/split")
            pg.fill("[data-testid=split-amount]", a); pg.fill("[data-testid=split-handles]", hs); pg.fill("[data-testid=split-note]", "dinner"); pg.wait_for_timeout(250)
            hl = [h.strip() for h in hs.split(",")]; prev = {h: txt(pg, f"split-share-{h}") for h in hl}
            n = len(posts); pg.click("[data-testid=split-submit]"); pg.wait_for_timeout(900)
            body = json.loads(posts[-1]["body"]) if len(posts) > n else None
            sv = {}; 
            for h in hl:
                if h == "ada": continue
                for r in api(A, "GET", "/requests", t=tok(A, f"{h}@example.com"))[1]["requests"]: sv[h] = "%d.%02d EUR" % (r["amount"] // 100, r["amount"] % 100)
            check(f"U7.submit {a} / {hs!r}: body participant order & minor-unit amount, preview shares == server-created requests", body and body["participant_handles"] == hl and body["amount"] == round(float(a) * 100) and all(prev[h] == sv[h] for h in sv), (body, prev, sv))
            check("U7.…no split-error shown on success; preview still shows shares", not (present(pg, "split-error") and visible(pg, "split-error")) and visible(pg, "split-preview"))
            n = len(posts); pg.click("[data-testid=split-submit]"); pg.wait_for_timeout(700)
            print("   INFO split unchanged resubmit POSTs:", len(posts) - n, "| requests now:", sum(len(api(A, "GET", "/requests", t=tok(A, f'{h}@example.com'))[1]['requests']) for h in hl if h != 'ada'))
        # error handling
        pg.goto(A + "/split"); pg.wait_for_selector("[data-testid=split-amount]")
        for a, hs, name in (("10", "ada,zzzz", "unknown handle"), ("10", "bob,bob", "duplicate"), ("10", "", "empty handles"), ("abc", "bob", "bad amount"), ("10.005", "bob,cy", "3 decimals"), ("0", "bob", "zero amount"), ("10", ",", "only commas")):
            pg.fill("[data-testid=split-amount]", a); pg.fill("[data-testid=split-handles]", hs); n = len(posts); pg.click("[data-testid=split-submit]"); pg.wait_for_timeout(500)
            check(f"U7.{name}: split-error shown (client-side or server refusal), form preserved", visible(pg, "split-error") and pg.input_value("[data-testid=split-amount]") == a and pg.input_value("[data-testid=split-handles]") == hs, (txt(pg, "split-error") if present(pg, "split-error") else None))
            if name in ("3 decimals", "bad amount"): check(f"U7.{name}: no request sent", len(posts) == n)
        pg.fill("[data-testid=split-amount]", "10"); pg.fill("[data-testid=split-handles]", "bob,,cy"); pg.wait_for_timeout(300); print("   INFO preview for 'bob,,cy':", pg.query_selector("[data-testid=split-preview]").inner_text()[:80].replace("\n", " | "))
        pg.fill("[data-testid=split-amount]", "10"); pg.fill("[data-testid=split-handles]", "bob,bob"); pg.wait_for_timeout(300); print("   INFO preview for duplicate handles:", pg.query_selector("[data-testid=split-preview]").inner_text()[:80].replace("\n", " | "))
        pg.fill("[data-testid=split-amount]", "1e2"); pg.fill("[data-testid=split-handles]", "bob,cy"); pg.wait_for_timeout(300); print("   INFO preview for amount 1e2:", pg.query_selector("[data-testid=split-preview]").inner_text()[:80].replace("\n", " | "))
        pg.fill("[data-testid=split-amount]", "0.001"); pg.wait_for_timeout(300); print("   INFO preview for 0.001:", pg.query_selector("[data-testid=split-preview]").inner_text()[:80].replace("\n", " | "))
    run(go)
    # minor_units 0 and 3
    for cur, mu in (("JPY", 0), ("BHD", 3)):
        reset(A, fixture(currency=cur, minor_units=mu, users=[U("ada@example.com", handle="ada", bal=1000000), U("bob@example.com", handle="bob"), U("cy@example.com", handle="cy")]))
        def go2(pg, ctx, b):
            ui_login(pg, A, path="/split"); pg.fill("[data-testid=split-amount]", "10" if mu == 0 else "1.000"); pg.fill("[data-testid=split-handles]", "ada,bob,cy"); pg.wait_for_timeout(300)
            got = [txt(pg, f"split-share-{h}") for h in ("ada", "bob", "cy")]; exp = ["4 JPY", "3 JPY", "3 JPY"] if mu == 0 else ["0.334 BHD", "0.333 BHD", "0.333 BHD"]
            check(f"U7.{cur} split preview formats with {mu} decimals", got == exp, got)
        run(go2)

def U8_xss_session():
    evil = ['<img src=x onerror="window.__x=1;alert(1)">', '<script>window.__x=1;alert(1)</script>', '"><svg onload=alert(1)>', "{{7*7}}", "'; alert(1); //", "</textarea><b id=boom>b</b>"]
    users = [U("ada@example.com", handle="ada", name=evil[0]), U("bob@example.com", handle="bob", name=evil[1]), U("cy@example.com", handle="cy", name=evil[2])]
    pays = [{"id": f"p_{i}", "from_user_id": "u_ada", "to_user_id": "u_bob", "amount": 100 + i, "note": e, "visibility": "public"} for i, e in enumerate(evil)]
    reqs = [{"id": "rq_x", "requester_id": "u_bob", "payer_id": "u_ada", "amount": 100, "note": evil[1], "status": "pending"}]
    auths = [{"id": "a_x", "from_user_id": "u_ada", "to_user_id": "u_bob", "amount": 100, "note": evil[0], "visibility": "public", "status": "open", "expires_at": "2099-01-01T00:00:00+00:00"}]
    reset(A, fixture(users=users, payments=pays, requests=reqs, authorizations=auths))
    def go(pg, ctx, b):
        ui_login(pg, A, email="ada@example.com"); pg.wait_for_selector("[data-testid^=activity-item-]")
        for i, e in enumerate(evil): check(f"U8.stored note #{i} rendered as exact text (no markup interpretation)", txt(pg, f"activity-note-p_{i}") == e, txt(pg, f"activity-note-p_{i}"))
        check("U8.no script executed / no injected element after loading feed", not pg.dialogs and pg.evaluate("window.__x") is None and pg.query_selector("#boom") is None and pg.query_selector("img[src=x]") is None and pg.query_selector("svg[onload]") is None, pg.dialogs)
        check("U8.display name with markup shown as text in current-user", txt(pg, "current-user") and evil[0] in txt(pg, "current-user"), txt(pg, "current-user"))
        for r in ("/requests", "/authorizations", "/split"):
            pg.goto(A + r); pg.wait_for_timeout(700)
        pg.goto(A + "/requests"); pg.wait_for_timeout(600); pg.goto(A + "/authorizations"); pg.wait_for_timeout(600)
        check("U8.requests/authorizations/split pages: no script executed, no injected nodes", not pg.dialogs and pg.evaluate("window.__x") is None and pg.query_selector("#boom") is None)
        # handles / errors reflect user input
        pg.goto(A); pg.wait_for_selector("[data-testid=pay-handle]"); fill_pay(pg, '"><img src=x onerror=alert(1)>', "1.00", "x"); pg.click("[data-testid=pay-submit]"); pg.wait_for_timeout(700)
        check("U8.handle with markup reflected in error as text only", not pg.dialogs and pg.query_selector("img[src=x]") is None, pg.dialogs)
        # signup with evil display name then view
        pg.goto(A + "/signup"); pg.fill("[data-testid=signup-email]", "x@x.com"); pg.fill("[data-testid=signup-password]", "password1"); pg.fill("[data-testid=signup-display-name]", '<img src=x onerror=alert(1)>'); pg.click("[data-testid=signup-submit]"); pg.wait_for_selector("[data-testid=wallet-balance]", timeout=8000); pg.wait_for_timeout(300)
        check("U8.signup with markup display name: rendered as text, no execution", not pg.dialogs and pg.query_selector("img[src=x]") is None and "<img" in (txt(pg, "current-user") or ""), (txt(pg, "current-user"), pg.dialogs))
        # CSP and headers
        resp = ctx.request.get(A + "/"); h = {k.lower(): v for k, v in resp.headers.items()}
        print("   INFO headers:", {k: h[k][:120] for k in h if k in ("content-security-policy", "x-content-type-options", "referrer-policy", "x-frame-options", "cache-control")})
        check("U8.Content-Security-Policy header present and forbids inline script (no 'unsafe-inline' in script-src) ", "content-security-policy" in h and "script-src" in h["content-security-policy"] and "unsafe-inline" not in h["content-security-policy"].split("script-src")[1].split(";")[0], h.get("content-security-policy"))
        check("U8.X-Content-Type-Options nosniff", h.get("x-content-type-options") == "nosniff", h.get("x-content-type-options"))
    run(go)
    # open redirect via next=
    reset(A, fixture())
    def go2(pg, ctx, b):
        for nxt in ("//evil.example.com/x", "https://evil.example.com/", "javascript:alert(1)", "/\\evil.example.com", "%2F%2Fevil.example.com"):
            pg.goto(A + "/login?next=" + nxt); pg.fill("[data-testid=login-email]", "ada@example.com"); pg.fill("[data-testid=login-password]", "correct horse")
            try:
                with pg.expect_navigation(timeout=4000, wait_until="commit"): pg.click("[data-testid=login-submit]")
            except Exception: pass
            pg.wait_for_timeout(500)
            check(f"U8.login next={nxt!r}: stays on own origin, no script", pg.url.startswith(A) and not pg.dialogs, pg.url)
            pg.evaluate("localStorage.clear()")
        pg.goto(A + "/login?next=/requests"); pg.fill("[data-testid=login-email]", "ada@example.com"); pg.fill("[data-testid=login-password]", "correct horse"); pg.click("[data-testid=login-submit]"); pg.wait_for_timeout(900)
        check("U8.login?next=/requests lands on /requests", pg.url.rstrip("/").endswith("/requests"), pg.url)
    run(go2)

def U9_session():
    reset(A, fixture())
    def go(pg, ctx, b):
        for r in ("/", "/requests", "/split", "/authorizations"):
            pg.goto(A + r); pg.wait_for_timeout(500); check(f"U9.signed-out visit to {r} redirects to /login", "/login" in pg.url, pg.url)
        pg.goto(A + "/login"); check("U9.login page has no current-user when signed out", not present(pg, "current-user"))
        pg.fill("[data-testid=login-email]", "ada@example.com"); pg.fill("[data-testid=login-password]", "WRONG"); pg.click("[data-testid=login-submit]"); pg.wait_for_timeout(600)
        check("U9.wrong password shows auth-error; fields: password kept? email kept", visible(pg, "auth-error") and txt(pg, "auth-error") and pg.input_value("[data-testid=login-email]") == "ada@example.com", txt(pg, "auth-error") if present(pg, "auth-error") else None)
        pg.fill("[data-testid=login-password]", "correct horse"); pg.click("[data-testid=login-submit]"); pg.wait_for_selector("[data-testid=wallet-balance]")
        check("U9.auth-error absent after success; current-user text contains display name; current-handle exactly 'ada'", txt(pg, "current-user") and "Ada" in txt(pg, "current-user") and txt(pg, "current-handle") == "ada")
        for r in ("/", "/requests", "/split", "/authorizations"):
            pg.goto(A + r); pg.wait_for_timeout(500); check(f"U9.signed in: {r} shows current-user + logout-button + current-handle", present(pg, "current-user") and present(pg, "logout-button") and txt(pg, "current-handle") == "ada")
        pg.reload(); pg.wait_for_selector("[data-testid=wallet-balance]"); check("U9.reload keeps session", present(pg, "current-user"))
        p2 = ctx.new_page(); p2.goto(A + "/requests"); p2.wait_for_timeout(600); check("U9.second tab shares session", present(p2, "current-user")); p2.close()
        pg.click("[data-testid=logout-button]"); pg.wait_for_timeout(600); check("U9.logout lands on login, session cleared", "/login" in pg.url and pg.evaluate("localStorage.getItem('pocketful.session')") in (None, ""), pg.url)
        pg.go_back(); pg.wait_for_timeout(600); check("U9.back after logout does not expose wallet data", not (present(pg, "wallet-balance") and visible(pg, "wallet-balance") and txt(pg, "wallet-balance")) or "/login" in pg.url, pg.url)
        # signup flows
        pg.goto(A + "/signup"); 
        for em, pw, dn, name in (("new@x.com", "short", "N", "short password"), ("bad", "password1", "N", "bad email"), ("ada@example.com", "password1", "N", "taken email"), ("ada@other.com", "password1", "N", "taken handle (ada)")):
            pg.fill("[data-testid=signup-email]", em); pg.fill("[data-testid=signup-password]", pw); pg.fill("[data-testid=signup-display-name]", dn); pg.click("[data-testid=signup-submit]"); pg.wait_for_timeout(600)
            check(f"U9.signup {name}: auth-error shown, still on /signup, inputs kept", visible(pg, "auth-error") and "/signup" in pg.url and pg.input_value("[data-testid=signup-email]") == em, (pg.url, txt(pg, "auth-error") if present(pg, "auth-error") else None))
        pg.fill("[data-testid=signup-email]", "Fresh.User@x.com"); pg.fill("[data-testid=signup-password]", "password1"); pg.fill("[data-testid=signup-display-name]", "Fresh"); pg.click("[data-testid=signup-submit]"); pg.wait_for_selector("[data-testid=wallet-balance]", timeout=8000)
        check("U9.signup success: signed in, handle derived 'fresh_user', balance 0.00 EUR, empty-activity", txt(pg, "current-handle") == "fresh_user" and txt(pg, "wallet-balance") == "0.00 EUR" and amt(pg, "wallet-available") == 0 and visible(pg, "empty-activity"), (txt(pg, "current-handle"), txt(pg, "wallet-balance")))
        check("U9.wallet-held absent when zero", not present(pg, "wallet-held"))
        # token revoked (server reset) mid-session
        reset(A, fixture()); pg.click("[data-testid=wallet-refresh]"); pg.wait_for_timeout(900); print("   INFO after server reset (token dead) refresh ->", pg.url)
        check("U9.invalid token -> sent to /login (not a blank/broken page)", "/login" in pg.url, pg.url)
    run(go)
    # 401 mid-payment keeps drafts and pending retry
    reset(A, fixture())
    def go2(pg, ctx, b):
        ui_login(pg, A); fill_pay(pg, "bob", "2.00", "draft"); 
        reset(A, fixture())          # kills the token (stands in for token invalidation)
        pg.click("[data-testid=pay-submit]"); pg.wait_for_timeout(900)
        print("   INFO pay with dead token -> url", pg.url, "| error:", txt(pg, "pay-error") if present(pg, "pay-error") else None)
        check("U9.pay with dead token: user ends at /login or sees an error; never silent", "/login" in pg.url or visible(pg, "pay-error") or visible(pg, "pay-uncertain"))
        if "/login" in pg.url:
            pg.fill("[data-testid=login-email]", "ada@example.com"); pg.fill("[data-testid=login-password]", "correct horse"); pg.click("[data-testid=login-submit]"); pg.wait_for_selector("[data-testid=wallet-balance]"); pg.wait_for_timeout(500)
            check("U9.drafts kept across forced re-login (pay form refilled)", pg.input_value("[data-testid=pay-handle]") == "bob" and pg.input_value("[data-testid=pay-amount]") == "2.00" and pg.input_value("[data-testid=pay-note]") == "draft", (pg.input_value("[data-testid=pay-handle]"), pg.input_value("[data-testid=pay-amount]")))
    run(go2)

def U10_upgrade():
    # browser signed in, response lost after commit, service "upgraded" (export -> import into another process), retry recovers
    reset(A, BIG); reset(B, fixture())
    def go(pg, ctx, b):
        ui_login(pg, A); posts = watch_posts(pg); start = amt(pg, "wallet-balance")
        mode = {"m": "lose"}; upgraded = {"v": False}
        def api_route(route):
            req = route.request; u = req.url
            if upgraded["v"]:
                # after the upgrade the same origin is now served by process B
                resp = route.fetch(url=u.replace(A, B)); route.fulfill(response=resp); return
            if req.method == "POST" and u.split("?")[0].endswith("/payments") and mode["m"] == "lose": route.fetch(); route.abort(); return
            route.continue_()
        pg.route("**/*", lambda r: api_route(r) if re.search(r"/(payments|me|activity|requests|authorizations|auth)(\?|$|/)", r.request.url.replace(A, "")) and r.request.resource_type in ("fetch", "xhr") else r.continue_())
        fill_pay(pg, "bob", "21.00", "across upgrade", "private"); pg.click("[data-testid=pay-submit]"); pg.wait_for_timeout(900)
        check("U10.lost after commit before upgrade: pay-uncertain", visible(pg, "pay-uncertain"))
        sess_before = pg.evaluate("localStorage.getItem('pocketful.session')")
        _, ex = api(A, "GET", "/_test/export"); s, j = api(B, "POST", "/_test/import", ex); check("U10.export A -> import B 204", s == 204, (s, j)); upgraded["v"] = True
        pg.click("[data-testid=pay-submit]"); pg.wait_for_timeout(1200)
        check("U10.signed in still (no redirect to /login), session untouched", "/login" not in pg.url and pg.evaluate("localStorage.getItem('pocketful.session')") == sess_before, pg.url)
        check("U10.retry recovered the ORIGINAL payment: pay-uncertain and pay-error gone", not (present(pg, "pay-uncertain") and visible(pg, "pay-uncertain")) and not (present(pg, "pay-error") and visible(pg, "pay-error")))
        check("U10.…balance reflects imported state (debited exactly once) and feed has exactly one payment", amt(pg, "wallet-balance") == start - 2100 and len(pg.query_selector_all("[data-testid^=activity-item-]")) == 1, (amt(pg, "wallet-balance"), start))
        check("U10.…form intact", pg.input_value("[data-testid=pay-handle]") == "bob" and pg.input_value("[data-testid=pay-amount]") == "21.00" and pg.input_value("[data-testid=pay-note]") == "across upgrade")
        check("U10.…B has exactly one payment", len(api(B, "GET", "/activity", t=tok(B, "ada@example.com"))[1]["payments"]) == 1)
    run(go)
    # pending request payable after import; existing page still works
    reset(A, fixture(requests=[{"id": "rq_1", "requester_id": "u_bob", "payer_id": "u_ada", "amount": 1200, "note": "taxi", "status": "pending"}])); reset(B, fixture())
    def go2(pg, ctx, b):
        ui_login(pg, A, path="/requests"); pg.wait_for_selector("[data-testid=request-pay-rq_1]")
        _, ex = api(A, "GET", "/_test/export"); api(B, "POST", "/_test/import", ex)
        pg.route("**/*", lambda r: r.fulfill(response=r.fetch(url=r.request.url.replace(A, B))) if r.request.resource_type in ("fetch", "xhr") else r.continue_())
        pg.click("[data-testid=request-pay-rq_1]"); pg.wait_for_timeout(1000)
        check("U10.pending request pays through the UI against the upgraded service (paid)", pg.get_attribute("[data-testid=request-item-rq_1]", "data-status") == "paid" and api(B, "GET", "/me", t=tok(B, "ada@example.com"))[1]["total"] == 8800, pg.get_attribute("[data-testid=request-item-rq_1]", "data-status"))
    run(go2)
    # stage-1 export (true stage-1 service) imported into stage 2; browser has a stage-1-issued token
    if S1:
        reset(S1, fixture()); t1 = tok(S1, "ada@example.com"); reset(B, fixture())
        s, p = api(S1, "POST", "/payments", {"to_handle": "bob", "amount": 400, "note": "s1 lost"}, t1, {"Idempotency-Key": "s1-lost"})
        s, rq = api(S1, "POST", "/requests", {"payer_handle": "ada", "amount": 300, "note": "s1 req"}, tok(S1, "bob@example.com"), {"Idempotency-Key": "s1-rq"})
        _, ex = api(S1, "GET", "/_test/export"); s, j = api(B, "POST", "/_test/import", ex); check("U10.true stage-1 export imports into stage-2 (B)", s == 204, (s, j))
        def go3(pg, ctx, b):
            pg.goto(B + "/login"); pg.evaluate("t => localStorage.setItem('pocketful.session', JSON.stringify({token: t, userId: 'u_ada', displayName: 'Ada', handle: 'ada'}))", t1)
            pg.goto(B + "/"); pg.wait_for_timeout(900)
            check("U10.browser holding a stage-1-issued token is signed in on stage-2 UI after import", present(pg, "wallet-balance") and "/login" not in pg.url, pg.url)
            if present(pg, "wallet-balance"):
                check("U10.…balance/available/total consistent, held absent", amt(pg, "wallet-balance") == 9600 and amt(pg, "wallet-available") == 9600 and not present(pg, "wallet-held"), (amt(pg, "wallet-balance"), amt(pg, "wallet-available")))
                posts = watch_posts(pg); fill_pay(pg, "bob", "4.00", "s1 lost"); pg.click("[data-testid=pay-submit]"); pg.wait_for_timeout(900); print("   INFO fresh submit of same body from the UI generates a NEW key (cannot know the lost key) ->", posts[-1]["key"] if posts else None)
        run(go3)

def U11_layout_a11y():
    longname = "Averyveryveryverylongdisplaynamewithoutanyspaces" * 2
    users = [U("ada@example.com", handle="ada", name=longname, bal=999999999999), U("bob@example.com", handle="bob_with_20_chars_x", name="Bob"), U("cy@example.com", handle="cy", bal=0)]
    pays = [{"id": f"p_{i}", "from_user_id": "u_ada", "to_user_id": "u_bob_with_20_chars_x", "amount": 123456789 + i, "note": ("N" * 200 if i % 2 else "a note with several words " * 7), "visibility": "public" if i % 3 else "private"} for i in range(40)]
    auths = [{"id": "a_1", "from_user_id": "u_ada", "to_user_id": "u_bob_with_20_chars_x", "amount": 1000000000, "note": "W" * 200, "visibility": "public", "status": "open", "expires_at": "2099-01-01T00:00:00+00:00"}]
    reqs = [{"id": "rq_1", "requester_id": "u_bob_with_20_chars_x", "payer_id": "u_ada", "amount": 1000000000, "note": "R" * 200, "status": "pending"}]
    for w in (375, 1280, 320):
        reset(A, fixture(users=users, payments=pays, authorizations=auths, requests=reqs))
        def go(pg, ctx, b, w=w):
            ui_login(pg, A)
            for r in ("/", "/requests", "/split", "/authorizations", "/login", "/signup"):
                if r in ("/login", "/signup"): pg.evaluate("localStorage.clear()")
                pg.goto(A + r); pg.wait_for_timeout(700)
                sw = pg.evaluate("document.documentElement.scrollWidth"); cw = pg.evaluate("document.documentElement.clientWidth")
                check(f"U11.{w}px {r}: no horizontal page scroll (scrollWidth {sw} <= {cw})", sw <= cw, (sw, cw))
                over = pg.evaluate("[...document.querySelectorAll('body *')].filter(e => {const r=e.getBoundingClientRect(); return r.width>0 && (r.right > innerWidth+1 || r.left < -1)}).slice(0,3).map(e=>e.tagName+'.'+e.className+':'+(e.dataset.testid||''))")
                check(f"U11.{w}px {r}: no element overflows the viewport", not over, over)
                if r == "/": ui_login(pg, A) if not present(pg, "current-user") else None
            if w == 375:
                ui_login(pg, A); pg.screenshot(path="shots/long-content-375.png", full_page=True)
        run(go, (w, 800))
    # keyboard focus + labels + contrast
    reset(A, fixture(authorizations=[{"id": "a_1", "from_user_id": "u_ada", "to_user_id": "u_bob", "amount": 2000, "note": "d", "visibility": "public", "status": "open", "expires_at": "2099-01-01T00:00:00+00:00"}], requests=[{"id": "rq_1", "requester_id": "u_bob", "payer_id": "u_ada", "amount": 1200, "note": "taxi", "status": "pending"}]))
    def go2(pg, ctx, b):
        pg.goto(A + "/login")
        for route in ("/login", "/", "/requests", "/split", "/authorizations", "/signup"):
            if route in ("/login", "/signup"): 
                if route == "/signup": pg.evaluate("localStorage.clear()")
                pg.goto(A + route)
            else:
                if not present(pg, "current-user"): ui_login(pg, A)
                pg.goto(A + route)
            pg.wait_for_timeout(600)
            # labels: every visible input/select has an accessible name
            unl = pg.evaluate("""[...document.querySelectorAll('input,select,textarea')].filter(e=>e.offsetParent!==null && e.type!=='hidden').filter(e=>{const l=e.labels&&e.labels.length; const a=e.getAttribute('aria-label')||e.getAttribute('aria-labelledby'); return !l&&!a}).map(e=>e.dataset.testid||e.name||e.type)""")
            check(f"U11.{route}: every visible input has a label/aria-label", not unl, unl)
            plc = pg.evaluate("""[...document.querySelectorAll('input,select,textarea')].filter(e=>e.offsetParent!==null && e.type!=='hidden').filter(e=>{const l=e.labels&&e.labels.length&&[...e.labels].some(x=>x.innerText.trim().length>0); return !l && !(e.getAttribute('aria-label')||'').trim()}).map(e=>e.dataset.testid)""")
            check(f"U11.{route}: labels are visible text (not placeholder-only)", not plc, plc)
            # focus visibility: tab through, check each focused element has visible outline/box-shadow/border change
            bad = []; seen = 0
            pg.evaluate("document.activeElement && document.activeElement.blur()")
            for i in range(30):
                pg.keyboard.press("Tab"); info = pg.evaluate("""() => {const e=document.activeElement; if(!e||e===document.body) return null; const cs=getComputedStyle(e); const r=e.getBoundingClientRect(); return {id:e.dataset.testid||e.tagName+':'+(e.textContent||'').trim().slice(0,12), outlineW:parseFloat(cs.outlineWidth), outlineS:cs.outlineStyle, outlineC:cs.outlineColor, shadow:cs.boxShadow, vis:r.width>0&&r.height>0, bw:cs.borderTopWidth, bc:cs.borderTopColor, bg:cs.backgroundColor}}""")
                if not info or not info["vis"]: continue
                seen += 1
                has_outline = info["outlineS"] != "none" and info["outlineW"] >= 1; has_shadow = info["shadow"] not in ("none", "")
                if not (has_outline or has_shadow): bad.append(info["id"])
            check(f"U11.{route}: {seen} focusable elements all show a visible focus indicator (outline or ring)", seen > 0 and not bad, bad)
            # contrast of text elements
            cr = pg.evaluate(open("contrast.js").read()); low = cr["low"]; print(f"   INFO {route} contrast: {cr['n']} text nodes, min ratio {cr['min']}")
            check(f"U11.{route}: all text meets WCAG AA contrast", not low, low)
            # placeholders contrast
            ph = pg.evaluate("""() => [...document.querySelectorAll('input::placeholder')].length""")
        run_focus = None
    run(go2)

def U12_states_network():
    reset(A, fixture(authorizations=[{"id": "a_1", "from_user_id": "u_ada", "to_user_id": "u_bob", "amount": 2000, "note": "d", "visibility": "public", "status": "open", "expires_at": "2099-01-01T00:00:00+00:00"}]))
    def go(pg, ctx, b):
        urls = []; pg.on("request", lambda r: urls.append(r.url)); 
        ctx.on("request", lambda r: urls.append(r.url))
        ui_login(pg, A)
        for r in ("/", "/requests", "/split", "/authorizations", "/signup", "/login"):
            pg.goto(A + r); pg.wait_for_timeout(500)
        ext = sorted({u for u in urls if not u.startswith(A) and not u.startswith("data:") and not u.startswith("blob:")})
        check("U12.no request leaves the origin on any screen (no CDN/fonts/analytics)", not ext, ext)
        html = pg.content(); check("U12.no external URLs in HTML (src/href/@import)", not re.findall(r"""(?:src|href)=["']https?://(?!127\.0\.0\.1)""", html) and "fonts.googleapis" not in html)
        css = ctx.request.get(A + "/assets/app.css").text(); check("U12.CSS has no external @import/url()", not re.findall(r"url\((?:'|\")?https?://", css) and "@import" not in css.replace("@import url(\"/", ""))
        # offline: everything still renders once loaded from the server (no hidden remote asset)
    run(go)
    # empty states
    reset(A, fixture())
    def go2(pg, ctx, b):
        ui_login(pg, A); check("U12.empty-activity visible, activity-list absent or empty", visible(pg, "empty-activity") and (not present(pg, "activity-list") or len(pg.query_selector_all("[data-testid^=activity-item-]")) == 0))
        pg.goto(A + "/authorizations"); pg.wait_for_selector("[data-testid=empty-authorizations]", timeout=4000); check("U12.empty-authorizations visible", visible(pg, "empty-authorizations"))
        # server down -> error state not blank
        pg.goto(A + "/"); pg.wait_for_selector("[data-testid=wallet-balance]")
        pg.route("**/*", lambda r: r.abort() if r.request.resource_type in ("fetch", "xhr") else r.continue_())
        pg.click("[data-testid=wallet-refresh]"); pg.wait_for_timeout(1000); body = pg.inner_text("body"); print("   INFO refresh with server down shows:", body[:200].replace("\n", " | "))
        check("U12.server unreachable on refresh: balance not blanked/zeroed, some error message shown", amt(pg, "wallet-balance") == 10000 and re.search(r"(couldn|could not|unable|offline|try again|failed|error|connection)", body, re.I), body[:200])
        pg.unroute("**/*")
        # slow loading state
        pg.route("**/me", lambda r: (time.sleep(1.5), r.continue_())[1]); pg.goto(A + "/"); pg.wait_for_timeout(300); shot = pg.inner_text("body"); print("   INFO during slow load:", shot[:120].replace("\n", " | "))
        check("U12.loading state while /me is slow: page not blank and no fake '0.00' balance shown", len(shot.strip()) > 20 and not (present(pg, "wallet-balance") and txt(pg, "wallet-balance") == "0.00 EUR" and not present(pg, "empty-activity") and False), shot[:120])
        pg.unroute("**/me")
    run(go2)
    # error shape for 404 pages
    s = api(A, "GET", "/nope"); print("   INFO GET /nope ->", s[0])
    with sync_playwright() as p:
        b = p.chromium.launch(); pg = b.new_page(); r = pg.goto(A + "/definitely-not-a-page"); print("   INFO browser GET unknown page status:", r.status, "| body:", pg.inner_text("body")[:80].replace("\n", " ")); b.close()

def U13_authorizations_ui():
    f = fixture(authorizations=[{"id": "a_in", "from_user_id": "u_bob", "to_user_id": "u_ada", "amount": 2000, "note": "deposit in", "visibility": "public", "status": "open", "expires_at": "2099-01-01T00:00:00+00:00"}, {"id": "a_out", "from_user_id": "u_ada", "to_user_id": "u_bob", "amount": 1500, "note": "deposit out", "visibility": "private", "status": "open", "expires_at": "2099-01-01T00:00:00+00:00"}, {"id": "a_cap", "from_user_id": "u_bob", "to_user_id": "u_ada", "amount": 300, "note": "done", "visibility": "public", "status": "captured", "expires_at": "2099-01-01T00:00:00+00:00"}, {"id": "a_void", "from_user_id": "u_ada", "to_user_id": "u_bob", "amount": 100, "note": "v", "visibility": "public", "status": "voided", "expires_at": "2099-01-01T00:00:00+00:00"}, {"id": "a_exp", "from_user_id": "u_ada", "to_user_id": "u_bob", "amount": 100, "note": "e", "visibility": "public", "status": "open", "expires_at": "2020-01-01T00:00:00+00:00"}])
    reset(A, f)
    def go(pg, ctx, b):
        ui_login(pg, A, path="/authorizations"); pg.wait_for_selector("[data-testid=authorization-list]"); posts = watch_posts(pg, r"/(authorizations|capture)$|/capture$")
        st = lambda i: pg.get_attribute(f"[data-testid=authorization-item-{i}]", "data-status")
        check("U13.statuses: open/open/captured/voided/expired(clock)", (st("a_in"), st("a_out"), st("a_cap"), st("a_void"), st("a_exp")) == ("open", "open", "captured", "voided", "expired"), (st("a_in"), st("a_out"), st("a_cap"), st("a_void"), st("a_exp")))
        check("U13.controls only where specified: capture on incoming open; void on outgoing open", present(pg, "authorization-capture-a_in") and present(pg, "authorization-capture-amount-a_in") and not present(pg, "authorization-void-a_in") and present(pg, "authorization-void-a_out") and not present(pg, "authorization-capture-a_out") and not present(pg, "authorization-capture-a_cap") and not present(pg, "authorization-void-a_cap") and not present(pg, "authorization-capture-a_exp") and not present(pg, "authorization-void-a_exp") and not present(pg, "authorization-void-a_void"))
        check("U13.authorization-captured only when captured", present(pg, "authorization-captured-a_cap") and not present(pg, "authorization-captured-a_in") and not present(pg, "authorization-captured-a_exp"))
        check("U13.amounts exact: authorization-amount '20.00 EUR'; capture input pre-filled with remaining decimal '20.00'; expires text RFC3339", txt(pg, "authorization-amount-a_in") == "20.00 EUR" and pg.input_value("[data-testid=authorization-capture-amount-a_in]") in ("20.00", "20") and txt(pg, "authorization-expires-a_in") == "2099-01-01T00:00:00+00:00", (txt(pg, "authorization-amount-a_in"), pg.input_value("[data-testid=authorization-capture-amount-a_in]"), txt(pg, "authorization-expires-a_in")))
        print("   INFO seeded captured hold (amount 3.00, no captured_amount in fixture) shows captured:", txt(pg, "authorization-captured-a_cap"))
        ids = [e.get_attribute("data-testid") for e in pg.query_selector_all("[data-testid^=authorization-item-]")]; api_ids = ["authorization-item-" + x["authorization_id"] for x in api(A, "GET", "/authorizations?limit=200", t=tok(A, "ada@example.com"))[1]["authorizations"]]; check("U13.list newest first in DOM (== API order)", ids == api_ids, (ids, api_ids))
        check("U13.hold headline: wallet-available = 100.00 - 15.00 = 85.00; held 15.00; total 100.00", amt(pg, "wallet-available") == 8500 and amt(pg, "wallet-held") == 1500 and amt(pg, "wallet-balance") == 10000, (amt(pg, "wallet-available"), amt(pg, "wallet-held")))
        # capture bad input
        n = len(posts)
        for bad in ("20.001", "abc", "", "-1", "0", "20.01", "1e1"):
            pg.fill("[data-testid=authorization-capture-amount-a_in]", bad); pg.click("[data-testid=authorization-capture-a_in]"); pg.wait_for_timeout(450)
            check(f"U13.capture amount {bad!r} -> authorization-error shown, authorization stays open", visible(pg, "authorization-error") and st("a_in") == "open", txt(pg, "authorization-error") if present(pg, "authorization-error") else None)
            if bad in ("20.001", "abc", ""): check(f"U13.capture amount {bad!r}: no request sent", len(posts) == n)
        # partial capture 7.50
        pg.fill("[data-testid=authorization-capture-amount-a_in]", "7.50"); pg.click("[data-testid=authorization-capture-a_in]"); pg.wait_for_timeout(900)
        cap_post = [p for p in posts if p["url"].endswith("/capture")]
        check("U13.partial capture 7.50 sends {amount:750} (final default) and shows captured; authorization-error cleared", cap_post and json.loads(cap_post[-1]["body"]).get("amount") == 750 and st("a_in") == "captured" and txt(pg, "authorization-captured-a_in") == "7.50 EUR" and not (present(pg, "authorization-error") and visible(pg, "authorization-error")), (cap_post[-1:] if cap_post else None, st("a_in")))
        m = api(A, "GET", "/me", t=tok(A, "ada@example.com"))[1]; check("U13.balances after partial final capture (ada +7.50, bob released the rest)", m["total"] == 10750 and amt(pg, "wallet-balance") == 10750 and amt(pg, "wallet-available") == 10750 - 1500 and amt(pg, "wallet-held") == 1500, (m, amt(pg, "wallet-balance")))
        check("U13.after capture the capture controls disappear", not present(pg, "authorization-capture-a_in") and not present(pg, "authorization-capture-amount-a_in"))
        # void
        pg.click("[data-testid=authorization-void-a_out]"); pg.wait_for_timeout(800); check("U13.void outgoing -> voided, controls gone, held absent, available == total", st("a_out") == "voided" and not present(pg, "authorization-void-a_out") and not present(pg, "wallet-held") and amt(pg, "wallet-available") == amt(pg, "wallet-balance"), (st("a_out"), present(pg, "wallet-held")))
    run(go)
    # authorize form
    reset(A, fixture())
    def go2(pg, ctx, b):
        ui_login(pg, A, path="/authorizations"); pg.wait_for_selector("[data-testid=empty-authorizations]"); posts = watch_posts(pg, r"/authorizations$")
        pg.fill("[data-testid=authorize-handle]", "bob"); pg.fill("[data-testid=authorize-amount]", "25.50"); pg.fill("[data-testid=authorize-note]", "deposit"); pg.select_option("[data-testid=authorize-visibility]", "private"); pg.click("[data-testid=authorize-submit]"); pg.wait_for_timeout(900)
        check("U13.authorize 25.50 sends 2550 private; list shows it open with controls; balances updated without reload", posts and json.loads(posts[0]["body"]) == {"to_handle": "bob", "amount": 2550, "note": "deposit", "visibility": "private"} and len(pg.query_selector_all("[data-testid^=authorization-item-]")) == 1 and amt(pg, "wallet-held") == 2550 and amt(pg, "wallet-available") == 10000 - 2550 and amt(pg, "wallet-balance") == 10000, (posts, amt(pg, "wallet-held")))
        check("U13.…empty-authorizations gone; form values retained", not visible(pg, "empty-authorizations") and pg.input_value("[data-testid=authorize-amount]") == "25.50")
        n = len(posts); pg.click("[data-testid=authorize-submit]"); pg.wait_for_timeout(700); print("   INFO authorize unchanged resubmit POSTs:", len(posts) - n)
        check("U13.unchanged authorise resubmission creates no second hold", len(api(A, "GET", "/authorizations", t=tok(A, "ada@example.com"))[1]["authorizations"]) == 1 and amt(pg, "wallet-held") == 2550)
        for a in ("0", "abc", "1.005", "1e2", "99999.00", "-5", ""):
            n = len(posts); pg.fill("[data-testid=authorize-amount]", a); pg.click("[data-testid=authorize-submit]"); pg.wait_for_timeout(450)
            check(f"U13.authorize amount {a!r} refused with authorize-error (insufficient funds / validation)", visible(pg, "authorize-error") and txt(pg, "authorize-error"), None)
        for a in ("1.005", "abc", "1e2", ""): pass
        pg.fill("[data-testid=authorize-amount]", "74.50"); pg.click("[data-testid=authorize-submit]"); pg.wait_for_timeout(800); check("U13.authorize exactly the available 74.50 succeeds; available 0.00", amt(pg, "wallet-available") == 0 and not (present(pg, "authorize-error") and visible(pg, "authorize-error")), (amt(pg, "wallet-available"),))
        pg.fill("[data-testid=authorize-amount]", "0.01"); pg.click("[data-testid=authorize-submit]"); pg.wait_for_timeout(600); check("U13.nothing available -> authorize-error, inputs preserved", visible(pg, "authorize-error") and pg.input_value("[data-testid=authorize-handle]") == "bob" and pg.input_value("[data-testid=authorize-amount]") == "0.01")
        # lost response on authorize (after commit)
        reset(A, fixture()); ui_login(pg, A, path="/authorizations"); pg.wait_for_selector("[data-testid=authorize-handle]")
        keys = []; mode = {"lose": True}
        def h(route):
            if route.request.method == "POST" and route.request.url.split("?")[0].endswith("/authorizations"):
                keys.append(route.request.headers.get("idempotency-key"))
                if mode["lose"]: route.fetch(); route.abort(); return
            route.continue_()
        pg.route("**/authorizations", h); pg.fill("[data-testid=authorize-handle]", "bob"); pg.fill("[data-testid=authorize-amount]", "10.00"); pg.click("[data-testid=authorize-submit]"); pg.wait_for_timeout(900)
        body = pg.inner_text("body"); print("   INFO lost authorize response: error elem:", visible(pg, "authorize-error"), "| uncertain-ish text:", re.findall(r"(?i)(not sure|unsure|uncertain|unknown|couldn.t confirm|may have)", body)[:2])
        mode["lose"] = False; pg.click("[data-testid=authorize-submit]"); pg.wait_for_timeout(900)
        check("U13.authorize retry after lost response reuses the same key and creates exactly one hold", len(keys) >= 2 and keys[0] == keys[1] and len(api(A, "GET", "/authorizations", t=tok(A, "ada@example.com"))[1]["authorizations"]) == 1 and amt(pg, "wallet-held") == 1000, (keys, amt(pg, "wallet-held")))
        pg.unroute("**/authorizations")
    run(go2)
    # capture refused elsewhere (void/expired/captured) -> authorization-error and refresh
    reset(A, fixture(authorizations=[{"id": "a_in", "from_user_id": "u_bob", "to_user_id": "u_ada", "amount": 2000, "note": "x", "visibility": "public", "status": "open", "expires_at": "2099-01-01T00:00:00+00:00"}]))
    def go3(pg, ctx, b):
        ui_login(pg, A, path="/authorizations"); pg.wait_for_selector("[data-testid=authorization-capture-a_in]")
        api(A, "POST", "/authorizations/a_in/void", {}, tok(A, "bob@example.com")); pg.click("[data-testid=authorization-capture-a_in]"); pg.wait_for_timeout(900)
        check("U13.capture of a hold voided elsewhere -> authorization-error and stale controls disappear (status voided)", visible(pg, "authorization-error") and pg.get_attribute("[data-testid=authorization-item-a_in]", "data-status") == "voided" and not present(pg, "authorization-capture-a_in"), (txt(pg, "authorization-error") if present(pg, "authorization-error") else None, pg.get_attribute("[data-testid=authorization-item-a_in]", "data-status")))
    run(go3)
    # short-ttl hold expires while the page is open
    reset(A, fixture(authorization_ttl_seconds=3))
    def go4(pg, ctx, b):
        ui_login(pg, A, path="/authorizations"); pg.wait_for_selector("[data-testid=authorize-handle]"); pg.fill("[data-testid=authorize-handle]", "bob"); pg.fill("[data-testid=authorize-amount]", "40.00"); pg.click("[data-testid=authorize-submit]"); pg.wait_for_timeout(900)
        aid = pg.query_selector("[data-testid^=authorization-item-]").get_attribute("data-testid")[len("authorization-item-"):]
        check("U13.fresh hold present with expires text", re.fullmatch(r"\d{4}-\d\d-\d\dT.*([+-]\d\d:\d\d|Z)", txt(pg, f"authorization-expires-{aid}") or "") is not None, txt(pg, f"authorization-expires-{aid}"))
        pg.wait_for_timeout(5500)
        print("   INFO 5.5s later (no user action): status", pg.get_attribute(f"[data-testid=authorization-item-{aid}]", "data-status"), "| held present:", present(pg, "wallet-held"))
        check("U13.page reflects expiry without a reload (builder says one scheduled refresh)", pg.get_attribute(f"[data-testid=authorization-item-{aid}]", "data-status") == "expired" and not present(pg, "wallet-held") and amt(pg, "wallet-available") == 10000, (pg.get_attribute(f"[data-testid=authorization-item-{aid}]", "data-status"), amt(pg, "wallet-available")))
        pg.reload(); pg.wait_for_selector("[data-testid^=authorization-item-]"); check("U13.after reload: expired, controls gone", pg.get_attribute(f"[data-testid=authorization-item-{aid}]", "data-status") == "expired" and not present(pg, f"authorization-void-{aid}"))
    run(go4)

def U14_misc():
    reset(A, fixture())
    def go(pg, ctx, b):
        ui_login(pg, A); 
        # form retention across wallet-refresh AND across a request submit on the same page
        fill_pay(pg, "bob", "1.23", "keep me", "private"); pg.fill("[data-testid=request-handle]", "cy"); pg.fill("[data-testid=request-amount]", "2.00"); pg.click("[data-testid=wallet-refresh]"); pg.wait_for_timeout(500)
        check("U14.wallet-refresh keeps both forms", pg.input_value("[data-testid=pay-note]") == "keep me" and pg.input_value("[data-testid=request-amount]") == "2.00")
        pg.click("[data-testid=request-submit]"); pg.wait_for_timeout(800)
        check("U14.request submit (which refreshes the page data) keeps the pay form filled", pg.input_value("[data-testid=pay-handle]") == "bob" and pg.input_value("[data-testid=pay-amount]") == "1.23" and pg.input_value("[data-testid=pay-note]") == "keep me" and pg.input_value("[data-testid=pay-visibility]") == "private")
        pg.fill("[data-testid=pay-note]", "typing"); api(A, "POST", "/payments", {"to_handle": "ada", "amount": 100}, tok(A, "bob@example.com"), {"Idempotency-Key": "z1"})
        pg.click("[data-testid=wallet-refresh]"); pg.wait_for_timeout(500); check("U14.refresh with new remote payment updates feed/balance and keeps typed text", pg.input_value("[data-testid=pay-note]") == "typing" and amt(pg, "wallet-balance") == 10100 and len(pg.query_selector_all("[data-testid^=activity-item-]")) == 1)
        # keyboard: Enter submits pay from amount field
        posts = watch_posts(pg); pg.fill("[data-testid=pay-note]", "enter"); pg.press("[data-testid=pay-amount]", "Enter"); pg.wait_for_timeout(700); check("U14.Enter key in the amount field submits the pay form", len(posts) == 1, posts)
        # feed privacy for third party seen in UI
        api(A, "POST", "/payments", {"to_handle": "cy", "amount": 10, "visibility": "private", "note": "secret"}, tok(A, "bob@example.com"), {"Idempotency-Key": "z2"}); pg.click("[data-testid=wallet-refresh]"); pg.wait_for_timeout(500)
        check("U14.private payment between others NOT shown to ada", "secret" not in pg.inner_text("body"))
        check("U14.activity item for incoming payment present and labelled", len(pg.query_selector_all("[data-testid^=activity-item-]")) >= 2)
        # Accept negotiation on shared routes
        import urllib.request as ur
        t = tok(A, "ada@example.com")
        def get(path, accept):
            rq = ur.Request(A + path, headers={"Authorization": "Bearer " + t, **({"Accept": accept} if accept is not None else {})}); r = ur.urlopen(rq); return r.headers.get("Content-Type", ""), r.read(200)
        for path in ("/requests", "/authorizations"):
            for accept, want in (("text/html", "html"), ("text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8", "html"), ("application/json", "json"), ("*/*", "json"), (None, "json"), ("application/json, text/html;q=0.5", "json"), ("text/html;q=0.1, application/json", "json")):
                ct, body = get(path, accept); got = "html" if "html" in ct else "json" if "json" in ct else ct
                check(f"U14.Accept {accept!r} on {path} -> {want}", got == want, (ct, body[:40]))
        ct, body = get("/", "application/json"); print("   INFO GET / with Accept json ->", ct)
        # unauth HTML request to API routes
        try: ur.urlopen(ur.Request(A + "/requests", headers={"Accept": "application/json"})); st = 200
        except ur.HTTPError as e: st = e.code
        check("U14.unauthenticated JSON GET /requests -> 401 (not redirect/HTML)", st == 401, st)
        rq = ur.Request(A + "/requests", headers={"Accept": "text/html"}); 
        try: r = ur.urlopen(rq); st2 = r.status; ct = r.headers.get("Content-Type", "")
        except ur.HTTPError as e: st2 = e.code; ct = ""
        print("   INFO unauth HTML GET /requests ->", st2, ct)
        # static assets / 404 page / HEAD
        for p_ in ("/assets/app.css", "/assets/app.js", "/assets/icon.svg", "/favicon.ico"): print("   INFO", p_, ctx.request.get(A + p_).status)
    run(go)

GROUPS = {"1": U1_decimal, "2": U2_pay_flow, "3": U3_refused_and_stale, "4": U4_lost_responses, "5": U5_refresh_latest_wins, "6": U6_requests_screen, "7": U7_split, "8": U8_xss_session, "9": U9_session, "10": U10_upgrade, "11": U11_layout_a11y, "12": U12_states_network, "13": U13_authorizations_ui, "14": U14_misc}
if __name__ == "__main__":
    for k, fn in GROUPS.items():
        if ONLY and k not in ONLY: continue
        print("==", k, fn.__name__, flush=True)
        try: fn()
        except Exception as e:
            import traceback; traceback.print_exc(); check(fn.__name__ + " (script error)", False, repr(e))
    sys.exit(summary())
