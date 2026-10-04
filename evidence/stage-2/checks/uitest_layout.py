"""Stage 2 UI checks (real browser): responsive layout at 375 and 1280 CSS px, keyboard focus, labels, contrast,
no runtime network, human-readable formatting, distinct states, console cleanliness."""
import json
import re
import unittest
import urllib.parse

from ui_lib import *

JS_OVERFLOW = """() => {
  const de = document.documentElement, W = window.innerWidth;
  const clipped = e => { for (let p = e.parentElement; p && p !== document.body; p = p.parentElement) {
      const o = getComputedStyle(p).overflowX; if (o === 'auto' || o === 'scroll' || o === 'hidden') return true; } return false; };
  const off = [];
  for (const e of document.querySelectorAll('body *')) {
    const r = e.getBoundingClientRect(); if (!r.width || !r.height) continue;
    const s = getComputedStyle(e); if (s.visibility === 'hidden' || s.display === 'none' || s.position === 'fixed') continue;
    if (r.right > W + 1 && !clipped(e)) off.push((e.getAttribute('data-testid') || e.tagName.toLowerCase()) + ' right=' + Math.round(r.right));
  }
  return {scrollW: de.scrollWidth, bodyScrollW: document.body.scrollWidth, innerW: W, clientW: de.clientWidth, off: off.slice(0, 8)};
}"""

JS_LABELS = """() => {
  const bad = [];
  const vis = l => { const r = l.getBoundingClientRect(), s = getComputedStyle(l);
    return r.width > 1 && r.height > 1 && r.left > -100 && s.visibility !== 'hidden' && s.display !== 'none' && parseFloat(s.opacity) > 0 &&
           parseFloat(s.fontSize) >= 10 && !/rect\\(0/.test(s.clip) && (l.innerText || '').trim().length > 0; };
  for (const e of document.querySelectorAll('input, select, textarea')) {
    if (['hidden', 'submit', 'button'].includes(e.type)) continue;
    const r = e.getBoundingClientRect(), s = getComputedStyle(e); if (!r.width || s.display === 'none' || s.visibility === 'hidden') continue;
    let ok = [...(e.labels || [])].some(vis);
    const lb = e.getAttribute('aria-labelledby'); if (!ok && lb) ok = lb.split(/\\s+/).map(i => document.getElementById(i)).filter(Boolean).some(vis);
    if (!ok) bad.push((e.getAttribute('data-testid') || e.name || e.tagName) + (e.placeholder ? ' (placeholder only)' : ''));
  }
  return bad;
}"""

JS_CONTRAST = """() => {
  const cv = document.createElement('canvas'); cv.width = cv.height = 1; const cx = cv.getContext('2d', {willReadFrequently: true});
  const rgba = c => { cx.clearRect(0, 0, 1, 1); cx.fillStyle = '#000'; cx.fillStyle = c; cx.fillRect(0, 0, 1, 1); const d = cx.getImageData(0, 0, 1, 1).data; return [d[0], d[1], d[2], d[3] / 255]; };
  const over = (f, b) => { const a = f[3]; return [f[0] * a + b[0] * (1 - a), f[1] * a + b[1] * (1 - a), f[2] * a + b[2] * (1 - a), 1]; };
  const lum = c => { const t = c.slice(0, 3).map(v => { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); }); return 0.2126 * t[0] + 0.7152 * t[1] + 0.0722 * t[2]; };
  const bgOf = el => { const layers = []; for (let e = el; e; e = e.parentElement) { const s = getComputedStyle(e);
      if (s.backgroundImage !== 'none') return null; const c = rgba(s.backgroundColor); layers.push(c); if (c[3] >= 1) break; }
    let out = [255, 255, 255, 1]; for (let i = layers.length - 1; i >= 0; i--) out = over(layers[i], out); return out; };
  const bad = [], seen = new Set();
  const test = (el, textLen) => { const s = getComputedStyle(el); if (s.visibility === 'hidden' || s.display === 'none' || el.disabled || parseFloat(s.opacity) === 0) return;
    const r = el.getBoundingClientRect(); if (r.width < 2 || r.height < 2) return;
    let op = 1; for (let p = el; p; p = p.parentElement) op *= parseFloat(getComputedStyle(p).opacity); if (op < 1) return;
    const bg = bgOf(el); if (!bg) return; const fg = over(rgba(s.color), bg);
    const L1 = lum(fg), L2 = lum(bg), ratio = (Math.max(L1, L2) + 0.05) / (Math.min(L1, L2) + 0.05);
    const px = parseFloat(s.fontSize), large = px >= 24 || (px >= 18.66 && parseInt(s.fontWeight) >= 700); const need = large ? 3 : 4.5;
    if (ratio < need) { const k = el.tagName + (el.getAttribute('data-testid') || '') + s.color + s.backgroundColor; if (!seen.has(k)) { seen.add(k);
      bad.push({el: (el.getAttribute('data-testid') || el.tagName.toLowerCase()), text: (el.innerText || el.value || '').trim().slice(0, 30), ratio: Math.round(ratio * 100) / 100, need}); } } };
  for (const el of document.querySelectorAll('body *')) {
    if (['SCRIPT', 'STYLE'].includes(el.tagName)) continue;
    const own = [...el.childNodes].some(n => n.nodeType === 3 && n.textContent.trim().length > 0);
    if (own) test(el);
    else if (['INPUT', 'SELECT', 'TEXTAREA'].includes(el.tagName) && (el.value || '').length) test(el);
  }
  return bad.slice(0, 12);
}"""

JS_FOCUS = """() => {
  const e = document.activeElement; if (!e || e === document.body) return null;
  const snap = () => { const s = getComputedStyle(e); return [s.outlineStyle, s.outlineWidth, s.outlineColor, s.boxShadow, s.borderTopColor, s.backgroundColor, s.textDecorationLine, s.color].join('|'); };
  const s = getComputedStyle(e); const ow = parseFloat(s.outlineWidth) || 0;
  const strong = (s.outlineStyle !== 'none' && ow >= 2) || (s.boxShadow && s.boxShadow !== 'none');
  const before = snap(); e.blur(); const after = snap(); e.focus({focusVisible: true});
  return {el: e.getAttribute('data-testid') || e.tagName.toLowerCase() + (e.getAttribute('href') ? '[' + e.getAttribute('href') + ']' : ''), strong, changed: before !== after};
}"""


def stress_fixture():
    long_handle = "abcdefghijklmnopqrst"
    longnote = "W" * 200
    users = [user("ada", 123456789012), user("bob", 2500), user(long_handle, 5), user("cy", 0), user("dee", 0), user("eve", 0), user("op", 0)]
    users[0]["display_name"] = "Ada Lovelace-Montgomery the Very Long Named"
    payments = [{"id": "p_%d" % i, "from_user_id": "u_ada", "to_user_id": "u_" + long_handle, "amount": 123456789 + i, "note": longnote if i % 3 == 0 else "note %d with spaces" % i,
                 "visibility": "public" if i % 2 else "private"} for i in range(40)]
    requests = [{"id": "rq_%d" % i, "requester_id": "u_" + long_handle, "payer_id": "u_ada", "amount": 987654321 + i, "note": longnote, "status": "pending"}
                for i in range(6)] + [{"id": "rq_x%d" % i, "requester_id": "u_ada", "payer_id": "u_bob", "amount": 100 + i, "note": longnote, "status": "pending"}
                                      for i in range(4)]
    fx = base_fixture(users=users, payments=payments, requests=requests)
    fx["authorizations"] = [seed_auth("a_%d" % i, "ada", long_handle, 1000000 + i, note=longnote) for i in range(4)] + \
                           [seed_auth("a_in%d" % i, long_handle, "ada", 5, note=longnote) for i in range(1)]
    fx["authorizations"][-1]["amount"] = 5
    return fx


class _Layout:
    VIEW = None
    NAME = ""

    def fixture(self):
        return stress_fixture()

    def check_scroll(self, label):
        r = self.page.evaluate(JS_OVERFLOW)
        self.assertLessEqual(r["scrollW"], r["innerW"] + 1, "%s %s: horizontal scroll (scrollWidth %s > %s) %s" % (self.NAME, label, r["scrollW"], r["innerW"], r["off"]))
        self.assertLessEqual(r["bodyScrollW"], r["innerW"] + 1, "%s %s: body wider than viewport %s" % (self.NAME, label, r["off"]))
        self.assertEqual(r["off"], [], "%s %s: elements poke out of the viewport" % (self.NAME, label))

    def test_no_horizontal_scroll_signed_in_routes(self):
        "[UI-120] every required route has no horizontal page scroll with long notes, long handles, large amounts and many items"
        self.login("ada")
        for path in ("/", "/requests", "/split", "/authorizations"):
            self.goto(path)
            self.page.wait_for_timeout(300)
            self.check_scroll(path)

    def test_no_horizontal_scroll_signed_out_routes(self):
        "[UI-121] /login and /signup have no horizontal page scroll"
        for path in ("/login", "/signup"):
            self.page.goto(path)
            self.page.wait_for_timeout(200)
            self.check_scroll(path)

    def test_no_horizontal_scroll_with_messages_open(self):
        "[UI-122] error and uncertain messages and the split preview with many handles do not widen the page"
        self.login("ada")
        self.goto("/")
        self.fill_pay("a" * 20, "99999999.99", "q" * 200, "private")
        self.L("pay-submit").click()
        expect(self.L("pay-error")).to_be_visible()
        self.check_scroll("/ pay-error")
        self.page.route(re.compile(r".*/payments(\?.*)?$"), lambda r: r.abort("connectionreset") if r.request.method == "POST" else r.continue_())
        self.fill_pay("bob", "1.00", "w" * 200)
        self.L("pay-submit").click()
        expect(self.L("pay-uncertain")).to_be_visible()
        self.check_scroll("/ pay-uncertain")
        self.goto("/split")
        self.L("split-amount").fill("12345678.90")
        self.L("split-handles").fill(",".join(["abcdefghijklmnopqrst", "bob", "cy", "dee", "eve", "op", "u1", "u2", "u3", "u4", "u5", "u6"]))
        self.page.wait_for_timeout(300)
        self.check_scroll("/split preview")
        self.L("split-submit").click()
        expect(self.L("split-error")).to_be_visible()
        self.check_scroll("/split error")

    def test_key_controls_visible_without_scrolling_sideways(self):
        "[UI-123] the headline available amount and the pay/request/split/authorize controls are inside the viewport horizontally"
        self.login("ada")
        self.goto("/")
        for t in ("wallet-available", "pay-handle", "pay-amount", "pay-submit", "request-submit", "wallet-refresh"):
            box = self.L(t).first.bounding_box()
            self.assertIsNotNone(box, t)
            self.assertGreaterEqual(box["x"], -1, t)
            self.assertLessEqual(box["x"] + box["width"], self.VIEW["width"] + 1, t)
        self.assertGreaterEqual(self.L("pay-submit").first.bounding_box()["height"], 28)
        if self.VIEW["width"] <= 400:
            fs = self.L("pay-amount").evaluate("e => parseFloat(getComputedStyle(e).fontSize)")
            self.assertGreaterEqual(fs, 16, "inputs under 16px zoom the page on phones")

    def test_viewport_meta_and_language(self):
        "[UI-124] pages declare a responsive viewport and a document language"
        self.login("ada")
        for path in ("/", "/requests", "/split", "/authorizations"):
            self.goto(path)
            vp = self.page.evaluate("() => (document.querySelector('meta[name=viewport]') || {}).content || ''")
            self.assertIn("width=device-width", vp.replace(" ", ""), path)
            self.assertTrue(self.page.evaluate("() => document.documentElement.lang"), path)
            self.assertTrue(self.page.title().strip(), path)

    def test_keyboard_focus_is_visible(self):
        "[UI-125] tabbing through every screen: each focused control shows a visible focus indicator (outline >= 2px, box-shadow, or a style change)"
        self.login("ada")
        bad = []
        for path in ("/", "/requests", "/split", "/authorizations"):
            self.goto(path)
            self.page.wait_for_timeout(200)
            self.page.evaluate("() => { document.activeElement && document.activeElement.blur(); window.scrollTo(0, 0); }")
            seen = set()
            for i in range(60):
                self.page.keyboard.press("Tab")
                r = self.page.evaluate(JS_FOCUS)
                if r is None:
                    continue
                key = r["el"] + str(i // 1000)
                if r["el"] in seen:
                    break
                seen.add(r["el"])
                if not (r["strong"] or r["changed"]):
                    bad.append((path, r["el"]))
            self.assertGreaterEqual(len(seen), 5, "keyboard cannot reach the controls on " + path)
        self.assertEqual(bad, [], "no visible focus indicator")
        for path in ("/login", "/signup"):
            self.page.context.clear_cookies()
            self.page.goto(path)
            self.page.evaluate("() => localStorage.clear()")
            self.page.goto(path)
            for i in range(8):
                self.page.keyboard.press("Tab")
                r = self.page.evaluate(JS_FOCUS)
                if r:
                    self.assertTrue(r["strong"] or r["changed"], (path, r["el"]))

    def test_inputs_have_visible_labels(self):
        "[UI-126] every visible input/select has a visible associated label (placeholder or aria-label alone does not count)"
        for path in ("/login", "/signup"):
            self.page.context.clear_cookies()
            self.page.goto(path)
            self.page.evaluate("() => localStorage.clear()")
            self.page.goto(path)
            self.assertEqual(self.page.evaluate(JS_LABELS), [], path)
        self.login("ada")
        for path in ("/", "/requests", "/split", "/authorizations"):
            self.goto(path)
            self.page.wait_for_timeout(200)
            self.assertEqual(self.page.evaluate(JS_LABELS), [], path)

    def test_text_contrast(self):
        "[UI-127] text and control contrast >= 4.5:1 (3:1 for large text) on every screen, including error and uncertain messages"
        for path in ("/login", "/signup"):
            self.page.context.clear_cookies()
            self.page.goto(path)
            self.page.evaluate("() => localStorage.clear()")
            self.page.goto(path)
            self.assertEqual(self.page.evaluate(JS_CONTRAST), [], path)
        self.login("ada")
        for path in ("/", "/requests", "/split", "/authorizations"):
            self.goto(path)
            self.page.wait_for_timeout(250)
            self.assertEqual(self.page.evaluate(JS_CONTRAST), [], path)
        self.goto("/")
        self.fill_pay("bob", "9999999999.99", "x")
        self.L("pay-submit").click()
        expect(self.L("pay-error")).to_be_visible()
        self.assertEqual(self.page.evaluate(JS_CONTRAST), [], "pay-error state")
        self.page.route(re.compile(r".*/payments(\?.*)?$"), lambda r: r.abort("connectionreset") if r.request.method == "POST" else r.continue_())
        self.fill_pay("bob", "1.00", "u")
        self.L("pay-submit").click()
        expect(self.L("pay-uncertain")).to_be_visible()
        self.assertEqual(self.page.evaluate(JS_CONTRAST), [], "pay-uncertain state")


class Desktop1280(_Layout, Ui):
    VIEW = {"width": 1280, "height": 800}
    NAME = "1280px"


class Phone375(_Layout, Ui):
    VIEW = {"width": 375, "height": 740}
    NAME = "375px"


class Tablet768(_Layout, Ui):
    VIEW = {"width": 768, "height": 900}
    NAME = "768px"


class Quality(Ui):
    def fixture(self):
        fx = base_fixture()
        fx["authorizations"] = [seed_auth("a_1", "ada", "bob", 2000, note="deposit")]
        return fx

    def visit_all(self):
        self.page.goto("/login")
        self.page.goto("/signup")
        self.login("ada")
        pay("bob", "ada", 100, note="hello")
        for path in ("/", "/requests", "/split", "/authorizations"):
            self.goto(path)
            self.page.wait_for_timeout(300)

    def test_nothing_reaches_the_network(self):
        "[UI-130] every request the pages make goes to the service's own origin: no CDN fonts, scripts, styles or images"
        base = urllib.parse.urlparse(BASE).netloc
        foreign = []
        self.page.on("request", lambda r: foreign.append(r.url) if urllib.parse.urlparse(r.url).scheme in ("http", "https", "ws", "wss") and urllib.parse.urlparse(r.url).netloc != base else None)
        self.visit_all()
        self.assertEqual(foreign, [])

    def test_no_console_errors_or_failed_assets(self):
        "[UI-131] no uncaught page errors, console errors or failed same-origin asset loads while using the screens"
        errors, bad = [], []
        self.page.on("pageerror", lambda e: errors.append(str(e)))
        self.page.on("console", lambda m: errors.append(m.text) if m.type == "error" and "favicon" not in m.text and "401" not in m.text else None)
        self.page.on("response", lambda r: bad.append((r.status, r.url)) if r.status >= 400 and r.request.resource_type in ("script", "stylesheet", "font", "image", "document")
                     and "favicon" not in r.url else None)
        self.visit_all()
        self.assertEqual(errors, [])
        self.assertEqual(bad, [])

    def test_people_first_formatting(self):
        "[UI-132] visible text has no raw ids, JSON, 'undefined'/'NaN'/'null', or ISO timestamps (except the required expires_at on authorizations)"
        self.visit_all()
        raw_re = re.compile(r"\b(?:u|p|rq|a|sp|st)_[a-z0-9]+\b|undefined|NaN|\bnull\b|\[object|\{\"|\\u00|\d{4}-\d{2}-\d{2}T\d{2}:\d{2}")
        for path in ("/", "/requests", "/split", "/authorizations"):
            self.goto(path)
            self.page.wait_for_timeout(250)
            body = self.page.evaluate("() => document.body.innerText")
            if path == "/authorizations":
                body = re.sub(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})", "", body)  # authorization-expires is RFC 3339 by contract
            m = raw_re.findall(body)
            self.assertEqual(m, [], "%s shows technical text: %r" % (path, m[:5]))

    def test_one_primary_action_is_identifiable(self):
        "[UI-133] the primary action (pay) is styled differently from secondary controls (refresh, request actions)"
        self.login("ada")
        st = lambda t: self.L(t).first.evaluate("e => { const s = getComputedStyle(e); return s.backgroundColor + '|' + s.color + '|' + s.fontWeight }")
        self.assertNotEqual(st("pay-submit"), st("wallet-refresh"))

    def test_available_is_the_headline(self):
        "[UI-134] available is visually the clearest monetary value; total and held are secondary (size and weight)"
        self.login("ada")
        self.assertTrue(self.exists("wallet-held"))
        g = lambda t: self.L(t).first.evaluate("e => { const s = getComputedStyle(e); return [parseFloat(s.fontSize), parseInt(s.fontWeight) || 400] }")
        a, b, h = g("wallet-available"), g("wallet-balance"), g("wallet-held")
        self.assertGreater(a[0], b[0])
        self.assertGreater(a[0], h[0])
        self.assertGreaterEqual(a[1], b[1])
        # first in reading order among the three
        order = self.page.evaluate("""() => ['wallet-available','wallet-balance','wallet-held'].map(t => { const e = document.querySelector('[data-testid="'+t+'"]');
            const r = e.getBoundingClientRect(); return r.top * 10000 + r.left; })""")
        self.assertLess(order[0], order[1])
        self.assertLess(order[0], order[2])

    def test_loading_state_while_reads_are_slow(self):
        "[UI-135] while the first reads are slow the page shows a loading indication (not an empty/zero wallet); content appears after"
        held_routes = []

        def hold(route):
            if route.request.method == "GET" and "text/html" not in (route.request.headers.get("accept") or ""):
                held_routes.append(route)  # the read stays pending until we release it
            else:
                route.continue_()

        self.login("ada")
        self.page.route(re.compile(r".*/(me|activity)(\?.*)?$"), hold)
        self.page.reload()
        self.page.wait_for_timeout(500)
        wallet_amount = self.L("wallet-balance").first.get_attribute("data-amount") if self.exists("wallet-balance") else None
        self.assertNotEqual(wallet_amount, "0", "the wallet shows a zero balance while loading")
        loading = self.page.evaluate(r"""() => /load|\u2026|\.\.\./i.test(document.body.innerText) || !!document.querySelector('[aria-busy="true"],[role="progressbar"],[data-loading],.loading,.skeleton,[class*="skeleton"],[class*="spinner"]')""")
        shows_content = self.exists("wallet-balance")
        for r in held_routes:
            r.continue_()
        self.page.unroute(re.compile(r".*/(me|activity)(\?.*)?$"))
        self.assertTrue(loading or not shows_content, "no loading indication and no content during a slow read")
        self.assertTrue(held_routes, "the page made no browser-side reads (server-rendered): loading state not observable")
        expect(self.L("wallet-balance")).to_have_text("100.00 EUR")

    def test_api_failure_shows_an_error_state(self):
        "[UI-136] if reads fail the page shows an understandable error/empty state rather than a blank screen or fake zeros"
        self.login("ada")
        self.page.route(re.compile(r".*/(me|activity)(\?.*)?$"),
                        lambda r: r.fulfill(status=503, content_type="application/json", body='{"error":{"code":"unavailable","message":"down"}}')
                        if r.request.method == "GET" and "text/html" not in (r.request.headers.get("accept") or "") else r.continue_())
        self.page.reload()
        self.page.wait_for_timeout(900)
        text = self.page.evaluate("() => document.body.innerText").strip()
        self.assertTrue(len(text) > 10, "blank page on failed reads")
        self.assertNotEqual(self.L("wallet-available").first.get_attribute("data-amount") if self.exists("wallet-available") else None, "0",
                            "failed read rendered as a zero balance")
