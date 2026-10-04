"""Stage 2 UI checks (real browser): screens, testids, formatting, decimal parsing, pay/request/split/authorization flows,
competing clients, uncertain outcomes, upgrade by import. IDs in [brackets] map to coverage.md."""
import json
import re
import time
import unittest
import urllib.parse

from ui_lib import *


def mine_posts(reqs, suffix):
    return [r for r in reqs if r.method == "POST" and r.url.split("?")[0].endswith(suffix)]


class Routes(Ui):
    def test_signed_out_screens(self):
        "[UI-01] /login and /signup are reachable by URL with their testids; no auth-error until something fails"
        self.page.goto("/login")
        for t in ("login-email", "login-password", "login-submit"):
            expect(self.L(t)).to_be_visible()
        self.assertFalse(self.exists("auth-error"))
        self.assertFalse(self.exists("current-user"))
        self.page.goto("/signup")
        for t in ("signup-email", "signup-password", "signup-display-name", "signup-submit"):
            expect(self.L(t)).to_be_visible()
        self.assertFalse(self.exists("auth-error"))

    def test_signed_in_screens_have_their_testids(self):
        "[UI-02] each required route is reachable by URL when signed in and exposes its testids; current-user/handle/logout on every screen"
        self.login("ada")
        want = {
            "/": ["wallet-balance", "wallet-available", "wallet-refresh", "pay-handle", "pay-amount", "pay-note", "pay-visibility", "pay-submit",
                  "request-handle", "request-amount", "request-note", "request-submit", "activity-list"],
            "/requests": ["incoming-list", "outgoing-list"],
            "/split": ["split-amount", "split-handles", "split-note", "split-submit", "split-preview"],
            "/authorizations": [],  # authorization-list OR empty-authorizations (no authorizations exist in this fixture)
        }
        for path, ids in want.items():
            self.goto(path)
            for t in ids:
                expect(self.L(t).first).to_be_attached()
            if path == "/authorizations":
                expect(self.page.locator(sel("authorization-list") + "," + sel("empty-authorizations")).first).to_be_attached()
            self.assertEqual(self.text("current-handle"), "ada", path)
            self.assertIn("Ada", self.text("current-user"), path)
            expect(self.L("logout-button")).to_be_visible()
            self.assertEqual(urllib.parse.urlparse(self.page.url).path.rstrip("/"), path.rstrip("/"), "route redirected away: " + path)

    def test_navigation_between_screens(self):
        "[UI-03] every screen is reachable through the UI (links), and navigation is the same on every route"
        self.login("ada")
        navs = []
        for path in ("/", "/requests", "/split", "/authorizations"):
            self.goto(path)
            links = self.page.eval_on_selector_all("a[href]", "els => els.map(e => new URL(e.href).pathname)")
            navs.append(sorted(set(links)))
            for target in ("/", "/requests", "/split", "/authorizations"):
                self.assertIn(target, links, "no link to %s from %s" % (target, path))
        self.assertTrue(all(n == navs[0] for n in navs), navs)
        self.goto("/")
        self.page.locator('a[href="/requests"]').first.click()
        expect(self.L("incoming-list")).to_be_attached()
        self.page.locator('a[href="/split"]').first.click()
        expect(self.L("split-amount")).to_be_visible()

    def test_signed_out_user_is_not_shown_wallet(self):
        "[UI-04] a signed-out visitor does not see another user's data (no current-user, no wallet)"
        self.page.goto("/")
        self.page.wait_for_timeout(600)
        self.assertFalse(self.exists("current-user"))
        self.assertEqual(self.L("wallet-balance").count(), 0)


class AuthFlow(Ui):
    def test_signup_flow(self):
        "[UI-05] signup: current-user contains the display name, current-handle is exactly the derived handle (no @), on every screen"
        self.page.goto("/signup")
        self.L("signup-display-name").fill("Grace Hopper")
        self.L("signup-email").fill("Grace.Hopper@example.com")
        self.L("signup-password").fill("a long password")
        self.L("signup-submit").click()
        expect(self.L("current-user")).to_be_visible()
        self.assertIn("Grace Hopper", self.text("current-user"))
        self.assertEqual(self.text("current-handle"), "grace_hopper")
        self.assertFalse(self.exists("auth-error"))
        for path in ("/", "/requests", "/split", "/authorizations"):
            self.goto(path)
            self.assertIn("Grace Hopper", self.text("current-user"))
            self.assertEqual(self.text("current-handle"), "grace_hopper")
        self.goto("/")
        expect(self.L("wallet-balance")).to_have_text("0.00 EUR")
        self.assertEqual(me("ada")["balance"], 10000)
        self.assertEqual(call("POST", "/auth/login", {"email": "Grace.Hopper@example.com", "password": "a long password"}).status, 200)

    def test_login_logout(self):
        "[UI-06] login shows the seeded user's handle; logout returns to a signed-out state; login again works"
        self.login("bob")
        self.assertEqual(self.text("current-handle"), "bob")
        self.assertIn("Bob", self.text("current-user"))
        self.L("logout-button").click()
        expect(self.L("current-user")).to_have_count(0)
        self.page.goto("/")
        self.page.wait_for_timeout(400)
        self.assertFalse(self.exists("current-user"))
        self.login("bob")
        self.assertEqual(self.text("current-handle"), "bob")

    def test_auth_errors(self):
        "[UI-07] bad login / duplicate signup / short password show auth-error; it disappears on success"
        self.page.goto("/login")
        self.L("login-email").fill("ada@example.com")
        self.L("login-password").fill("wrong password")
        self.L("login-submit").click()
        expect(self.L("auth-error")).to_be_visible()
        self.assertTrue(self.text("auth-error"))
        self.assertFalse(self.exists("current-user"))
        self.L("login-password").fill(PW)
        self.L("login-submit").click()
        expect(self.L("current-user")).to_be_visible()
        self.assertFalse(self.exists("auth-error"))
        self.L("logout-button").click()
        for email, pw, name in (("ada@example.com", "long password", "Dup"), ("fresh.person@example.com", "short", "Short"),
                                ("not-an-email", "long password", "Bad")):
            self.page.goto("/signup")
            self.L("signup-display-name").fill(name)
            self.L("signup-email").fill(email)
            self.L("signup-password").fill(pw)
            self.L("signup-submit").click()
            expect(self.L("auth-error")).to_be_visible()
            self.assertFalse(self.exists("current-user"), email)
        self.assertEqual(call("POST", "/auth/login", {"email": "fresh.person@example.com", "password": "short"}).status, 401)


class Formatting(Ui):
    def fixture(self):
        return base_fixture()

    def test_eur_balance_and_available(self):
        "[UI-10] wallet-balance is exactly '100.00 EUR' with data-amount in minor units; available agrees; wallet-held absent at zero"
        self.login("ada")
        expect(self.L("wallet-balance")).to_have_text("100.00 EUR")
        self.assertEqual(self.L("wallet-balance").get_attribute("data-amount"), "10000")
        expect(self.L("wallet-available")).to_have_text("100.00 EUR")
        self.assertEqual(self.L("wallet-available").get_attribute("data-amount"), "10000")
        self.assertEqual(self.L("wallet-held").count(), 0)

    def test_amount_formats(self):
        "[UI-11] exactly minor_units decimals, a single space, the currency code; no sign, no grouping; JPY has no decimal point"
        cases = [("EUR", 2, 10000, "100.00 EUR"), ("EUR", 2, 5, "0.05 EUR"), ("EUR", 2, 0, "0.00 EUR"), ("EUR", 2, 123456789, "1234567.89 EUR"),
                 ("EUR", 2, 100, "1.00 EUR"), ("JPY", 0, 1200, "1200 JPY"), ("JPY", 0, 0, "0 JPY"), ("JPY", 0, 7, "7 JPY"),
                 ("BHD", 3, 1500, "1.500 BHD"), ("BHD", 3, 5, "0.005 BHD"), ("BHD", 3, 1000000, "1000.000 BHD")]
        for cur, mu, balance, want in cases:
            reset(base_fixture(currency=cur, minor_units=mu, users=[user("ada", balance), user("bob", 0)], payments=[], requests=[]))
            self.page.context.clear_cookies()
            self.login("ada")
            expect(self.L("wallet-balance")).to_have_text(want)
            self.assertEqual(self.L("wallet-balance").get_attribute("data-amount"), str(balance))
            expect(self.L("wallet-available")).to_have_text(want)
            self.assertEqual(self.text("wallet-balance"), want)
            self.L("logout-button").click()

    def test_feed_amounts_formatted(self):
        "[UI-12] activity-amount is exactly the formatted amount in every currency"
        for cur, mu, amount, want in (("JPY", 0, 15, "15 JPY"), ("BHD", 3, 1234, "1.234 BHD"), ("EUR", 2, 1550, "15.50 EUR")):
            reset(base_fixture(currency=cur, minor_units=mu, users=[user("ada", 100000), user("bob", 0)], payments=[], requests=[]))
            pid = pay("ada", "bob", amount).body["payment_id"]
            self.login("ada")
            expect(self.L("activity-amount-" + pid)).to_have_text(want)
            self.L("logout-button").click()

    def test_held_and_available_presentation(self):
        "[UI-13] with an open hold: balance=total, available=total-held (headline), held shown; available is the visually dominant number"
        reset(azfixture([seed_auth("a_1", "ada", "bob", 2000)]))
        self.login("ada")
        expect(self.L("wallet-balance")).to_have_text("100.00 EUR")
        expect(self.L("wallet-available")).to_have_text("80.00 EUR")
        self.assertEqual(self.L("wallet-available").get_attribute("data-amount"), "8000")
        expect(self.L("wallet-held")).to_have_text("20.00 EUR")
        self.assertEqual(self.L("wallet-held").get_attribute("data-amount"), "2000")
        self.assertEqual(self.L("wallet-balance").get_attribute("data-amount"), "10000")
        fs = lambda t: float(self.L(t).evaluate("e => parseFloat(getComputedStyle(e).fontSize)"))
        self.assertGreaterEqual(fs("wallet-available"), 1.25 * fs("wallet-balance"), "available must be the headline number")
        self.assertGreaterEqual(fs("wallet-available"), 1.25 * fs("wallet-held"))
        # visible text 'available' wording near the number so people know what it is
        near = self.L("wallet-available").evaluate("e => (e.parentElement.innerText || '').toLowerCase()")
        self.assertRegex(near, r"available|spend|can spend")
        # held disappears when released
        void("ada", "a_1")
        self.L("wallet-refresh").click()
        expect(self.L("wallet-available")).to_have_text("100.00 EUR")
        expect(self.L("wallet-held")).to_have_count(0)

    def test_expired_seed_holds_nothing_in_ui(self):
        "[UI-14] seeded holds already expired are not held; seeded future holds are"
        reset(azfixture([seed_auth("a_1", "ada", "bob", 9000, expires_in=-7200), seed_auth("a_2", "ada", "bob", 1000, status="voided"),
                         seed_auth("a_3", "ada", "bob", 500)]))
        self.login("ada")
        expect(self.L("wallet-available")).to_have_text("95.00 EUR")
        expect(self.L("wallet-held")).to_have_text("5.00 EUR")


class DecimalInput(Ui):
    def sent_amounts(self):
        return [json.loads(r.post_data)["amount"] for r in mine_posts(self.reqs, "/payments")]

    def test_valid_decimals_become_minor_units(self):
        "[UI-20] '15' and '15.00' -> 1500, '15.5' -> 1550; float traps 0.29, 1.15, 4.35, 8.2, 0.07 are exact"
        self.login("ada")
        cases = [("15", 1500), ("15.00", 1500), ("15.5", 1550), ("0.29", 29), ("1.15", 115), ("4.35", 435), ("8.2", 820), ("0.07", 7),
                 ("40.40", 4040)]
        spent = 0
        for i, (txt, minor) in enumerate(cases):
            self.reqs.clear()
            self.fill_pay("bob", txt, "case %d" % i)
            self.L("pay-submit").click()
            spent += minor
            expect(self.L("wallet-balance")).to_have_attribute("data-amount", str(10000 - spent))
            self.assertEqual(self.sent_amounts(), [minor], txt)
            self.assertFalse(self.exists("pay-error"), txt)
        self.assertEqual(bal("bob"), 2500 + spent)

    def test_invalid_decimals_never_leave_the_browser(self):
        "[UI-21] '15.005', letters, empty, exponent, negative, commas, currency symbols, double dots: pay-error, no request sent, balance unchanged"
        self.login("ada")
        for bad in ("15.005", "15.001", "abc", "", "1e2", "-5", "1,50", "15.5.5", "$15", "15 EUR", "0x10"):
            self.reqs.clear()
            self.fill_pay("bob", bad, "bad")
            self.L("pay-submit").click()
            expect(self.L("pay-error")).to_be_visible()
            self.assertTrue(self.text("pay-error"))
            self.page.wait_for_timeout(150)
            self.assertEqual(self.api_posts("/payments"), [], "request was sent for %r" % bad)
            self.assertEqual(bal("ada"), 10000)
        # fixing the amount works and clears the error
        self.L("pay-amount").fill("1.50")
        self.L("pay-submit").click()
        expect(self.L("pay-error")).to_have_count(0)
        expect(self.L("wallet-balance")).to_have_text("98.50 EUR")

    def test_zero_amount_is_refused(self):
        "[UI-22] amount 0 / 0.00 is refused with pay-error and moves nothing (client or server side)"
        self.login("ada")
        for z in ("0", "0.00"):
            self.fill_pay("bob", z)
            self.L("pay-submit").click()
            expect(self.L("pay-error")).to_be_visible()
            self.assertEqual(bal("ada"), 10000)

    def test_jpy_and_bhd_precision(self):
        "[UI-23] decimal places allowed = minor_units: JPY none, BHD three"
        reset(base_fixture(currency="JPY", minor_units=0, users=[user("ada", 5000), user("bob", 0)], payments=[], requests=[]))
        self.login("ada")
        self.fill_pay("bob", "15.5")
        self.reqs.clear()
        self.L("pay-submit").click()
        expect(self.L("pay-error")).to_be_visible()
        self.assertEqual(self.api_posts("/payments"), [])
        self.fill_pay("bob", "15")
        self.L("pay-submit").click()
        expect(self.L("wallet-balance")).to_have_text("4985 JPY")
        self.assertEqual(self.sent_amounts(), [15])
        reset(base_fixture(currency="BHD", minor_units=3, users=[user("ada", 50000), user("bob", 0)], payments=[], requests=[]))
        self.page.context.clear_cookies()
        self.login("ada")
        self.reqs.clear()
        self.fill_pay("bob", "1.2345")
        self.L("pay-submit").click()
        expect(self.L("pay-error")).to_be_visible()
        self.assertEqual(self.api_posts("/payments"), [])
        for txt, minor in (("1.234", 1234), ("1.5", 1500), ("2", 2000)):
            self.reqs.clear()
            self.fill_pay("bob", txt, "n" + txt)
            self.L("pay-submit").click()
            expect(self.L("pay-error")).to_have_count(0)
            self.page.wait_for_timeout(200)
            self.assertEqual(self.sent_amounts(), [minor], txt)

    def test_request_and_authorize_and_split_amounts_use_the_same_rule(self):
        "[UI-24] request-amount, split-amount and authorize-amount take decimals; '15.005' is refused locally on each"
        self.login("ada")
        self.reqs.clear()
        self.L("request-handle").fill("bob")
        self.L("request-amount").fill("15.005")
        self.L("request-submit").click()
        expect(self.L("request-error")).to_be_visible()
        self.assertEqual(self.api_posts("/requests"), [])
        self.L("request-amount").fill("15.5")
        self.L("request-submit").click()
        self.page.wait_for_timeout(500)
        self.assertEqual(json.loads(self.api_posts("/requests")[-1].post_data)["amount"], 1550)
        self.goto("/split")
        self.reqs.clear()
        self.L("split-amount").fill("10.005")
        self.L("split-handles").fill("bob, cy")
        self.L("split-submit").click()
        expect(self.L("split-error")).to_be_visible()
        self.assertEqual(self.api_posts("/splits"), [])
        page = authorize_page(self)
        self.reqs.clear()
        self.fill_pay("bob", "15.005", prefix="authorize")
        self.L("authorize-submit").click()
        expect(self.L("authorize-error")).to_be_visible()
        self.assertEqual(self.api_posts("/authorizations"), [])


def authorize_page(t):
    """The spec does not say which screen hosts the authorise form: look on / first, then /authorizations."""
    t.goto("/")
    if t.exists("authorize-handle"):
        return "/"
    t.goto("/authorizations")
    if t.exists("authorize-handle"):
        return "/authorizations"
    raise AssertionError("no authorize form (authorize-handle) on / or /authorizations")


class Pay(Ui):
    def test_success_keeps_form_and_resubmit_sends_nothing_new(self):
        "[UI-30] form values survive success; submitting again unchanged -> balance falls once, feed has one payment, pay-error absent"
        self.login("ada")
        self.fill_pay("bob", "15.00", "lunch", "private")
        self.L("pay-submit").click()
        expect(self.L("wallet-balance")).to_have_text("85.00 EUR")
        self.assertEqual(self.L("pay-handle").input_value(), "bob")
        self.assertEqual(self.L("pay-amount").input_value(), "15.00")
        self.assertEqual(self.L("pay-note").input_value(), "lunch")
        self.assertEqual(self.L("pay-visibility").input_value(), "private")
        self.L("pay-submit").click()
        self.L("pay-submit").click()
        self.page.wait_for_timeout(700)
        self.assertEqual(bal("ada"), 8500)
        expect(self.L("wallet-balance")).to_have_text("85.00 EUR")
        self.assertFalse(self.exists("pay-error"))
        self.assertEqual(len([p for p in activity("ada", limit=200)["payments"] if p["note"] == "lunch"]), 1)
        self.assertEqual(self.page.locator('[data-testid^="activity-item-"][data-visibility="private"]').count(), 2)  # seeded p_2 + the new one
        keys = {r.headers.get("idempotency-key") for r in mine_posts(self.reqs, "/payments")}
        self.assertLessEqual(len(keys), 1)  # a resend, if any, is a replay of the same key
        self.assertEqual(len([p for p in activity("bob", limit=200)["payments"] if p["note"] == "lunch"]), 1)

    def test_changed_field_is_a_new_payment(self):
        "[UI-31] changing any field makes the next submission a new payment (new key); each field individually"
        self.login("ada")
        self.fill_pay("bob", "10.00", "a", "public")
        self.L("pay-submit").click()
        expect(self.L("wallet-balance")).to_have_text("90.00 EUR")
        self.L("pay-note").fill("b")
        self.L("pay-submit").click()
        expect(self.L("wallet-balance")).to_have_text("80.00 EUR")
        self.L("pay-amount").fill("5.00")
        self.L("pay-submit").click()
        expect(self.L("wallet-balance")).to_have_text("75.00 EUR")
        self.L("pay-visibility").select_option("private")
        self.L("pay-submit").click()
        expect(self.L("wallet-balance")).to_have_text("70.00 EUR")
        self.L("pay-handle").fill("cy")
        self.L("pay-submit").click()
        expect(self.L("wallet-balance")).to_have_text("65.00 EUR")
        keys = [r.headers.get("idempotency-key") for r in mine_posts(self.reqs, "/payments")]
        self.assertEqual(len(keys), 5)
        self.assertEqual(len(set(keys)), 5)
        self.assertEqual(bal("ada"), 6500)

    def test_refused_payments_keep_inputs_and_show_pay_error(self):
        "[UI-32] insufficient funds / unknown handle / own handle: pay-error visible, every input preserved, balance unchanged"
        self.login("ada")
        for handle, amount in (("bob", "100.01"), ("nobody", "1.00"), ("ada", "1.00")):
            self.fill_pay(handle, amount, "keep me", "private")
            self.L("pay-submit").click()
            expect(self.L("pay-error")).to_be_visible()
            self.assertTrue(self.text("pay-error"))
            self.assertEqual((self.L("pay-handle").input_value(), self.L("pay-amount").input_value(), self.L("pay-note").input_value(),
                              self.L("pay-visibility").input_value()), (handle, amount, "keep me", "private"))
            expect(self.L("wallet-balance")).to_have_text("100.00 EUR")
            self.assertFalse(self.exists("pay-uncertain"))
        self.fill_pay("bob", "1.00", "ok")
        self.L("pay-submit").click()
        expect(self.L("pay-error")).to_have_count(0)
        expect(self.L("wallet-balance")).to_have_text("99.00 EUR")

    def test_available_funds_gate_payments_in_ui(self):
        "[UI-33] held funds cannot be spent: paying more than available shows pay-error; exactly available succeeds"
        reset(azfixture([seed_auth("a_1", "ada", "bob", 2000)]))
        self.login("ada")
        self.fill_pay("cy", "80.01")
        self.L("pay-submit").click()
        expect(self.L("pay-error")).to_be_visible()
        expect(self.L("wallet-available")).to_have_text("80.00 EUR")
        self.fill_pay("cy", "80.00")
        self.L("pay-submit").click()
        expect(self.L("wallet-available")).to_have_text("0.00 EUR")
        expect(self.L("wallet-held")).to_have_text("20.00 EUR")
        expect(self.L("wallet-balance")).to_have_text("20.00 EUR")

    def test_other_client_spent_the_balance(self):
        "[UI-34] another client drains the wallet after this page read it: refused payment -> pay-error, balance+feed refreshed, inputs kept"
        self.login("ada")
        expect(self.L("wallet-balance")).to_have_text("100.00 EUR")
        drain = pay("ada", "cy", 9500, note="drained elsewhere")
        self.assertEqual(drain.status, 201)
        self.fill_pay("bob", "50.00", "my note", "private")
        self.L("pay-submit").click()
        expect(self.L("pay-error")).to_be_visible()
        expect(self.L("wallet-balance")).to_have_text("5.00 EUR")
        self.assertEqual(self.L("wallet-balance").get_attribute("data-amount"), "500")
        expect(self.L("activity-note-" + drain.body["payment_id"])).to_have_text("drained elsewhere")
        self.assertEqual((self.L("pay-handle").input_value(), self.L("pay-amount").input_value(), self.L("pay-note").input_value(),
                          self.L("pay-visibility").input_value()), ("bob", "50.00", "my note", "private"))

    def test_pending_state_blocks_double_submission(self):
        "[UI-35] while a payment is in flight the submit is visibly busy and a second click sends nothing; exactly one payment results"
        def slow(route):
            if route.request.method != "POST":
                return route.continue_()
            time.sleep(1.2)
            route.continue_()

        self.login("ada")
        self.route_json_post("/payments", slow)
        self.fill_pay("bob", "3.00", "slow")
        self.L("pay-submit").click()
        self.page.wait_for_timeout(250)
        busy = self.L("pay-submit").evaluate("e => e.disabled || e.getAttribute('aria-busy') === 'true'") or \
            self.page.locator('[role="status"], [role="progressbar"], [aria-busy="true"]').count() > 0
        self.assertTrue(busy, "no visible pending state while the request is in flight")
        self.L("pay-submit").click(force=True, no_wait_after=True)
        expect(self.L("wallet-balance")).to_have_text("97.00 EUR")
        self.page.wait_for_timeout(500)
        self.assertEqual(bal("ada"), 9700)
        keys = {r.headers.get("idempotency-key") for r in mine_posts(self.reqs, "/payments")}
        self.assertEqual(len(keys), 1)

    def test_refused_and_uncertain_look_different(self):
        "[UI-36] refused (pay-error) and uncertain (pay-uncertain) are visually distinct; so are available and held"
        self.login("ada")
        self.fill_pay("bob", "500.00")
        self.L("pay-submit").click()
        expect(self.L("pay-error")).to_be_visible()
        style = lambda t: self.L(t).evaluate("e => { const s = getComputedStyle(e); return [s.color, s.backgroundColor, s.borderTopColor, s.borderTopStyle].join('|') }")
        refused = style("pay-error")
        self.fill_pay("bob", "5.00", "unc")

        def lose(route):
            if route.request.method != "POST":
                return route.continue_()
            route.abort()

        self.route_json_post("/payments", lose)
        self.L("pay-submit").click()
        expect(self.L("pay-uncertain")).to_be_visible()
        self.assertNotEqual(refused, style("pay-uncertain"), "refused and uncertain states must not look identical")


class Uncertain(Ui):
    def lose_after_commit(self, seen):
        def h(route):
            if route.request.method != "POST":
                return route.continue_()
            seen.append((route.request.headers.get("idempotency-key"), route.request.post_data))
            route.fetch()  # the server commits ...
            route.abort("connectionreset")  # ... but the response never reaches the browser

        return h

    def test_lost_response_after_commit(self):
        "[UI-40] response lost after POST /payments commits: pay-uncertain (not pay-error), form retained, retry = same key + same body, money moves once"
        seen = []
        self.login("ada")
        self.route_json_post("/payments", self.lose_after_commit(seen))
        self.fill_pay("bob", "12.34", "uncertain", "private")
        self.L("pay-submit").click()
        expect(self.L("pay-uncertain")).to_be_visible()
        self.assertTrue(self.text("pay-uncertain"))
        self.assertFalse(self.exists("pay-error"))
        self.assertEqual((self.L("pay-handle").input_value(), self.L("pay-amount").input_value(), self.L("pay-note").input_value(),
                          self.L("pay-visibility").input_value()), ("bob", "12.34", "uncertain", "private"))
        self.assertEqual(bal("ada"), 10000 - 1234)  # it did commit
        self.page.unroute(re.compile(r".*/payments(\?.*)?$"))
        self.L("pay-submit").click()
        expect(self.L("pay-uncertain")).to_have_count(0)
        expect(self.L("pay-error")).to_have_count(0)
        expect(self.L("wallet-balance")).to_have_text("87.66 EUR")
        self.assertEqual(bal("ada"), 10000 - 1234)
        retry = mine_posts(self.reqs, "/payments")[-1]
        self.assertEqual(retry.headers.get("idempotency-key"), seen[0][0])
        self.assertEqual(json.loads(retry.post_data), json.loads(seen[0][1]))
        self.assertEqual(len([p for p in activity("ada", limit=200)["payments"] if p["note"] == "uncertain"]), 1)
        self.assertEqual(self.page.locator('[data-testid^="activity-item-"]').count(), 3)  # p_1, p_2 + the new payment, once

    def test_lost_response_before_commit(self):
        "[UI-41] request lost before reaching the server: pay-uncertain; retry with the same key creates the payment exactly once"
        seen = []

        def drop(route):
            if route.request.method != "POST":
                return route.continue_()
            seen.append(route.request.headers.get("idempotency-key"))
            route.abort("connectionreset")

        self.login("ada")
        self.route_json_post("/payments", drop)
        self.fill_pay("bob", "7.00", "never arrived")
        self.L("pay-submit").click()
        expect(self.L("pay-uncertain")).to_be_visible()
        self.assertEqual(bal("ada"), 10000)
        self.page.unroute(re.compile(r".*/payments(\?.*)?$"))
        self.L("pay-submit").click()
        expect(self.L("wallet-balance")).to_have_text("93.00 EUR")
        expect(self.L("pay-uncertain")).to_have_count(0)
        self.assertEqual(mine_posts(self.reqs, "/payments")[-1].headers.get("idempotency-key"), seen[0])
        self.assertEqual(bal("ada"), 9300)

    def test_repeated_losses_keep_one_identity(self):
        "[UI-42] three lost responses in a row, then success: every attempt carries the same key and body; money moves once"
        seen = []
        self.login("ada")
        self.route_json_post("/payments", self.lose_after_commit(seen))
        self.fill_pay("bob", "2.50", "persist")
        for _ in range(3):
            self.L("pay-submit").click()
            expect(self.L("pay-uncertain")).to_be_visible()
            self.page.wait_for_timeout(150)
        self.page.unroute(re.compile(r".*/payments(\?.*)?$"))
        self.L("pay-submit").click()
        expect(self.L("pay-uncertain")).to_have_count(0)
        expect(self.L("wallet-balance")).to_have_text("97.50 EUR")
        keys = [r.headers.get("idempotency-key") for r in mine_posts(self.reqs, "/payments")]
        self.assertEqual(len(keys), 4)
        self.assertEqual(len(set(keys)), 1)
        self.assertEqual(len({r.post_data and json.dumps(json.loads(r.post_data), sort_keys=True) for r in mine_posts(self.reqs, "/payments")}), 1)
        self.assertEqual(bal("ada"), 9750)

    def test_changing_the_form_while_uncertain_is_a_new_payment(self):
        "[UI-43] after an uncertain outcome, editing a field starts a new payment (new key); the old identity is not reused"
        seen = []
        self.login("ada")
        self.route_json_post("/payments", self.lose_after_commit(seen))
        self.fill_pay("bob", "1.00", "first")
        self.L("pay-submit").click()
        expect(self.L("pay-uncertain")).to_be_visible()
        self.page.unroute(re.compile(r".*/payments(\?.*)?$"))
        self.L("pay-note").fill("second")
        self.L("pay-submit").click()
        expect(self.L("pay-uncertain")).to_have_count(0)
        last = mine_posts(self.reqs, "/payments")[-1]
        self.assertNotEqual(last.headers.get("idempotency-key"), seen[0][0])
        self.assertEqual(json.loads(last.post_data)["note"], "second")

    def test_uncertain_then_server_refuses_on_retry(self):
        "[UI-44] if the retry is answered with a real refusal, uncertainty is replaced by pay-error (a confirmed outcome)"
        seen = []
        self.login("ada")
        self.fill_pay("bob", "60.00", "big")

        def lose_before(route):
            if route.request.method != "POST":
                return route.continue_()
            route.abort("connectionreset")

        self.route_json_post("/payments", lose_before)
        self.L("pay-submit").click()
        expect(self.L("pay-uncertain")).to_be_visible()
        self.page.unroute(re.compile(r".*/payments(\?.*)?$"))
        pay("ada", "cy", 9000)  # someone else spends the money meanwhile
        self.L("pay-submit").click()
        expect(self.L("pay-error")).to_be_visible()
        expect(self.L("pay-uncertain")).to_have_count(0)
        expect(self.L("wallet-balance")).to_have_text("10.00 EUR")


class Refresh(Ui):
    def test_refresh_keeps_form_and_sees_other_clients(self):
        "[UI-50] wallet-refresh updates balance and feed from other clients without clearing the filled pay form"
        self.login("ada")
        self.fill_pay("bob", "3.21", "typing", "private")
        p = pay("bob", "ada", 700, note="from bob")
        self.L("wallet-refresh").click()
        expect(self.L("wallet-balance")).to_have_text("107.00 EUR")
        expect(self.L("activity-note-" + p.body["payment_id"])).to_have_text("from bob")
        self.assertEqual((self.L("pay-handle").input_value(), self.L("pay-amount").input_value(), self.L("pay-note").input_value(),
                          self.L("pay-visibility").input_value()), ("bob", "3.21", "typing", "private"))
        self.assertFalse(self.exists("pay-error"))

    def test_refresh_updates_available_and_held(self):
        "[UI-51] the same refresh rules apply to available and held"
        self.login("ada")
        a = authorize("ada", "bob", 2500)
        self.L("wallet-refresh").click()
        expect(self.L("wallet-held")).to_have_text("25.00 EUR")
        expect(self.L("wallet-available")).to_have_text("75.00 EUR")
        expect(self.L("wallet-balance")).to_have_text("100.00 EUR")
        void("ada", a.body["authorization_id"])
        self.L("wallet-refresh").click()
        expect(self.L("wallet-held")).to_have_count(0)
        expect(self.L("wallet-available")).to_have_text("100.00 EUR")

    def test_latest_refresh_wins_out_of_order(self):
        "[UI-52] an older read that returns after a newer one never overwrites it (balance, available, held, feed)"
        self.login("ada")
        held = []

        stale_state = {"n_me": 0, "n_act": 0}
        stash = []

        def on_me(route):
            if route.request.method != "GET" or "text/html" in (route.request.headers.get("accept") or ""):
                return route.continue_()
            stale_state["n_me"] += 1
            if stale_state["n_me"] == 1:
                stash.append((route, route.fetch()))  # response computed now (stale), delivered later
            else:
                route.continue_()

        def on_act(route):
            if route.request.method != "GET" or "text/html" in (route.request.headers.get("accept") or ""):
                return route.continue_()
            stale_state["n_act"] += 1
            if stale_state["n_act"] == 1:
                stash.append((route, route.fetch()))
            else:
                route.continue_()

        self.page.route(re.compile(r".*/me(\?.*)?$"), on_me)
        self.page.route(re.compile(r".*/activity(\?.*)?$"), on_act)
        self.reqs.clear()
        self.L("wallet-refresh").click()  # R1: held back
        self.page.wait_for_timeout(500)
        if not stash:
            self.skipTest("UI does not read /me or /activity from the browser on refresh (server-rendered); latest-wins is trivially satisfied")
        newp = pay("bob", "ada", 1000, note="newer")
        authorize("ada", "cy", 2000)
        self.L("wallet-refresh").click()  # R2: passes through
        expect(self.L("wallet-balance")).to_have_text("110.00 EUR")
        expect(self.L("wallet-available")).to_have_text("90.00 EUR")
        expect(self.L("wallet-held")).to_have_text("20.00 EUR")
        expect(self.L("activity-note-" + newp.body["payment_id"])).to_have_text("newer")
        for route, resp in stash:  # now the OLD responses arrive
            route.fulfill(response=resp)
        self.page.wait_for_timeout(900)
        expect(self.L("wallet-balance")).to_have_text("110.00 EUR")
        expect(self.L("wallet-available")).to_have_text("90.00 EUR")
        expect(self.L("wallet-held")).to_have_text("20.00 EUR")
        self.assertTrue(self.exists("activity-note-" + newp.body["payment_id"]), "stale feed overwrote the newer feed")
        self.assertEqual(self.bal_amount(), 11000)

    def test_in_order_refreshes_still_apply(self):
        "[UI-53] a slow first refresh followed by nothing newer is still applied (latest-wins must not drop the only response)"
        self.login("ada")

        def slow(route):
            if route.request.method != "GET" or "text/html" in (route.request.headers.get("accept") or ""):
                return route.continue_()
            time.sleep(0.8)
            route.continue_()

        pay("bob", "ada", 500)
        self.page.route(re.compile(r".*/me(\?.*)?$"), slow)
        self.L("wallet-refresh").click()
        expect(self.L("wallet-balance")).to_have_text("105.00 EUR")


class Feed(Ui):
    def test_feed_item_parts(self):
        "[UI-60] item carries data-visibility; parties contain both handles; amount and note exact; empty note element present"
        self.login("ada")
        a = pay("ada", "bob", 1550, note="Lunch ☕ <b>x</b>  spaced", visibility="private").body["payment_id"]
        b = pay("ada", "cy", 1, note="", visibility="public").body["payment_id"]
        self.L("wallet-refresh").click()
        expect(self.L("activity-item-" + a)).to_have_attribute("data-visibility", "private")
        self.assertEqual(self.L("activity-item-" + b).get_attribute("data-visibility"), "public")
        parties = self.text("activity-parties-" + a)
        self.assertIn("ada", parties)
        self.assertIn("bob", parties)
        self.assertEqual(self.text("activity-amount-" + a), "15.50 EUR")
        self.assertEqual(self.L("activity-note-" + a).text_content().strip(), "Lunch ☕ <b>x</b>  spaced")
        self.assertEqual(self.L("activity-amount-" + b).inner_text().strip(), "0.01 EUR")
        self.assertEqual(self.L("activity-note-" + b).count(), 1)
        self.assertEqual(self.L("activity-note-" + b).text_content().strip(), "")
        self.assertEqual(self.page.locator('[data-testid="activity-item-%s"] b' % a).count(), 0)  # note is text, never markup
        self.assertEqual(self.L("activity-list").locator('[data-testid^="activity-item-"]').count(), self.page.locator('[data-testid^="activity-item-"]').count())

    def test_newest_first_in_dom(self):
        "[UI-61] activity-list children are newest first"
        ids = []
        for i in range(3):
            ids.append(pay("ada", "bob", 100 + i, note="n%d" % i).body["payment_id"])
            time.sleep(1.1)
        self.login("ada")
        order = self.page.eval_on_selector_all('[data-testid="activity-list"] [data-testid^="activity-item-"]',
                                               "els => els.map(e => e.getAttribute('data-testid').replace('activity-item-',''))")
        self.assertEqual(order[:3], ids[::-1])

    def test_privacy_in_the_browser(self):
        "[UI-62] a private payment between two users is not rendered for a third party; public is; both parties see private"
        priv = pay("ada", "bob", 100, note="secret", visibility="private").body["payment_id"]
        pub = pay("ada", "bob", 101, note="open", visibility="public").body["payment_id"]
        ctx2 = self.browser.new_context(viewport=self.VIEW, base_url=BASE)
        p2 = ctx2.new_page()
        self.login("eve", page=p2)
        expect(self.L("activity-item-" + pub, p2)).to_be_visible()
        self.assertEqual(self.L("activity-item-" + priv, p2).count(), 0)
        self.assertNotIn("secret", p2.content())
        self.login("bob")
        expect(self.L("activity-item-" + priv)).to_be_visible()
        ctx2.close()

    def test_empty_activity(self):
        "[UI-63] empty-activity is shown instead of the list when nothing is visible; absent otherwise"
        reset(base_fixture(payments=[]))
        self.login("dee")
        expect(self.L("empty-activity")).to_be_visible()
        self.assertEqual(self.L("activity-list").count() and self.L("activity-list").locator('[data-testid^="activity-item-"]').count(), 0)
        pay("ada", "dee", 100, note="x")
        self.L("wallet-refresh").click()
        expect(self.L("empty-activity")).to_have_count(0)

    def test_pay_updates_feed_and_balance_without_reload(self):
        "[UI-64] after a successful action the balance and feed show the new state with no manual reload"
        self.login("ada")
        self.fill_pay("bob", "9.99", "fresh")
        self.L("pay-submit").click()
        expect(self.L("wallet-balance")).to_have_text("90.01 EUR")
        expect(self.page.locator('[data-testid^="activity-note-"]', has_text="fresh")).to_have_count(1)
        pid = [p for p in activity("ada", limit=200)["payments"] if p["note"] == "fresh"][0]["payment_id"]
        self.assertEqual(self.text("activity-amount-" + pid), "9.99 EUR")
        order = self.page.eval_on_selector_all('[data-testid="activity-list"] [data-testid^="activity-item-"]',
                                               "els => els.map(e => e.getAttribute('data-testid'))")
        self.assertEqual(order[0], "activity-item-" + pid)  # newest first

    def test_request_form_on_home(self):
        "[UI-65] request form creates a request visible on /requests (outgoing); refusals show request-error"
        self.login("ada")
        self.L("request-handle").fill("bob")
        self.L("request-amount").fill("12.50")
        self.L("request-note").fill("rent")
        self.L("request-submit").click()
        self.page.wait_for_timeout(600)
        rows = [q for q in requests_of("ada", direction="outgoing")["requests"] if q["note"] == "rent"]
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]["amount"], rows[0]["payer_handle"]), (1250, "bob"))
        self.assertFalse(self.exists("request-error"))
        self.goto("/requests")
        expect(self.L("request-item-" + rows[0]["request_id"])).to_be_visible()
        self.goto("/")
        for handle, amount in (("nobody", "1.00"), ("ada", "1.00")):
            self.L("request-handle").fill(handle)
            self.L("request-amount").fill(amount)
            self.L("request-submit").click()
            expect(self.L("request-error")).to_be_visible()


class Requests(Ui):
    def test_lists_items_and_buttons(self):
        "[UI-70] items sit in incoming-list/outgoing-list with data-status and formatted amount; buttons only where allowed"
        self.login("ada")
        self.goto("/requests")
        inc = self.L("incoming-list")
        expect(inc.locator(sel("request-item-rq_1"))).to_be_visible()
        self.assertEqual(self.L("request-item-rq_1").get_attribute("data-status"), "pending")
        self.assertEqual(self.text("request-amount-rq_1"), "12.00 EUR")
        for t in ("request-pay-rq_1", "request-decline-rq_1"):
            self.assertEqual(self.L(t).count(), 1)
        self.assertEqual(self.L("request-cancel-rq_1").count(), 0)
        for rid, st in (("rq_4", "declined"), ("rq_5", "cancelled")):
            self.assertEqual(self.L("request-item-" + rid).get_attribute("data-status"), st)
            for t in ("pay", "decline", "cancel"):
                self.assertEqual(self.L("request-%s-%s" % (t, rid)).count(), 0, (rid, t))
        self.assertEqual(self.L("outgoing-list").locator(sel("request-item-rq_1")).count(), 0)
        self.assertEqual(self.L("empty-requests").count(), 0)
        # bob is the requester of rq_1
        ctx2 = self.browser.new_context(viewport=self.VIEW, base_url=BASE)
        p2 = ctx2.new_page()
        self.login("bob", page=p2)
        p2.goto("/requests")
        expect(self.L("outgoing-list", p2).locator(sel("request-item-rq_1"))).to_be_visible()
        self.assertEqual(self.L("request-cancel-rq_1", p2).count(), 1)
        self.assertEqual(self.L("request-pay-rq_1", p2).count(), 0)
        self.assertEqual(self.L("request-decline-rq_1", p2).count(), 0)
        ctx2.close()
        # a stranger sees neither
        ctx3 = self.browser.new_context(viewport=self.VIEW, base_url=BASE)
        p3 = ctx3.new_page()
        self.login("eve", page=p3)
        p3.goto("/requests")
        self.assertEqual(self.L("request-item-rq_1", p3).count(), 0)
        ctx3.close()

    def test_pay_decline_cancel(self):
        "[UI-71] pay -> paid (buttons gone, balance falls); decline -> declined; cancel -> cancelled; lists refresh without reload"
        q2 = req("bob", "ada", 300, note="two").body["request_id"]
        self.login("ada")
        self.goto("/requests")
        self.L("request-pay-rq_1").click()
        expect(self.L("request-item-rq_1")).to_have_attribute("data-status", "paid")
        self.assertEqual(self.L("request-pay-rq_1").count(), 0)
        self.assertEqual(self.L("request-decline-rq_1").count(), 0)
        self.assertEqual(bal("ada"), 10000 - 1200)
        self.assertFalse(self.exists("request-error"))
        self.L("request-decline-" + q2).click()
        expect(self.L("request-item-" + q2)).to_have_attribute("data-status", "declined")
        self.assertEqual(self.L("request-pay-" + q2).count(), 0)
        q3 = req("ada", "cy", 50).body["request_id"]
        self.page.reload()
        expect(self.L("request-item-" + q3)).to_be_visible()
        self.L("request-cancel-" + q3).click()
        expect(self.L("request-item-" + q3)).to_have_attribute("data-status", "cancelled")
        self.goto("/")
        expect(self.L("wallet-balance")).to_have_text("88.00 EUR")

    def test_refused_pay_shows_request_error(self):
        "[UI-72] paying a request beyond available funds shows request-error; the request stays pending and payable"
        big = req("cy", "ada", 20000).body["request_id"]
        self.login("ada")
        self.goto("/requests")
        self.L("request-pay-" + big).click()
        expect(self.L("request-error")).to_be_visible()
        self.assertTrue(self.text("request-error"))
        self.assertEqual(self.L("request-item-" + big).get_attribute("data-status"), "pending")
        self.assertEqual(self.L("request-pay-" + big).count(), 1)
        self.assertEqual(bal("ada"), 10000)

    def test_stale_pay_button_disappears(self):
        "[UI-73] request cancelled elsewhere while its pay button is visible: pay -> request-error, list refreshes, pay button gone, status cancelled"
        self.login("ada")
        self.goto("/requests")
        expect(self.L("request-pay-rq_1")).to_be_visible()
        r = call("POST", "/requests/rq_1/cancel", token=tok("bob"))
        self.assertEqual(r.status, 200)
        self.L("request-pay-rq_1").click()
        expect(self.L("request-error")).to_be_visible()
        expect(self.L("request-item-rq_1")).to_have_attribute("data-status", "cancelled")
        expect(self.L("request-pay-rq_1")).to_have_count(0)
        self.assertEqual(bal("ada"), 10000)

    def test_stale_decline_and_cancel(self):
        "[UI-74] decline/cancel refused because the request already moved on: request-error and a refreshed list"
        self.login("ada")
        self.goto("/requests")
        pay_req("ada", "rq_1")  # paid elsewhere
        self.L("request-decline-rq_1").click()
        expect(self.L("request-error")).to_be_visible()
        expect(self.L("request-item-rq_1")).to_have_attribute("data-status", "paid")
        expect(self.L("request-decline-rq_1")).to_have_count(0)

    def test_empty_requests(self):
        "[UI-75] empty-requests when both lists are empty; not shown otherwise"
        reset(base_fixture(requests=[]))
        self.login("dee")
        self.goto("/requests")
        expect(self.L("empty-requests")).to_be_visible()
        req("ada", "dee", 100)
        self.page.reload()
        expect(self.L("incoming-list")).to_be_attached()
        self.assertEqual(self.L("empty-requests").count(), 0)


class Split(Ui):
    def preview(self, amount, handles):
        self.L("split-amount").fill(amount)
        self.L("split-handles").fill(handles)

    def shares(self, handles):
        return [self.text("split-share-" + h) for h in handles]

    def test_preview_matches_the_rule(self):
        "[UI-80] split-preview shows the server's shares before anything is posted (10.00 / 3 -> 3.34, 3.33, 3.33), in handle order"
        self.login("ada")
        self.goto("/split")
        self.reqs.clear()
        self.preview("10.00", "bob, cy, dee")
        expect(self.L("split-share-bob")).to_have_text("3.34 EUR")
        self.assertEqual(self.shares(["bob", "cy", "dee"]), ["3.34 EUR", "3.33 EUR", "3.33 EUR"])
        self.preview("10.00", "dee,cy,bob")
        expect(self.L("split-share-dee")).to_have_text("3.34 EUR")
        self.assertEqual(self.shares(["dee", "cy", "bob"]), ["3.34 EUR", "3.33 EUR", "3.33 EUR"])
        self.assertEqual(self.L("split-preview").locator('[data-testid^="split-share-"]').count(), 3)
        self.assertEqual(self.api_posts("/splits"), [])

    def test_preview_table_rows(self):
        "[UI-81] spec table: 10.00/1000 among 3, 0.01 among 3, 0.05 among 5, 9.99 among 3; caller included counts as a participant"
        self.login("ada")
        self.goto("/split")
        for amount, handles, want in (("10.00", "bob, cy, dee", ["3.34 EUR", "3.33 EUR", "3.33 EUR"]),
                                      ("0.01", "bob, cy, dee", ["0.01 EUR", "0.00 EUR", "0.00 EUR"]),
                                      ("0.05", "bob, cy, dee, eve, op", ["0.01 EUR"] * 5), ("9.99", "bob, cy, dee", ["3.33 EUR"] * 3),
                                      ("10", "ada, bob, cy", ["3.34 EUR", "3.33 EUR", "3.33 EUR"]), ("0.10", "bob", ["0.10 EUR"]),
                                      ("10000000.00", "bob, cy, dee", ["3333333.34 EUR", "3333333.33 EUR", "3333333.33 EUR"])):
            self.preview(amount, handles)
            hs = [h.strip() for h in handles.split(",")]
            expect(self.L("split-share-" + hs[0])).to_have_text(want[0])
            self.assertEqual(self.shares(hs), want, (amount, handles))

    def test_preview_in_other_currencies(self):
        "[UI-82] preview uses the service's minor units: JPY 10 / 3 -> 4,3,3; BHD 10.000 / 3 -> 3.334, 3.333, 3.333"
        for cur, mu, amount, want in (("JPY", 0, "10", ["4 JPY", "3 JPY", "3 JPY"]), ("BHD", 3, "10.000", ["3.334 BHD", "3.333 BHD", "3.333 BHD"])):
            reset(base_fixture(currency=cur, minor_units=mu))
            self.page.context.clear_cookies()
            self.login("ada")
            self.goto("/split")
            self.preview(amount, "bob, cy, dee")
            expect(self.L("split-share-bob")).to_have_text(want[0])
            self.assertEqual(self.shares(["bob", "cy", "dee"]), want)
            self.L("logout-button").click()

    def test_preview_equals_submitted_shares(self):
        "[UI-83] the preview and the submitted split have identical shares; one pending request per other participant"
        self.login("ada")
        self.goto("/split")
        self.preview("10.00", "ada, bob, cy")
        expect(self.L("split-share-bob")).to_have_text("3.33 EUR")
        shown = {h: self.text("split-share-" + h) for h in ("ada", "bob", "cy")}
        self.L("split-note").fill("dinner")
        self.L("split-submit").click()
        self.page.wait_for_timeout(700)
        self.assertFalse(self.exists("split-error"))
        rows = [q for q in requests_of("ada", direction="outgoing", limit=200)["requests"] if q["note"] == "dinner"]
        self.assertEqual(sorted((q["payer_handle"], q["amount"]) for q in rows), [("bob", 333), ("cy", 333)])
        self.assertEqual(shown, {"ada": "3.34 EUR", "bob": "3.33 EUR", "cy": "3.33 EUR"})
        self.assertEqual(bal("ada"), 10000)

    def test_split_errors(self):
        "[UI-84] unknown handle / duplicate handle / empty list / bad decimal -> split-error; nothing is created"
        self.login("ada")
        self.goto("/split")
        before = len(requests_of("ada", limit=200)["requests"])
        for amount, handles in (("10.00", "bob, nobody"), ("10.00", "bob, bob"), ("10.00", ""), ("abc", "bob, cy"), ("10.005", "bob, cy"), ("", "bob")):
            self.preview(amount, handles)
            self.L("split-submit").click()
            expect(self.L("split-error")).to_be_visible()
            self.assertTrue(self.text("split-error"))
        self.assertEqual(len(requests_of("ada", limit=200)["requests"]), before)

    def test_preview_follows_edits_and_whitespace(self):
        "[UI-85] the preview updates as fields change; handles are trimmed"
        self.login("ada")
        self.goto("/split")
        self.preview("10.00", "  bob ,  cy ")
        expect(self.L("split-share-bob")).to_have_text("5.00 EUR")
        self.L("split-amount").fill("10.01")
        expect(self.L("split-share-bob")).to_have_text("5.01 EUR")
        self.assertEqual(self.text("split-share-cy"), "5.00 EUR")
        self.L("split-handles").fill("bob")
        expect(self.L("split-share-bob")).to_have_text("10.01 EUR")
        self.assertEqual(self.L("split-share-cy").count(), 0)


class Authorizations(Ui):
    def fixture(self):
        return azfixture([seed_auth("a_1", "ada", "bob", 2000, note="deposit")])

    def test_list_testids_and_buttons(self):
        "[UI-90] item data-status, formatted amount, expires text == API expires_at; payer: void only; receiver: prefilled capture amount + capture only"
        self.login("ada")
        self.goto("/authorizations")
        item = self.L("authorization-item-a_1")
        expect(item).to_be_visible()
        self.assertEqual(item.get_attribute("data-status"), "open")
        self.assertEqual(self.text("authorization-amount-a_1"), "20.00 EUR")
        expires = az_get("ada", "a_1")["expires_at"]
        self.assertEqual(self.text("authorization-expires-a_1"), expires)
        self.assertEqual(self.L("authorization-captured-a_1").count(), 0)
        self.assertEqual(self.L("authorization-void-a_1").count(), 1)
        self.assertEqual(self.L("authorization-capture-a_1").count(), 0)
        self.assertEqual(self.L("authorization-capture-amount-a_1").count(), 0)
        self.assertEqual(self.L("empty-authorizations").count(), 0)
        ctx2 = self.browser.new_context(viewport=self.VIEW, base_url=BASE)
        p2 = ctx2.new_page()
        self.login("bob", page=p2)
        p2.goto("/authorizations")
        expect(self.L("authorization-item-a_1", p2)).to_be_visible()
        self.assertEqual(self.L("authorization-capture-amount-a_1", p2).input_value(), "20.00")
        self.assertEqual(self.L("authorization-capture-a_1", p2).count(), 1)
        self.assertEqual(self.L("authorization-void-a_1", p2).count(), 0)
        self.assertEqual(self.L("authorization-expires-a_1", p2).inner_text().strip(), expires)
        ctx2.close()
        ctx3 = self.browser.new_context(viewport=self.VIEW, base_url=BASE)
        p3 = ctx3.new_page()
        self.login("eve", page=p3)
        p3.goto("/authorizations")
        self.assertEqual(self.L("authorization-item-a_1", p3).count(), 0)
        expect(self.L("empty-authorizations", p3)).to_be_visible()
        ctx3.close()

    def test_capture_prefill_is_the_remaining_amount(self):
        "[UI-91] the capture input is pre-filled with the REMAINING amount (after a nonfinal API capture: 13.00)"
        capture("bob", "a_1", 700, final=False)
        ctx = self.browser.new_context(viewport=self.VIEW, base_url=BASE)
        p = ctx.new_page()
        self.login("bob", page=p)
        p.goto("/authorizations")
        expect(self.L("authorization-capture-amount-a_1", p)).to_have_value("13.00")
        ctx.close()

    def test_capture_partial_in_ui(self):
        "[UI-92] capture 15.00 of 20.00: status captured, authorization-captured shows 15.00 EUR, controls gone, money moved, hold released"
        self.login("bob")
        self.goto("/authorizations")
        self.L("authorization-capture-amount-a_1").fill("15.00")
        self.L("authorization-capture-a_1").click()
        expect(self.L("authorization-item-a_1")).to_have_attribute("data-status", "captured")
        self.assertEqual(self.text("authorization-captured-a_1"), "15.00 EUR")
        self.assertEqual(self.text("authorization-amount-a_1"), "20.00 EUR")
        for t in ("authorization-capture-a_1", "authorization-capture-amount-a_1", "authorization-void-a_1"):
            self.assertEqual(self.L(t).count(), 0, t)
        self.assertFalse(self.exists("authorization-error"))
        self.assertEqual((bal("ada"), bal("bob")), (8500, 4000))
        self.assertEqual(me("ada")["held"], 0)
        body = self.reqs and mine_posts(self.reqs, "/capture")
        self.assertEqual(json.loads(body[-1].post_data)["amount"], 1500)
        self.goto("/")
        expect(self.L("wallet-balance")).to_have_text("40.00 EUR")

    def test_capture_decimal_validation(self):
        "[UI-93] capture amount '20.005' / 'abc' is refused locally (authorization-error, no request); more than remaining is refused by the server"
        self.login("bob")
        self.goto("/authorizations")
        for bad in ("20.005", "abc", "", "-1"):
            self.reqs.clear()
            self.L("authorization-capture-amount-a_1").fill(bad)
            self.L("authorization-capture-a_1").click()
            expect(self.L("authorization-error")).to_be_visible()
            self.page.wait_for_timeout(150)
            self.assertEqual(mine_posts(self.reqs, "/capture"), [], bad)
        self.L("authorization-capture-amount-a_1").fill("20.01")
        self.L("authorization-capture-a_1").click()
        expect(self.L("authorization-error")).to_be_visible()
        self.assertEqual(az_get("ada", "a_1")["status"], "open")
        self.assertEqual(bal("bob"), 2500)

    def test_void_in_ui(self):
        "[UI-94] payer voids: status voided, controls gone, wallet-held disappears, available restored"
        self.login("ada")
        self.goto("/authorizations")
        self.L("authorization-void-a_1").click()
        expect(self.L("authorization-item-a_1")).to_have_attribute("data-status", "voided")
        self.assertEqual(self.L("authorization-void-a_1").count(), 0)
        self.assertFalse(self.exists("authorization-error"))
        self.goto("/")
        expect(self.L("wallet-available")).to_have_text("100.00 EUR")
        self.assertEqual(self.L("wallet-held").count(), 0)

    def test_stale_capture_voided_elsewhere(self):
        "[UI-95] hold voided elsewhere while the capture button is visible: authorization-error and a refreshed list"
        self.login("bob")
        self.goto("/authorizations")
        expect(self.L("authorization-capture-a_1")).to_be_visible()
        self.assertEqual(void("ada", "a_1").status, 200)
        self.L("authorization-capture-a_1").click()
        expect(self.L("authorization-error")).to_be_visible()
        expect(self.L("authorization-item-a_1")).to_have_attribute("data-status", "voided")
        expect(self.L("authorization-capture-a_1")).to_have_count(0)
        self.assertEqual(bal("bob"), 2500)

    def test_stale_void_after_capture_elsewhere(self):
        "[UI-96] hold captured elsewhere while the void button is visible: authorization-error and a refreshed list"
        self.login("ada")
        self.goto("/authorizations")
        expect(self.L("authorization-void-a_1")).to_be_visible()
        self.assertEqual(capture("bob", "a_1").status, 201)
        self.L("authorization-void-a_1").click()
        expect(self.L("authorization-error")).to_be_visible()
        expect(self.L("authorization-item-a_1")).to_have_attribute("data-status", "captured")
        self.assertEqual(self.text("authorization-captured-a_1"), "20.00 EUR")

    def test_authorize_form(self):
        "[UI-97] authorise form: creates the hold (visibility honoured), wallet-held/available update, form kept, unchanged resubmit creates no second hold"
        self.login("ada")
        where = authorize_page(self)
        self.fill_pay("cy", "25", "tab", "private", prefix="authorize")
        self.L("authorize-submit").click()
        self.page.wait_for_timeout(700)
        rows = [a for a in auths_of("ada", direction="outgoing", limit=200)["authorizations"] if a["note"] == "tab"]
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]["amount"], rows[0]["visibility"], rows[0]["to_handle"]), (2500, "private", "cy"))
        self.assertFalse(self.exists("authorize-error"))
        self.assertEqual((self.L("authorize-handle").input_value(), self.L("authorize-amount").input_value(), self.L("authorize-note").input_value(),
                          self.L("authorize-visibility").input_value()), ("cy", "25", "tab", "private"))
        self.L("authorize-submit").click()
        self.L("authorize-submit").click()
        self.page.wait_for_timeout(700)
        self.assertEqual(len([a for a in auths_of("ada", limit=200)["authorizations"] if a["note"] == "tab"]), 1)
        self.assertEqual(me("ada")["held"], 2000 + 2500)
        self.goto("/")
        expect(self.L("wallet-held")).to_have_text("45.00 EUR")
        expect(self.L("wallet-available")).to_have_text("55.00 EUR")
        expect(self.L("wallet-balance")).to_have_text("100.00 EUR")

    def test_authorize_refused_insufficient_available(self):
        "[UI-98] authorising more than available shows authorize-error (held funds do not count) and keeps the inputs"
        self.login("ada")
        authorize_page(self)
        self.fill_pay("cy", "80.01", "too much", "public", prefix="authorize")
        self.L("authorize-submit").click()
        expect(self.L("authorize-error")).to_be_visible()
        self.assertEqual(self.L("authorize-amount").input_value(), "80.01")
        self.assertEqual(me("ada")["held"], 2000)
        self.fill_pay("cy", "80.00", "exact", "public", prefix="authorize")
        self.L("authorize-submit").click()
        expect(self.L("authorize-error")).to_have_count(0)
        self.page.wait_for_timeout(500)
        self.assertEqual(me("ada")["available"], 0)
        for handle in ("nobody", "ada"):
            self.fill_pay(handle, "0.01", "x", "public", prefix="authorize")
            self.L("authorize-submit").click()
            expect(self.L("authorize-error")).to_be_visible()

    def test_order_and_empty(self):
        "[UI-99] authorization-list children are newest first; empty-authorizations when there are none"
        reset(azfixture(None))
        ids = []
        for i in range(3):
            ids.append(authorize("ada", "bob", 100 + i).body["authorization_id"])
            time.sleep(1.1)
        self.login("ada")
        self.goto("/authorizations")
        order = self.page.eval_on_selector_all('[data-testid="authorization-list"] [data-testid^="authorization-item-"]',
                                               "els => els.map(e => e.getAttribute('data-testid').replace('authorization-item-',''))")
        self.assertEqual(order, ids[::-1])
        self.assertEqual(self.L("empty-authorizations").count(), 0)
        self.login("dee")
        self.goto("/authorizations")
        expect(self.L("empty-authorizations")).to_be_visible()

    def test_expired_seeded_hold_is_shown_expired(self):
        "[UI-100] a seeded open hold whose expiry has passed shows data-status expired with no controls; a closed one never offers capture"
        reset(azfixture([seed_auth("a_1", "ada", "bob", 2000, expires_in=-7200), seed_auth("a_2", "ada", "bob", 100, status="voided"),
                         seed_auth("a_3", "ada", "bob", 300)]))
        self.login("bob")
        self.goto("/authorizations")
        self.assertEqual(self.L("authorization-item-a_1").get_attribute("data-status"), "expired")
        self.assertEqual(self.L("authorization-item-a_2").get_attribute("data-status"), "voided")
        self.assertEqual(self.L("authorization-item-a_3").get_attribute("data-status"), "open")
        for aid in ("a_1", "a_2"):
            self.assertEqual(self.L("authorization-capture-" + aid).count(), 0)
            self.assertEqual(self.L("authorization-capture-amount-" + aid).count(), 0)
        self.assertEqual(self.L("authorization-capture-a_3").count(), 1)

    def test_clock_expiry_visible_without_polling(self):
        "[UI-101] a short-lived hold shows as expired after its deadline on the next read (explicit navigation), controls gone, funds back"
        reset(azfixture(ttl=2))
        aid = authorize("ada", "bob", 3000).body["authorization_id"]
        self.login("ada")
        self.goto("/authorizations")
        expect(self.L("authorization-item-" + aid)).to_have_attribute("data-status", "open")
        time.sleep(3.3)
        self.goto("/authorizations")
        expect(self.L("authorization-item-" + aid)).to_have_attribute("data-status", "expired")
        self.assertEqual(self.L("authorization-void-" + aid).count(), 0)
        self.goto("/")
        expect(self.L("wallet-available")).to_have_text("100.00 EUR")
        self.assertEqual(self.L("wallet-held").count(), 0)


class Upgrade(Ui):
    def export(self):
        r = call("GET", "/_test/export", timeout=10)
        self.assertEqual(r.status, 200)
        return r.body

    def do_import(self, state, scramble=True):
        if scramble:  # make the destination different so the import is observable
            reset(base_fixture(users=[user("zed", 1)], payments=[], requests=[], settlement_operator_ids=[]))
        r = call("POST", "/_test/import", state, timeout=10)
        self.assertEqual(r.status, 204, r)

    def test_session_form_and_committed_retry_survive_import(self):
        "[UI-110] lost response (committed before export): after export/import between browser requests the SAME page stays signed in, keeps the form, retries with the same key/body and recovers the original payment"
        seen = []

        def lose(route):
            if route.request.method != "POST":
                return route.continue_()
            seen.append((route.request.headers.get("idempotency-key"), route.request.post_data))
            route.fetch()
            route.abort("connectionreset")

        self.login("ada")
        self.route_json_post("/payments", lose)
        self.fill_pay("bob", "7.77", "across the upgrade", "private")
        self.L("pay-submit").click()
        expect(self.L("pay-uncertain")).to_be_visible()
        snap = self.export()
        self.do_import(snap)
        self.page.unroute(re.compile(r".*/payments(\?.*)?$"))
        self.assertEqual((self.L("pay-handle").input_value(), self.L("pay-amount").input_value(), self.L("pay-note").input_value()),
                         ("bob", "7.77", "across the upgrade"))
        self.L("pay-submit").click()
        expect(self.L("pay-uncertain")).to_have_count(0)
        expect(self.L("pay-error")).to_have_count(0)
        expect(self.L("wallet-balance")).to_have_text("92.23 EUR")
        retry = mine_posts(self.reqs, "/payments")[-1]
        self.assertEqual(retry.headers.get("idempotency-key"), seen[0][0])
        self.assertEqual(json.loads(retry.post_data), json.loads(seen[0][1]))
        self.assertEqual(bal("ada"), 10000 - 777)
        self.assertEqual(len([p for p in activity("ada", limit=200)["payments"] if p["note"] == "across the upgrade"]), 1)
        expect(self.page.locator('[data-testid^="activity-note-"]', has_text="across the upgrade")).to_have_count(1)
        self.assertEqual(self.text("current-handle"), "ada")

    def test_lost_before_commit_then_import_then_retry(self):
        "[UI-111] a payment that never committed before the export is created exactly once by the retry after import"
        def drop(route):
            if route.request.method != "POST":
                return route.continue_()
            route.abort("connectionreset")

        self.login("ada")
        self.route_json_post("/payments", drop)
        self.fill_pay("bob", "4.00", "never committed")
        self.L("pay-submit").click()
        expect(self.L("pay-uncertain")).to_be_visible()
        snap = self.export()
        self.do_import(snap)
        self.page.unroute(re.compile(r".*/payments(\?.*)?$"))
        self.L("pay-submit").click()
        expect(self.L("wallet-balance")).to_have_text("96.00 EUR")
        expect(self.L("pay-uncertain")).to_have_count(0)
        self.assertEqual(bal("ada"), 9600)

    def test_session_survives_and_pending_request_payable_after_import(self):
        "[UI-112] signed in before the import, still signed in after it; the pending request is payable through the request screen"
        self.login("ada")
        self.goto("/requests")
        expect(self.L("request-pay-rq_1")).to_be_visible()
        snap = self.export()
        self.do_import(snap)
        self.L("request-pay-rq_1").click()
        expect(self.L("request-item-rq_1")).to_have_attribute("data-status", "paid")
        self.assertFalse(self.exists("request-error"))
        self.assertEqual(bal("ada"), 10000 - 1200)
        self.assertEqual(self.text("current-handle"), "ada")
        self.goto("/")
        expect(self.L("wallet-balance")).to_have_text("88.00 EUR")

    def test_refresh_shows_imported_balance(self):
        "[UI-113] after an import that changes balances, wallet-refresh shows the imported numbers (session intact)"
        self.login("ada")
        pay("ada", "bob", 1000)
        snap = self.export()
        reset()  # back to 100.00
        self.page.context.clear_cookies()
        self.do_import(snap, scramble=False)
        self.L("wallet-refresh").click()
        expect(self.L("wallet-balance")).to_have_text("90.00 EUR")
        self.assertEqual(self.text("current-handle"), "ada")

    def test_holds_survive_import_in_ui(self):
        "[UI-114] holds survive an import: wallet-held/available and the authorization list are restored"
        reset(azfixture([seed_auth("a_1", "ada", "bob", 2000)]))
        self.login("ada")
        snap = self.export()
        self.do_import(snap)
        self.L("wallet-refresh").click()
        expect(self.L("wallet-held")).to_have_text("20.00 EUR")
        self.goto("/authorizations")
        expect(self.L("authorization-item-a_1")).to_have_attribute("data-status", "open")


if __name__ == "__main__":
    unittest.main()
