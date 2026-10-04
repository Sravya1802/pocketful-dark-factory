"""Playwright helpers for the stage-2 UI checks (sync API, Chromium). The UI is treated as a black box:
only URLs, data-testid attributes and visible behaviour are used."""
import os
import re
import sys
import time
import unittest

from lib import *  # API helpers (reset, tok, pay, ...)

try:
    from playwright.sync_api import sync_playwright, expect
except ImportError:  # pragma: no cover
    sys.stderr.write("playwright is required for the UI checks; use the harness venv: dark-factory-wearedevs/.venv/bin/python\n")
    raise

HEADLESS = os.environ.get("HEADED") != "1"
T_MS = 6000


def sel(testid):
    return '[data-testid="%s"]' % testid


class Ui(unittest.TestCase):
    maxDiff = None
    pw = None
    browser = None
    VIEW = {"width": 1280, "height": 900}

    @classmethod
    def setUpClass(cls):
        cls.pw = sync_playwright().start()
        cls.browser = cls.pw.chromium.launch(headless=HEADLESS)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()

    def fixture(self):
        return base_fixture()

    def setUp(self):
        reset(self.fixture())
        self.ctx = self.browser.new_context(viewport=self.VIEW, base_url=BASE)
        self.ctx.set_default_timeout(T_MS)
        self.page = self.ctx.new_page()
        self.reqs = []
        self.page.on("request", lambda r: self.reqs.append(r))
        expect.set_options(timeout=T_MS)

    def tearDown(self):
        self.ctx.close()

    # ---- helpers
    def L(self, testid, page=None):
        return (page or self.page).locator(sel(testid))

    def exists(self, testid, page=None):
        return self.L(testid, page).count() > 0

    def text(self, testid, page=None):
        return self.L(testid, page).first.inner_text().strip()

    def login(self, name="ada", page=None, password=PW):
        p = page or self.page
        p.context.clear_cookies()
        p.goto("/login")
        p.evaluate("() => { try { localStorage.clear(); sessionStorage.clear(); } catch (e) {} }")
        p.goto("/login")
        self.L("login-email", p).fill(name + "@example.com")
        self.L("login-password", p).fill(password)
        self.L("login-submit", p).click()
        expect(self.L("current-user", p)).to_be_visible()
        return p

    def goto(self, path, page=None):
        p = page or self.page
        p.goto(path)
        expect(self.L("current-user", p)).to_be_visible()

    def api_posts(self, suffix):
        return [r for r in self.reqs if r.method == "POST" and r.url.split("?")[0].endswith(suffix)]

    def fill_pay(self, handle="bob", amount="15.00", note="", vis=None, prefix="pay"):
        self.L(prefix + "-handle").fill(handle)
        self.L(prefix + "-amount").fill(amount)
        self.L(prefix + "-note").fill(note)
        if vis:
            self.L(prefix + "-visibility").select_option(vis)

    def bal_amount(self, tid="wallet-balance"):
        return int(self.L(tid).first.get_attribute("data-amount"))

    def route_json_post(self, suffix, handler):
        self.page.route(re.compile(r".*" + re.escape(suffix) + r"(\?.*)?$"), handler)

    def settle_ui(self, ms=400):
        self.page.wait_for_timeout(ms)
