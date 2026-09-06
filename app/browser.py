# ============================================================
# app/browser.py
# ============================================================


from playwright._impl._api_structures import ViewportSize
from playwright.sync_api import sync_playwright

from config import (
    HEADLESS,
    BROWSER_CHANNEL,
    VIEWPORT_WIDTH,
    VIEWPORT_HEIGHT,
    LOCALE,
    TIMEZONE_ID,
)

from .recovery import (
    create_new_page,
    close_page_safely,
)


class GoogleMapsBrowser:
    def __init__(
        self,
        headless=None,
        logger=None,
    ):

        self.headless = HEADLESS if headless is None else headless

        self.logger = logger

        self.playwright = None
        self.browser = None
        self.context = None
        self.page = None

    # ========================================================
    # START
    # ========================================================

    def start(self):

        self.playwright = sync_playwright().start()

        launch_options = {
            "headless": self.headless,
            "args": [
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage",
                "--disable-gpu",
                "--no-sandbox",
                "--disable-background-networking",
                "--disable-background-timer-throttling",
                "--disable-renderer-backgrounding",
                "--disable-features=Translate,BackForwardCache",
            ],
        }

        if BROWSER_CHANNEL:
            launch_options["channel"] = BROWSER_CHANNEL

        self.browser = self.playwright.chromium.launch(**launch_options)
        viewport: ViewportSize = {
            "width": int(VIEWPORT_WIDTH),
            "height": int(VIEWPORT_HEIGHT),
        }
        self.context = self.browser.new_context(
            locale=LOCALE, timezone_id=TIMEZONE_ID, viewport=viewport
        )

        self.page = create_new_page(self.context)

        return self

    # ========================================================
    # NEW PAGE
    # ========================================================

    def new_page(self):

        if not self.context:
            raise RuntimeError("Browser is not started.")

        self.page = create_new_page(self.context)

        return self.page

    # ========================================================
    # RECREATE PAGE
    # ========================================================

    def recreate_page(self):

        if not self.context:
            raise RuntimeError("Browser context unavailable.")

        close_page_safely(self.page)

        self.page = create_new_page(self.context)

        return self.page

    # ========================================================
    # RESTART BROWSER
    # ========================================================

    def restart(self):

        self.close()

        return self.start()

    # ========================================================
    # CLOSE
    # ========================================================

    def close(self):

        try:
            close_page_safely(self.page)

        except Exception:
            pass

        try:
            if self.context:
                self.context.close()

        except Exception:
            pass

        try:
            if self.browser:
                self.browser.close()

        except Exception:
            pass

        try:
            if self.playwright:
                self.playwright.stop()

        except Exception:
            pass

        self.page = None
        self.context = None
        self.browser = None
        self.playwright = None

    # ========================================================
    # CONTEXT
    # ========================================================

    def get_context(self):
        return self.context

    # ========================================================
    # PAGE
    # ========================================================

    def get_page(self):
        return self.page

    # ========================================================
    # HEALTH
    # ========================================================

    def is_page_healthy(self):

        if self.page is None:
            return False

        try:
            if self.page.is_closed():
                return False

            _ = self.page.url

            return True

        except Exception:
            return False
