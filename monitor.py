import glob
import json
import logging
import logging.handlers
import os
import re
import smtplib
import subprocess
import sys
import threading
import time
import urllib.parse
import webbrowser
from concurrent.futures import ThreadPoolExecutor, as_completed, Future
from dataclasses import dataclass, field
from datetime import datetime
from email.mime.text import MIMEText
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from dotenv import load_dotenv
from plyer import notification
from selenium import webdriver
from selenium.common.exceptions import (
    NoSuchElementException,
    StaleElementReferenceException,
    TimeoutException,
    WebDriverException,
)
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.remote.webelement import WebElement
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import Select, WebDriverWait

# ── Optional: Scrapling (stealth browser + adaptive selectors) ────────────────
try:
    from scrapling import StealthyFetcher, Adaptor
    _HAS_SCRAPLING = True
except ImportError:
    _HAS_SCRAPLING = False

# ── Environment ───────────────────────────────────────────────────────────────

load_dotenv()

MODE = os.getenv("MONITOR_MODE", "test-merch")
TARGET_URL = os.getenv(
    "TARGET_URL",
    "https://shop.royalchallengers.com/merchandise/152"
    if MODE == "test-merch"
    else "https://shop.royalchallengers.com/ticket",
)
CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL", "120"))
MIN_PRICE = int(os.getenv("MIN_PRICE", "2000"))
MAX_PRICE = int(os.getenv("MAX_PRICE", "5000"))
MAX_RETRIES = int(os.getenv("MAX_RETRIES", "3"))
SCREENSHOT_DIR = Path(os.getenv("SCREENSHOT_DIR", "screenshots"))
SCREENSHOT_KEEP_CYCLES = int(os.getenv("SCREENSHOT_KEEP_CYCLES", "10"))
LOG_FILE = os.getenv("LOG_FILE", "monitor.log")
LOG_MAX_BYTES = int(os.getenv("LOG_MAX_BYTES", str(5 * 1024 * 1024)))  # 5 MB
LOG_BACKUP_COUNT = int(os.getenv("LOG_BACKUP_COUNT", "3"))
ENABLE_NOTIFICATIONS = os.getenv("ENABLE_NOTIFICATIONS", "1") == "1"

QUANTITY = int(os.getenv("QUANTITY", "4"))
MIN_QUANTITY = int(os.getenv("MIN_QUANTITY", "2"))
NAMES: List[str] = [
    n.strip()
    for n in os.getenv(
        "NAMES", "Srinath Ekbote,Anjali Ekbote,Prabh Ekbote,Shruti Ekbote"
    ).split(",")
    if n.strip()
]
UPI_VPA = os.getenv("UPI_VPA", "srinath7788ekbote1@ybl")
CONTACT_EMAIL = os.getenv("CONTACT_EMAIL", "")
CONTACT_PHONE = os.getenv("CONTACT_PHONE", "")
MERCH_SIZE = os.getenv("MERCH_SIZE", "L")
MERCH_CATEGORY = os.getenv("MERCH_CATEGORY", "")
# Shipping address
ADDRESS_LINE1 = os.getenv("ADDRESS_LINE1", "")
ADDRESS_LINE2 = os.getenv("ADDRESS_LINE2", "")
ADDRESS_LANDMARK = os.getenv("ADDRESS_LANDMARK", "")
ADDRESS_CITY = os.getenv("ADDRESS_CITY", "")
ADDRESS_STATE = os.getenv("ADDRESS_STATE", "")
ADDRESS_PINCODE = os.getenv("ADDRESS_PINCODE", "")
GENDER = os.getenv("GENDER", "")  # Male, Female, Others
# Email notification (auto-send via Gmail SMTP)
SMTP_EMAIL = os.getenv("SMTP_EMAIL", "")  # your gmail address
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")  # gmail app password (16 chars)
NOTIFY_EMAIL = os.getenv("NOTIFY_EMAIL", "")  # recipient (defaults to CONTACT_EMAIL)
# WhatsApp notification (comma-separated phones with country code)
NOTIFY_WHATSAPP: List[str] = [
    n.strip() for n in os.getenv("NOTIFY_WHATSAPP", "").split(",") if n.strip()
]
MANUAL_SEAT_TIMEOUT = int(os.getenv("MANUAL_SEAT_TIMEOUT", "60"))
OTP_TIMEOUT = int(os.getenv("OTP_TIMEOUT", "90"))

# ── Ticket-specific ───────────────────────────────────────────────────────────
# Preferred stands in priority order (case-insensitive partial match).
# Set PREFERRED_STANDS in .env with current-season stand names, e.g.:
#   PREFERRED_STANDS=sun pharma a,e stand,boat c stand
# If unset or empty, falls back to cheapest-first ordering.
PREFERRED_STANDS: List[str] = [
    s.strip().lower()
    for s in os.getenv(
        "PREFERRED_STANDS",
        "",
    ).split(",")
    if s.strip()
]
# Number of tickets to select in the "How many tickets?" popup (1–6)
TICKET_QUANTITY = int(os.getenv("TICKET_QUANTITY", str(QUANTITY)))
PRICE_PER_TICKET_MAX = int(os.getenv("PRICE_PER_TICKET_MAX", "5000"))
MAX_STAND_WORKERS = int(os.getenv("MAX_STAND_WORKERS", "7"))
# Stagger worker starts by this many seconds so they don't all slam the server at once
WORKER_STARTUP_JITTER = float(os.getenv("WORKER_STARTUP_JITTER", "0.4"))
CHROME_PROFILE_DIR = os.getenv("CHROME_PROFILE_DIR", str(Path.cwd() / "chrome_profile"))

# Stealth browser for parallel workers (requires scrapling + camoufox)
USE_STEALTH_BROWSER = os.getenv("USE_STEALTH_BROWSER", "0") == "1"

# ── Semantic keyword scoring ──────────────────────────────────────────────────
# Instead of hardcoded selectors, we score every visible button/link by keywords.
# Higher score = more likely the action we want.

# Words that indicate a *purchase* action button (primary CTA)
PURCHASE_KEYWORDS = {
    "add to bag": 10, "add to cart": 10, "buy now": 10, "buy ticket": 10,
    "buy tickets": 10, "book now": 10, "book ticket": 10, "book tickets": 10,
    "get ticket": 10, "get tickets": 10, "purchase": 9, "enroll": 8,
    "register": 7, "subscribe": 7, "reserve": 8, "select seat": 8,
    "select seats": 8, "add": 4, "buy": 6, "book": 5, "get": 3,
}
# Words that indicate a *forward/checkout* navigation
CHECKOUT_KEYWORDS = {
    "proceed to checkout": 10, "proceed to payment": 10, "place order": 10,
    "checkout": 9, "proceed": 8, "continue to payment": 9, "continue": 6,
    "pay now": 9, "pay": 7, "complete order": 9, "confirm order": 9,
    "next": 5, "submit": 6, "go to cart": 8, "go to bag": 8,
    "view cart": 7, "view bag": 7, "proceed to pay": 9,
}
# Words that indicate a *cart/bag* action
CART_KEYWORDS = {
    "view cart": 8, "view bag": 8, "go to cart": 8, "go to bag": 8,
    "my cart": 7, "my bag": 7, "cart": 5, "bag": 4, "basket": 5,
}
# Words that mean item is UNAVAILABLE (negative signals)
NEGATIVE_KEYWORDS = {
    "sold out", "coming soon", "pre-sale", "presale", "not available",
    "unavailable", "out of stock", "notify me", "waitlist", "wait list",
    "disabled", "expired", "login", "sign in", "sign up", "register",
}
# Words for nav/chrome elements to IGNORE
IGNORE_KEYWORDS = {
    "home", "news", "team", "contact", "about", "faq", "help",
    "privacy", "terms", "cookie", "footer", "header", "menu", "nav",
    "search", "filter", "sort", "share", "follow", "instagram", "facebook",
    "twitter", "youtube", "close", "dismiss", "cancel", "back",
    "rcb tv", "rcb bar", "echo of fans", "more", "shop",
}
# UPI keywords
UPI_KEYWORDS = {"upi", "bhim", "google pay", "phonepe", "paytm", "vpa"}

# Link keywords that suggest a ticket booking URL (same-domain only)
TICKET_LINK_KEYWORDS = (
    "ticket", "book", "buy-ticket", "buy_ticket",
    "fixtures", "match", "ipl", "schedule",
)

LOGIN_MOBILE_INPUT_XPATHS = [
    "//input[contains(@placeholder, 'Mobile') or contains(@placeholder, 'mobile') or contains(@placeholder, 'Phone') or contains(@placeholder, 'phone')]",
    "//input[@type='tel']",
    "//input[contains(@name, 'mobile') or contains(@name, 'phone') or contains(@id, 'mobile') or contains(@id, 'phone')]",
]
LOGIN_CONTINUE_XPATHS = [
    "//button[contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'continue')]",
    "//button[contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'send otp')]",
    "//button[contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'get otp')]",
    "//button[@type='submit']",
]
LOGIN_VERIFY_XPATHS = [
    "//button[contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'verify')]",
    "//button[contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'login')]",
    "//button[contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'submit')]",
    "//button[contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'continue')]",
    "//button[@type='submit']",
]


# ── Logging ───────────────────────────────────────────────────────────────────

def setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s",
        handlers=[
            logging.handlers.RotatingFileHandler(
                LOG_FILE, maxBytes=LOG_MAX_BYTES,
                backupCount=LOG_BACKUP_COUNT, encoding="utf-8",
            ),
            logging.StreamHandler(sys.stdout),
        ],
    )


@dataclass(frozen=True)
class Config:
    mode: str = MODE
    target_url: str = TARGET_URL
    check_interval: int = CHECK_INTERVAL
    min_price: int = MIN_PRICE
    max_price: int = MAX_PRICE
    max_retries: int = MAX_RETRIES
    screenshot_dir: Path = SCREENSHOT_DIR
    quantity: int = QUANTITY
    min_quantity: int = MIN_QUANTITY
    upi_vpa: str = UPI_VPA
    captcha_markers: Tuple[str, ...] = (
        "captcha", "i am human", "verify you are human",
        "security check", "cloudflare",
    )


# ══════════════════════════════════════════════════════════════════════════════
# STEALTH DRIVER ADAPTER — wraps Scrapling's StealthyFetcher as a Selenium-like driver
# ══════════════════════════════════════════════════════════════════════════════

class _StealthElement:
    """Thin wrapper around a Scrapling Adaptor node to mimic WebElement."""

    def __init__(self, node, page) -> None:
        self._node = node
        self._page = page
        self.tag_name = node.tag or ""

    # ── Selenium WebElement interface ─────────────────────────────────────

    @property
    def text(self) -> str:
        return self._node.text or ""

    def get_attribute(self, name: str) -> Optional[str]:
        return self._node.attrib.get(name)

    def is_displayed(self) -> bool:
        style = self._node.attrib.get("style", "")
        if "display:none" in style.replace(" ", "") or "visibility:hidden" in style.replace(" ", ""):
            return False
        return True

    def is_enabled(self) -> bool:
        return self._node.attrib.get("disabled") is None

    def click(self) -> None:
        # Build a JS selector to click via Playwright
        eid = self._node.attrib.get("id")
        if eid:
            self._page.evaluate(f'document.getElementById("{eid}").click()')
        else:
            # Fallback: use XPath from Scrapling node
            xpath = self._build_xpath()
            self._page.evaluate(
                f"""(function() {{
                    var r = document.evaluate('{xpath}', document, null,
                        XPathResult.FIRST_ORDERED_NODE_TYPE, null);
                    if (r.singleNodeValue) r.singleNodeValue.click();
                }})()"""
            )

    def send_keys(self, *value) -> None:
        eid = self._node.attrib.get("id")
        if eid:
            selector = f"#{eid}"
        else:
            name = self._node.attrib.get("name")
            if name:
                selector = f"[name='{name}']"
            else:
                selector = self.tag_name
        try:
            pw_el = self._page.query_selector(selector)
            if pw_el:
                pw_el.fill("".join(str(v) for v in value if not isinstance(v, Keys)))
                return
        except Exception:
            pass

    def _build_xpath(self) -> str:
        """Construct a rough XPath for this node."""
        tag = self.tag_name or "*"
        eid = self._node.attrib.get("id")
        if eid:
            return f'//*[@id="{eid}"]'
        classes = self._node.attrib.get("class", "")
        text_snip = (self._node.text or "")[:30].replace("'", "\\'")
        if text_snip:
            return f"//{tag}[contains(text(),'{text_snip}')]"
        if classes:
            first_cls = classes.split()[0]
            return f"//{tag}[contains(@class,'{first_cls}')]"
        return f"//{tag}"


class StealthDriverAdapter:
    """Wraps Scrapling StealthyFetcher + a Playwright page to expose a
    minimal Selenium-WebDriver-compatible interface so PageAnalyzer,
    _click, _scroll_to, _screenshot, etc. work unchanged.

    Only used by parallel workers 1..N when USE_STEALTH_BROWSER=1.
    """

    def __init__(self) -> None:
        if not _HAS_SCRAPLING:
            raise ImportError("scrapling is not installed — pip install scrapling[camoufox]")
        self._fetcher = StealthyFetcher()
        self._page = None          # Playwright page (set after first .get())
        self._current_url = ""
        self.page_source = ""

    # ── Navigation ────────────────────────────────────────────────────────

    @property
    def current_url(self) -> str:
        if self._page:
            try:
                return self._page.url
            except Exception:
                pass
        return self._current_url

    def get(self, url: str) -> None:
        """Navigate to *url* using the stealth browser."""
        response = self._fetcher.fetch(url)
        self._page = response.page          # underlying Playwright page
        self._current_url = url
        self.page_source = response.html_content if hasattr(response, "html_content") else str(response)

    def refresh(self) -> None:
        if self._page:
            self._page.reload(wait_until="domcontentloaded")
            self.page_source = self._page.content()

    # ── Element finding ───────────────────────────────────────────────────

    def find_elements(self, by: str, value: str) -> List["_StealthElement"]:
        """Find elements from the current page using Scrapling Adaptor."""
        if not self.page_source:
            return []
        adaptor = Adaptor(self.page_source)
        try:
            if by == By.XPATH:
                nodes = adaptor.xpath(value)
            elif by == By.CSS_SELECTOR:
                nodes = adaptor.css(value)
            elif by == By.ID:
                node = adaptor.css(f"#{value}")
                nodes = [node] if node else []
            elif by == By.TAG_NAME:
                nodes = adaptor.css(value)
            elif by == By.CLASS_NAME:
                nodes = adaptor.css(f".{value}")
            else:
                nodes = adaptor.xpath(f"//*[contains(text(),'{value}')]")
        except Exception:
            return []
        if nodes is None:
            return []
        if not isinstance(nodes, (list, tuple)):
            nodes = [nodes]
        return [_StealthElement(n, self._page) for n in nodes if n is not None]

    def find_element(self, by: str, value: str):
        els = self.find_elements(by, value)
        if not els:
            raise NoSuchElementException(f"No element found for {by}={value}")
        return els[0]

    # ── JavaScript ────────────────────────────────────────────────────────

    def execute_script(self, script: str, *args):
        if self._page:
            return self._page.evaluate(script)
        return None

    # ── Misc driver interface ─────────────────────────────────────────────

    def set_page_load_timeout(self, timeout: int) -> None:
        pass  # Playwright uses its own timeout model

    def save_screenshot(self, filename: str) -> bool:
        if self._page:
            try:
                self._page.screenshot(path=filename)
                return True
            except Exception:
                return False
        return False

    def quit(self) -> None:
        if self._page:
            try:
                self._page.context.close()
            except Exception:
                pass
            try:
                self._page.context.browser.close()
            except Exception:
                pass
            self._page = None

    @property
    def title(self) -> str:
        if self._page:
            try:
                return self._page.title()
            except Exception:
                return ""
        return ""


# ══════════════════════════════════════════════════════════════════════════════
# SMART PAGE ANALYZER — scores every visible element by purpose
# ══════════════════════════════════════════════════════════════════════════════

class PageAnalyzer:
    """Scans the current page DOM and classifies interactive elements."""

    def __init__(self, driver: webdriver.Chrome) -> None:
        self.driver = driver

    # ── Core scanner ──────────────────────────────────────────────────────────

    def _get_all_interactive(self) -> List[WebElement]:
        """Get all visible buttons, links, and submit inputs on the page."""
        els: List[WebElement] = []
        for tag_xpath in (
            "//button", "//a", "//input[@type='submit']", "//input[@type='button']",
            "//*[@role='button']",
        ):
            try:
                els.extend(self.driver.find_elements(By.XPATH, tag_xpath))
            except WebDriverException:
                pass
        # Deduplicate and filter visible
        seen: set = set()
        result: List[WebElement] = []
        for el in els:
            eid = id(el)
            if eid in seen:
                continue
            seen.add(eid)
            try:
                if el.is_displayed():
                    result.append(el)
            except (StaleElementReferenceException, WebDriverException):
                pass
        return result

    @staticmethod
    def _el_text(el: WebElement) -> str:
        """Get the readable text of an element (text + value + aria-label)."""
        parts = []
        try:
            if el.text:
                parts.append(el.text.strip())
            for attr in ("value", "aria-label", "title", "alt"):
                v = el.get_attribute(attr)
                if v:
                    parts.append(v.strip())
        except (StaleElementReferenceException, WebDriverException):
            pass
        return " ".join(parts)

    def _score_element(
        self, el: WebElement, keywords: Dict[str, int]
    ) -> Tuple[int, str]:
        """Score an element against a keyword dict. Returns (score, matched_text)."""
        text = self._el_text(el).lower()
        if not text:
            return 0, ""
        # Skip if it matches ignore/negative keywords
        for neg in IGNORE_KEYWORDS:
            if neg in text and len(text) < 40:  # short text = nav element
                return -1, text
        for neg in NEGATIVE_KEYWORDS:
            if neg in text:
                return -10, text
        # Score by keyword match
        best_score = 0
        for kw, score in keywords.items():
            if kw in text:
                best_score = max(best_score, score)
        return best_score, text

    def _best_match(
        self, keywords: Dict[str, int], min_score: int = 3
    ) -> Optional[Tuple[WebElement, int, str]]:
        """Find the highest-scoring clickable element for the given keywords."""
        elements = self._get_all_interactive()
        candidates: List[Tuple[WebElement, int, str]] = []
        for el in elements:
            try:
                if not el.is_enabled():
                    continue
            except (StaleElementReferenceException, WebDriverException):
                continue
            score, text = self._score_element(el, keywords)
            if score >= min_score:
                candidates.append((el, score, text))

        if not candidates:
            return None
        # Sort by score desc, pick highest
        candidates.sort(key=lambda x: x[1], reverse=True)
        return candidates[0]

    # ── Scrapling adaptive fallback ───────────────────────────────────────────

    def _scrapling_adaptive_fallback(
        self, keywords: Dict[str, int], label: str
    ) -> Optional[WebElement]:
        """Last-resort element finder using Scrapling's Adaptor when keyword
        scoring found nothing.  Searches the page HTML for buttons/links whose
        text matches any keyword, then maps the result back to a real
        Selenium WebElement (or _StealthElement) so callers can click it.
        Returns None if Scrapling is unavailable or finds nothing.
        """
        if not _HAS_SCRAPLING:
            return None
        try:
            html = getattr(self.driver, "page_source", None)
            if not html:
                html = self.driver.execute_script("return document.documentElement.outerHTML")
            if not html:
                return None
            adaptor = Adaptor(html)
            # Search for buttons and links
            candidates = []
            for tag in ("button", "a", "input[type=submit]", "input[type=button]"):
                for node in (adaptor.css(tag) or []):
                    node_text = (node.text or "").strip().lower()
                    if not node_text:
                        continue
                    for kw, score in keywords.items():
                        if kw in node_text:
                            candidates.append((node, score, node_text))
                            break
            if not candidates:
                return None
            candidates.sort(key=lambda x: x[1], reverse=True)
            best_node, best_score, best_text = candidates[0]
            logging.info("Scrapling fallback found %s (score=%s): '%s'", label, best_score, best_text[:80])
            # Map back to a real WebElement via unique attributes
            eid = best_node.attrib.get("id")
            if eid:
                try:
                    return self.driver.find_element(By.ID, eid)
                except Exception:
                    pass
            # Try by exact text via XPath
            safe_text = best_text.replace("'", "\\'")[:60]
            for tag in ("button", "a"):
                try:
                    xpath = f"//{tag}[contains(translate(normalize-space(.),'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'{safe_text}')]"
                    els = self.driver.find_elements(By.XPATH, xpath)
                    for el in els:
                        try:
                            if el.is_displayed():
                                return el
                        except Exception:
                            continue
                except Exception:
                    pass
            return None
        except Exception as exc:
            logging.debug("Scrapling adaptive fallback error: %s", exc)
            return None

    # ── Public detection methods ──────────────────────────────────────────────

    def find_purchase_button(self) -> Optional[WebElement]:
        """Find the primary purchase / add-to-cart / book CTA on the page."""
        match = self._best_match(PURCHASE_KEYWORDS, min_score=4)
        if match:
            el, score, text = match
            logging.info("Found purchase button (score=%s): '%s'", score, text[:80])
            return el
        # Fallback: Scrapling adaptive selector
        return self._scrapling_adaptive_fallback(PURCHASE_KEYWORDS, "purchase button")

    def find_checkout_button(self) -> Optional[WebElement]:
        """Find checkout / proceed / place-order button."""
        match = self._best_match(CHECKOUT_KEYWORDS, min_score=5)
        if match:
            el, score, text = match
            logging.info("Found checkout button (score=%s): '%s'", score, text[:80])
            return el
        # Fallback: Scrapling adaptive selector
        return self._scrapling_adaptive_fallback(CHECKOUT_KEYWORDS, "checkout button")

    def find_cart_button(self) -> Optional[WebElement]:
        """Find view-cart / go-to-bag link or icon button."""
        # Priority 1: cart icon / badge in header (href-based)
        for xp in (
            "//a[contains(@href, '/cart')]",
            "//a[contains(@href, '/bag')]",
            "//a[contains(@href, '/basket')]",
            "//*[contains(@class, 'cart-icon') or contains(@class, 'cartIcon') or contains(@class, 'bag-icon') or contains(@class, 'bagIcon')]",
            "//*[contains(@class, 'cart') and (contains(@class, 'btn') or contains(@class, 'icon') or contains(@class, 'badge'))]",
            "//a[contains(@class, 'cart') or contains(@class, 'bag')]",
        ):
            try:
                els = self.driver.find_elements(By.XPATH, xp)
                for e in els:
                    if e.is_displayed():
                        logging.info("Found cart link/icon: %s", e.get_attribute("href") or e.get_attribute("class"))
                        return e
            except WebDriverException:
                pass
        # Priority 2: keyword-matched buttons, but EXCLUDE elements that are purchase buttons
        match = self._best_match(CART_KEYWORDS, min_score=5)
        if match:
            el, score, text = match
            # Skip if it's actually a purchase button ("add to bag" etc.)
            purchase_score, _ = self._score_element(el, PURCHASE_KEYWORDS)
            if purchase_score >= 6:
                logging.info("Skipping cart candidate '%s' — looks like purchase button", text[:60])
            else:
                logging.info("Found cart button (score=%s): '%s'", score, text[:80])
                return el
        return None

    def find_cart_overlay(self) -> Optional[WebElement]:
        """Detect a cart drawer/popup/overlay that appeared after adding to cart."""
        overlay_xpaths = (
            "//*[contains(@class, 'cart-drawer') or contains(@class, 'cartDrawer') or contains(@class, 'cart-popup') or contains(@class, 'cart-overlay')]",
            "//*[contains(@class, 'mini-cart') or contains(@class, 'minicart') or contains(@class, 'miniCart')]",
            "//*[contains(@class, 'drawer') and contains(@class, 'open')]",
            "//*[contains(@class, 'cart') and contains(@class, 'sidebar')]",
            "//*[contains(@class, 'offcanvas') and contains(@class, 'show')]",
            "//*[contains(@class, 'modal') and contains(@class, 'show') and (contains(@class, 'cart') or contains(@class, 'bag'))]",
        )
        for xp in overlay_xpaths:
            try:
                els = self.driver.find_elements(By.XPATH, xp)
                for el in els:
                    if el.is_displayed():
                        logging.info("Found cart overlay: %s", (el.get_attribute('class') or '')[:60])
                        return el
            except WebDriverException:
                pass
        return None

    def find_popup_or_alert(self) -> Optional[WebElement]:
        """Detect any popup/alert/toast that appeared (e.g. 'please select size')."""
        popup_xpaths = (
            "//*[contains(@class, 'modal') and contains(@class, 'show')]",
            "//*[contains(@class, 'popup') and contains(@class, 'show')]",
            "//*[contains(@class, 'alert') and contains(@class, 'show')]",
            "//*[contains(@class, 'toast') and contains(@class, 'show')]",
            "//*[contains(@class, 'toast') and not(contains(@class, 'hide'))]",
            "//*[contains(@class, 'snackbar')]",
            "//*[contains(@class, 'notification') and contains(@class, 'visible')]",
            "//*[contains(@role, 'alert') or contains(@role, 'alertdialog')]",
            "//*[contains(@class, 'modal-dialog')]//parent::*[contains(@class, 'show')]",
        )
        for xp in popup_xpaths:
            try:
                els = self.driver.find_elements(By.XPATH, xp)
                for el in els:
                    if el.is_displayed():
                        text = (el.text or "").strip()
                        if text and len(text) < 500:  # real popup, not huge overlay
                            logging.info("Found popup/alert: '%s'", text[:120])
                            return el
            except WebDriverException:
                pass
        return None

    def find_upi_option(self) -> Optional[WebElement]:
        """Find UPI payment method radio/tab/button on payment gateways."""
        # First try: direct text match on common payment gateway structures
        # Juspay / Razorpay / Paytm gateways use divs/anchors/spans
        for xp in (
            "//*[normalize-space(text())='UPI']",
            "//*[normalize-space(text())='UPI / QR']",
            "//div[contains(text(),'UPI')]",
            "//a[contains(text(),'UPI')]",
            "//span[contains(text(),'UPI')]",
            "//label[contains(text(),'UPI')]",
            "//*[contains(@class,'upi') or contains(@id,'upi')]",
            "//*[contains(@data-method,'upi')]",
        ):
            try:
                els = self.driver.find_elements(By.XPATH, xp)
                for el in els:
                    if el.is_displayed():
                        text = (el.text or "").strip()
                        logging.info("Found UPI option: '%s'", text[:60])
                        return el
            except WebDriverException:
                pass
        # Fallback: scored keyword match on all interactive + labels
        elements = self._get_all_interactive()
        for xp in (
            "//label", "//div[contains(@class, 'payment')]//div",
            "//div[contains(@class, 'method')]//div",
        ):
            try:
                elements.extend(self.driver.find_elements(By.XPATH, xp))
            except WebDriverException:
                pass
        for el in elements:
            text = self._el_text(el).lower()
            cls = (el.get_attribute("class") or "").lower()
            if any(kw in text or kw in cls for kw in UPI_KEYWORDS):
                try:
                    if el.is_displayed():
                        logging.info("Found UPI option (fallback): '%s'", text[:60])
                        return el
                except (StaleElementReferenceException, WebDriverException):
                    pass
        return None

    def find_vpa_input(self) -> Optional[WebElement]:
        """Find UPI VPA / UPI ID input field. Excludes email/phone fields."""
        inputs = self.driver.find_elements(By.XPATH, "//input")
        for inp in inputs:
            try:
                if not inp.is_displayed():
                    continue
                inp_type = (inp.get_attribute("type") or "").lower()
                # Never match email or tel inputs as VPA
                if inp_type in ("email", "tel", "password", "hidden"):
                    continue
                attrs = " ".join(
                    (inp.get_attribute(a) or "").lower()
                    for a in ("placeholder", "name", "id", "aria-label")
                )
                # Skip if attributes say it's email/phone/name/address
                if any(excl in attrs for excl in ("email", "phone", "mobile", "name", "address", "city", "pin", "state", "gender")):
                    continue

                # Direct attribute match
                if any(kw in attrs for kw in ("vpa", "upi")):
                    logging.info("Found VPA input: placeholder='%s'",
                                 inp.get_attribute("placeholder"))
                    return inp

                # Placeholder pattern: "Username@bankname" or "abc@xyz"
                placeholder = (inp.get_attribute("placeholder") or "").lower()
                if "@" in placeholder and "bankname" in placeholder:
                    logging.info("Found VPA input via placeholder pattern: '%s'", placeholder)
                    return inp

                # Check surrounding label text for "UPI" or "VPA"
                label_text = ""
                try:
                    inp_id = inp.get_attribute("id")
                    if inp_id:
                        lbl = self.driver.find_element(By.XPATH, f"//label[@for='{inp_id}']")
                        label_text = (lbl.text or "").lower()
                except (NoSuchElementException, WebDriverException):
                    pass
                if not label_text:
                    try:
                        container = inp.find_element(By.XPATH, "./ancestor::div[1]")
                        label_text = (container.text or "").lower()[:150]
                    except WebDriverException:
                        pass
                if any(kw in label_text for kw in ("upi", "vpa", "upi id")):
                    logging.info("Found VPA input via label: '%s'", label_text[:60])
                    return inp
            except (StaleElementReferenceException, WebDriverException):
                pass
        return None

    def find_pay_button(self) -> Optional[WebElement]:
        """Find the final Pay / Send Request button."""
        match = self._best_match(
            {"pay": 8, "pay now": 10, "send request": 9, "proceed": 6,
             "confirm": 7, "submit": 5, "complete": 7},
            min_score=5,
        )
        if match:
            el, score, text = match
            logging.info("Found pay button (score=%s): '%s'", score, text[:60])
            return el
        return None

    def find_quantity_input(self) -> Optional[WebElement]:
        """Find quantity input or select on the page."""
        # Direct attribute matches
        for xp in (
            "//input[contains(@name, 'qty') or contains(@name, 'quantity') or contains(@id, 'qty') or contains(@id, 'quantity')]",
            "//input[contains(@class, 'qty') or contains(@class, 'quantity')]",
            "//select[contains(@name, 'qty') or contains(@name, 'quantity') or contains(@id, 'qty') or contains(@id, 'quantity')]",
            "//input[@type='number']",
            "//*[@role='spinbutton']",
        ):
            try:
                els = self.driver.find_elements(By.XPATH, xp)
                for el in els:
                    if el.is_displayed():
                        logging.info("Found qty field (direct): tag=%s type=%s", el.tag_name, el.get_attribute("type"))
                        return el
            except WebDriverException:
                pass
        # Fallback: find inputs near text containing "qty"/"quantity"
        qty_kws = ("qty", "quantity")
        try:
            for label_xp in (
                "//*[contains(translate(normalize-space(text()), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'qty')]",
            ):
                labels = self.driver.find_elements(By.XPATH, label_xp)
                for lbl in labels:
                    if not lbl.is_displayed():
                        continue
                    # Search nearby parent for an input
                    for ancestor in ("./ancestor::div[1]", "./ancestor::div[2]", "./following-sibling::*[1]"):
                        try:
                            container = lbl.find_element(By.XPATH, ancestor)
                            inputs = container.find_elements(By.XPATH, ".//input | .//select | .//*[@role='spinbutton']")
                            for inp in inputs:
                                if inp.is_displayed():
                                    logging.info("Found qty field (near label): tag=%s", inp.tag_name)
                                    return inp
                        except WebDriverException:
                            pass
        except WebDriverException:
            pass
        return None

    def find_product_options(self) -> List[Tuple[str, List[WebElement], int]]:
        """Find product option groups (size, color, category, etc.)
        Returns: [(label, [option_elements], y_position), ...] sorted by page position.
        """
        groups: List[Tuple[str, List[WebElement], int]] = []
        # Strategy: find labels like "Size", "Category", "Color" and collect
        # sibling interactive elements
        option_labels = ("size", "category", "color", "colour", "variant", "type", "style")
        # Method 1: look for text elements that act as section headers
        for label_text in option_labels:
            found = False
            for xp in (
                f"//*[translate(normalize-space(text()), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz')='{label_text}']",
                f"//*[contains(translate(normalize-space(text()), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), '{label_text}')]",
            ):
                if found:
                    break
                try:
                    labels = self.driver.find_elements(By.XPATH, xp)
                    for lbl in labels:
                        if not lbl.is_displayed():
                            continue
                        # Find clickable options near this label
                        options = self._find_option_buttons_near(lbl)
                        if options:
                            # Get Y position of label for sorting
                            try:
                                y_pos = lbl.location["y"]
                            except WebDriverException:
                                y_pos = 9999
                            groups.append((label_text, options, y_pos))
                            found = True
                            break  # found this group
                except WebDriverException:
                    pass
            if found:
                continue
            # Also check for select dropdowns
            for xp in (
                f"//select[contains(@name, '{label_text}') or contains(@id, '{label_text}')]",
            ):
                try:
                    sels = self.driver.find_elements(By.XPATH, xp)
                    for s in sels:
                        if s.is_displayed():
                            try:
                                y_pos = s.location["y"]
                            except WebDriverException:
                                y_pos = 9999
                            groups.append((label_text, [s], y_pos))
                except WebDriverException:
                    pass
        # Sort by Y position so options are selected top-to-bottom (page order)
        groups.sort(key=lambda g: g[2])
        return groups

    def _find_option_buttons_near(self, label_el: WebElement) -> List[WebElement]:
        """Find clickable option buttons near a label element."""
        options: List[WebElement] = []
        try:
            # Look in the parent container for buttons/clickable divs
            parent = label_el.find_element(By.XPATH, "./ancestor::div[1]")
            # Try broader parent if this one is too small
            children = parent.find_elements(By.XPATH, ".//button | .//a | .//*[@role='button'] | .//input[@type='radio']/ancestor::label")
            if not children:
                parent = label_el.find_element(By.XPATH, "./ancestor::div[2]")
                children = parent.find_elements(By.XPATH, ".//button | .//a | .//*[@role='button'] | .//input[@type='radio']/ancestor::label")
            for ch in children:
                try:
                    if ch.is_displayed() and ch.is_enabled():
                        text = self._el_text(ch).lower().strip()
                        # Skip if it looks like a nav element
                        if text and len(text) < 30 and not any(n in text for n in IGNORE_KEYWORDS):
                            options.append(ch)
                except (StaleElementReferenceException, WebDriverException):
                    pass
        except (NoSuchElementException, WebDriverException):
            pass
        return options

    def find_name_fields(self) -> List[Tuple[WebElement, str]]:
        """Find all visible text inputs that look like name/attendee fields.
        Returns list of (element, field_type) where field_type is
        'first', 'last', or 'full'.
        """
        result: List[Tuple[WebElement, str]] = []
        try:
            inputs = self.driver.find_elements(By.XPATH, "//input[@type='text' or not(@type)]")
            for inp in inputs:
                if not inp.is_displayed():
                    continue
                attrs = " ".join(
                    (inp.get_attribute(a) or "").lower()
                    for a in ("placeholder", "name", "id", "aria-label")
                )
                # Check surrounding label text too
                label_text = ""
                try:
                    label = inp.find_element(By.XPATH, "./preceding-sibling::label | ./ancestor::label | ./preceding::label[1]")
                    label_text = (label.text or "").lower()
                except (NoSuchElementException, WebDriverException):
                    pass
                combined = attrs + " " + label_text
                if any(kw in combined for kw in ("name", "attendee", "guest", "first", "full name", "person", "last")):
                    # Exclude search bars, email, phone
                    if not any(exc in combined for exc in ("search", "email", "phone", "mobile", "password")):
                        # Determine if first-name, last-name, or full-name
                        if any(kw in combined for kw in ("first", "fname", "first_name", "firstname", "first name")):
                            field_type = "first"
                        elif any(kw in combined for kw in ("last", "lname", "last_name", "lastname", "last name", "surname")):
                            field_type = "last"
                        else:
                            field_type = "full"
                        result.append((inp, field_type))
        except WebDriverException:
            pass
        return result

    def find_email_fields(self) -> List[WebElement]:
        """Find visible email inputs."""
        result: List[WebElement] = []
        try:
            inputs = self.driver.find_elements(
                By.XPATH, "//input[@type='email'] | //input[contains(@name,'email') or contains(@id,'email') or contains(@placeholder,'email')]"
            )
            for inp in inputs:
                if inp.is_displayed():
                    result.append(inp)
        except WebDriverException:
            pass
        return result

    def find_phone_fields(self) -> List[WebElement]:
        """Find visible phone/mobile inputs."""
        result: List[WebElement] = []
        try:
            inputs = self.driver.find_elements(
                By.XPATH,
                "//input[@type='tel'] | //input[contains(@name,'phone') or contains(@name,'mobile') or contains(@id,'phone') or contains(@id,'mobile') or contains(@placeholder,'phone') or contains(@placeholder,'mobile')]"
            )
            for inp in inputs:
                if inp.is_displayed():
                    result.append(inp)
        except WebDriverException:
            pass
        return result

    def find_gender_radios(self) -> Dict[str, WebElement]:
        """Find gender radio buttons. Returns {'male': el, 'female': el, ...}."""
        result: Dict[str, WebElement] = {}
        try:
            # Look for radio inputs near "gender" label
            radios = self.driver.find_elements(By.XPATH,
                "//input[@type='radio']")
            for r in radios:
                if not r.is_displayed():
                    # Radios are often hidden; check the label
                    pass
                name = (r.get_attribute("name") or "").lower()
                val = (r.get_attribute("value") or "").lower()
                ident = (r.get_attribute("id") or "").lower()
                if any(kw in name for kw in ("gender", "sex")):
                    # Map by value
                    key = val or ident
                    result[key] = r
            # Also try label-based detection
            if not result:
                labels = self.driver.find_elements(By.XPATH, "//label")
                for lbl in labels:
                    text = (lbl.text or "").strip().lower()
                    if text in ("male", "female", "others", "other", "non-binary"):
                        # Check for a radio/input inside or linked via 'for'
                        for_id = lbl.get_attribute("for")
                        if for_id:
                            try:
                                inp = self.driver.find_element(By.ID, for_id)
                                result[text] = inp
                            except NoSuchElementException:
                                result[text] = lbl  # click the label itself
                        else:
                            try:
                                inp = lbl.find_element(By.XPATH, ".//input[@type='radio']")
                                result[text] = inp
                            except NoSuchElementException:
                                result[text] = lbl
        except WebDriverException:
            pass
        return result

    def find_address_fields(self) -> Dict[str, WebElement]:
        """Find shipping address form fields.
        Returns dict with keys like 'address1', 'address2', 'landmark', 'city', 'state', 'pincode'.
        """
        field_map: Dict[str, WebElement] = {}
        field_patterns = {
            "address1": ("address", "house", "building", "address line 1", "address1", "street"),
            "address2": ("locality", "area", "address line 2", "address2", "street2"),
            "landmark": ("landmark", "near", "landmark"),
            "city": ("city", "town", "district"),
            "state": ("state", "province", "region"),
            "pincode": ("pin", "zip", "postal", "pincode"),
        }
        try:
            inputs = self.driver.find_elements(By.XPATH,
                "//input[@type='text' or @type='number' or @type='tel' or not(@type)] | //select | //textarea")
            for inp in inputs:
                if not inp.is_displayed():
                    continue
                # Gather all hints about this field
                attrs = " ".join(
                    (inp.get_attribute(a) or "").lower()
                    for a in ("placeholder", "name", "id", "aria-label")
                )
                # Also check preceding label/text
                label_text = ""
                try:
                    container = inp.find_element(By.XPATH, "./ancestor::div[1]")
                    label_text = (container.text or "").lower()[:100]
                except WebDriverException:
                    pass
                combined = attrs + " " + label_text
                for field_key, patterns in field_patterns.items():
                    if field_key in field_map:
                        continue  # already found this field
                    if any(p in combined for p in patterns):
                        # Exclude fields already mapped
                        if inp not in field_map.values():
                            field_map[field_key] = inp
                            break
        except WebDriverException:
            pass
        return field_map

    # ── Ticket-specific: stand selection & quantity popup ─────────────────────

    def find_stand_buttons(self) -> "List[Tuple[str, int, WebElement]]":
        """Find visible stand rows on ticket page, sorted cheapest first.
        Returns [(stand_name_lower, price_per_ticket, element)].
        Skips stands above PRICE_PER_TICKET_MAX; caps at MAX_STAND_WORKERS.
        """
        candidate_xpaths = [
            "//table//tr[td]",
            "//ul//li[contains(@class,'category') or contains(@class,'stand') or contains(@class,'ticket')]",
            "//div[contains(@class,'category')]",
            "//div[contains(@class,'stand')]",
            "//*[contains(@class,'ticket-category')]",
            "//*[contains(@class,'ticket-row')]",
            "//td[contains(text(),'\u20b9') or contains(text(),'Rs')]/parent::tr",
            "//span[contains(text(),'\u20b9') or contains(text(),'Rs')]/ancestor::div[2]",
            # RCB site: stand rows are often direct children under a CATEGORY section
            "//*[contains(text(),'\u20b9') or contains(text(),'Rs')]/ancestor::*[self::div or self::li or self::tr][1]",
            # Clickable rows/cards with price text
            "//a[contains(text(),'\u20b9') or contains(text(),'Rs')]",
            "//button[contains(text(),'\u20b9') or contains(text(),'Rs')]",
        ]
        seen: set = set()
        raw: List[Tuple[str, int, WebElement]] = []
        for xp in candidate_xpaths:
            try:
                for el in self.driver.find_elements(By.XPATH, xp):
                    eid = id(el)
                    if eid in seen:
                        continue
                    try:
                        if not el.is_displayed():
                            continue
                    except (StaleElementReferenceException, WebDriverException):
                        continue
                    text = (el.text or "").strip()
                    if not text or len(text) > 300:
                        continue
                    price = self._extract_price_from_text(text)
                    has_stand_kw = any(kw in text.lower() for kw in (
                        # Physical / structural stand terms — sponsor-agnostic
                        # NOTE: "category" and "section" removed — too generic,
                        # they match e-commerce pages and cause false positives.
                        "stand", "corporate", "lounge", "pavilion", "terrace",
                        "executive", "upper", "lower", "enclosure", "annexe",
                        "platinum", "gallery", "block", "tier", "level",
                    ))
                    if price is None and not has_stand_kw:
                        continue
                    if price is None:
                        price = 0
                    seen.add(eid)
                    raw.append((text.lower(), price, el))
            except WebDriverException:
                pass

        # Keep first MAX_STAND_WORKERS entries within price cap (page order)
        result: List[Tuple[str, int, WebElement]] = []
        seen_els: set = set()
        for name, price, el in raw:
            eid = id(el)
            if eid in seen_els:
                continue
            seen_els.add(eid)
            if price > 0 and price > PRICE_PER_TICKET_MAX:
                logging.info("  Skipping stand (price %s > max %s): %s", price, PRICE_PER_TICKET_MAX, name[:60])
                continue
            if len(result) >= MAX_STAND_WORKERS:
                break
            result.append((name, price, el))

        # Sort cheapest first (unknowns/price=0 go last)
        result.sort(key=lambda x: x[1] if x[1] > 0 else 99999)
        return result

    @staticmethod
    def _extract_price_from_text(text: str) -> "Optional[int]":
        """Extract lowest rupee price found in a text block."""
        matches = re.findall(r"(?:Rs\.?\s*)([\d,]+)", text, re.IGNORECASE)
        matches += re.findall("\u20b9" + r"\s*([\d,]+)", text)
        prices = [int(m.replace(",", "")) for m in matches if m.replace(",","").isdigit()]
        return min(prices) if prices else None

    def find_ticket_quantity_popup(self) -> Optional[WebElement]:
        """Detect the 'How many tickets?' popup shown after clicking a stand."""
        popup_xpaths = [
            "//*[contains(translate(normalize-space(.),'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'how many ticket')]",
            "//*[contains(translate(normalize-space(.),'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'how many')]",
            "//div[contains(@class,'modal') and contains(@class,'show')]",
            "//div[contains(@class,'popup') and not(contains(@class,'hide'))]",
            "//div[contains(@class,'dialog')]",
            "//*[@role='dialog']",
        ]
        for xp in popup_xpaths:
            try:
                for el in self.driver.find_elements(By.XPATH, xp):
                    if el.is_displayed():
                        text = (el.text or "").lower()
                        if "ticket" in text or "how many" in text or "continue" in text:
                            logging.info("Ticket qty popup detected: '%s'", text[:80])
                            return el
            except WebDriverException:
                pass
        return None

    def find_quantity_buttons_in_popup(self, popup: WebElement) -> List[Tuple[int, WebElement]]:
        """Find the numbered buttons (1–6) inside the ticket quantity popup.
        Returns [(number, element), ...] sorted ascending.
        """
        results: List[Tuple[int, WebElement]] = []
        try:
            # Buttons that are just a digit (1–6)
            btns = popup.find_elements(By.XPATH,
                ".//button | .//*[@role='button'] | .//div[contains(@class,'qty')] | .//span[contains(@class,'qty')]")
            # Also try plain divs/spans that contain only a digit
            btns += popup.find_elements(By.XPATH,
                ".//*[string-length(normalize-space(text()))=1 and number(normalize-space(text())) = number(normalize-space(text()))]")
            seen: set = set()
            for b in btns:
                bid = id(b)
                if bid in seen:
                    continue
                seen.add(bid)
                try:
                    if not b.is_displayed():
                        continue
                    t = (b.text or "").strip()
                    if t.isdigit() and 1 <= int(t) <= 6:
                        results.append((int(t), b))
                except (StaleElementReferenceException, WebDriverException):
                    pass
        except WebDriverException:
            pass
        results.sort(key=lambda x: x[0])
        return results

    def find_popup_continue_button(self, popup: WebElement) -> Optional[WebElement]:
        """Find the 'Continue' button inside the ticket quantity popup."""
        for xp in (
            ".//button[contains(translate(normalize-space(.),'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'continue')]",
            ".//button[contains(translate(normalize-space(.),'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'proceed')]",
            ".//button[contains(translate(normalize-space(.),'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'next')]",
            ".//button[@type='submit']",
        ):
            try:
                for el in popup.find_elements(By.XPATH, xp):
                    if el.is_displayed() and el.is_enabled():
                        return el
            except WebDriverException:
                pass
        return None

    # ── Page stage classifier ─────────────────────────────────────────────────

    def classify_page(self, url: str = "") -> "PageStage":
        """Determine where in the RCB ticket flow we currently are.

        Uses a hierarchy of signals: URL patterns, visible structural elements,
        page text — NOT button label strings.
        """
        try:
            body = self.driver.find_element(By.TAG_NAME, "body")
            body_text = (body.text or "").lower()
            url = url or self.driver.current_url.lower()
        except WebDriverException:
            return PageStage.UNKNOWN

        # ── Failure / wall states ─────────────────────────────────────────
        if any(s in body_text for s in ("503 service", "502 bad", "504 gateway",
                                         "too many requests", "server error")):
            return PageStage.ERROR_PAGE

        if any(s in body_text for s in ("sold out", "no tickets available",
                                         "tickets not available")):
            return PageStage.SOLD_OUT

        # ── Payment gateway ───────────────────────────────────────────────
        # Usually a redirect to juspay / razorpay / paytm
        payment_domains = ("juspay", "razorpay", "paytm.com", "cashfree",
                            "ccavenue", "billdesk", "hdfc", "axis")
        if any(d in url for d in payment_domains):
            return PageStage.PAYMENT
        if any(s in body_text for s in ("enter upi", "upi id", "net banking",
                                         "credit card", "debit card",
                                         "pay securely", "payment method")):
            return PageStage.PAYMENT

        # ── Checkout / cart ───────────────────────────────────────────────
        if any(s in url for s in ("/cart", "/checkout", "/bag", "/order")):
            return PageStage.CHECKOUT
        if any(s in body_text for s in ("order summary", "subtotal", "place order",
                                         "billing address", "your cart", "your bag")):
            return PageStage.CHECKOUT

        # ── Seat map ──────────────────────────────────────────────────────
        if self.has_seat_map():
            return PageStage.SEAT_MAP

        # ── Qty popup ─────────────────────────────────────────────────────
        if self.find_ticket_quantity_popup():
            return PageStage.QTY_POPUP

        # ── Stand list ────────────────────────────────────────────────────
        # Stand list = multiple rows each with a rupee price visible
        # BUT skip if URL is a merchandise product page (merch has prices too)
        import re as _re
        is_merch_url = (
            "/merchandise" in url or "/product" in url
            # The shop.* subdomain is the merchandise store; only treat as
            # ticket page if the URL explicitly contains a ticket path.
            or ("shop." in url and "/ticket" not in url)
        )
        is_ticket_url = "/ticket" in url
        if not is_merch_url:
            price_hits = _re.findall(r"[₹][\s]*[\d,]{3,}", body_text)
            # Also count "Rs NNNN" style prices common on RCB site
            price_hits += _re.findall(r"rs\.?\s*[\d,]{3,}", body_text)
            stand_kws = sum(1 for kw in (
                # Physical / structural terms — no sponsor names
                # NOTE: "category" and "section" removed — too generic,
                # they match e-commerce pages and cause false positives.
                "stand", "enclosure", "corporate", "lounge", "pavilion",
                "terrace", "executive", "upper", "lower",
                "platinum", "gallery", "block", "tier", "level",
                "annexe",
            ) if kw in body_text)
            if is_ticket_url and stand_kws >= 1:
                return PageStage.STAND_LIST
            if len(price_hits) >= 2 and stand_kws >= 1:
                return PageStage.STAND_LIST
            if len(price_hits) >= 3:
                # Multiple prices without clear stand labels — still likely stand list
                return PageStage.STAND_LIST

        # ── Match list / match detail ─────────────────────────────────────
        # Match list: multiple team names + dates visible
        ipl_teams = ["rcb", " mi ", "csk", "kkr", " dc ", "srh", " rr ", "lsg",
                     "pbks", " gt ", "bangalore", "mumbai", "chennai", "kolkata",
                     "hyderabad", "delhi", "rajasthan", "lucknow", "punjab",
                     "gujarat", "sunrisers"]
        team_hits = sum(1 for t in ipl_teams if t in f" {body_text} ")
        date_hits = bool(_re.search(r"(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)", body_text))

        if team_hits >= 2 and date_hits:
            # Is there a prominent ticket/buy CTA? → MATCH_DETAIL
            # Multiple such CTAs? → MATCH_LIST
            ctas = self.find_all_primary_ctas()
            if len(ctas) >= 2:
                return PageStage.MATCH_LIST
            elif len(ctas) == 1:
                return PageStage.MATCH_DETAIL

        # ── Tickets nav ───────────────────────────────────────────────────
        # Landing page where "Tickets" is a section/tab to click into
        if "ticket" in body_text and ("fixture" in body_text or "schedule" in body_text
                                       or "match" in body_text):
            return PageStage.TICKETS_NAV

        # ── Home / generic ────────────────────────────────────────────────
        # Merchandise pages are NOT home — avoid looping with CTA clicks
        if (("royalchallengers" in url or "rcb" in url)
                and "/merchandise" not in url and "/product" not in url):
            return PageStage.HOME

        return PageStage.UNKNOWN

    def find_primary_cta(self) -> "Optional[WebElement]":
        """Find the single most prominent actionable element on the page.

        Uses visual prominence signals (size, position, colour keywords in class)
        rather than button text. Falls back to keyword scoring but with a wider net.
        """
        candidates: "List[Tuple[int, WebElement]]" = []
        interactive_xpaths = [
            "//button",
            "//a[not(contains(@href,'#')) and not(contains(@href,'javascript'))]",
            "//*[@role='button']",
            "//input[@type='submit' or @type='button']",
        ]
        els: "List[WebElement]" = []
        seen: set = set()
        for xp in interactive_xpaths:
            try:
                for el in self.driver.find_elements(By.XPATH, xp):
                    if id(el) not in seen:
                        seen.add(id(el))
                        els.append(el)
            except WebDriverException:
                pass

        for el in els:
            try:
                if not el.is_displayed() or not el.is_enabled():
                    continue
            except (StaleElementReferenceException, WebDriverException):
                continue

            score = 0
            text = self._el_text(el).lower().strip()
            cls  = (el.get_attribute("class") or "").lower()
            href = (el.get_attribute("href") or "").lower()

            if not text and not href:
                continue

            # Skip links to external domains (social media icons, etc.)
            if href and el.tag_name.lower() == "a":
                href_domain = urllib.parse.urlparse(href).netloc.lower()
                target_domain = urllib.parse.urlparse(self.driver.current_url).netloc.lower()
                if href_domain and target_domain and href_domain != target_domain:
                    continue

            # Skip nav / footer / social noise
            if any(n in text for n in IGNORE_KEYWORDS) and len(text) < 25:
                continue
            if any(n in text for n in NEGATIVE_KEYWORDS):
                continue

            # Visual prominence: primary/CTA class names
            if any(c in cls for c in ("primary", "cta", "btn-main", "action",
                                       "highlight", "featured", "hero")):
                score += 15
            if any(c in cls for c in ("btn", "button")):
                score += 5

            # Position bonus: elements in the upper-center of viewport score higher
            try:
                loc = el.location
                sz  = el.size
                # Avoid tiny elements (icons, badges)
                if sz["width"] < 40 or sz["height"] < 25:
                    score -= 5
                # Boost larger buttons
                area = sz["width"] * sz["height"]
                if area > 5000:
                    score += 8
                elif area > 2000:
                    score += 4
                # Penalise elements deep in page (likely footer)
                if loc["y"] > 2000:
                    score -= 10
            except WebDriverException:
                pass

            # Semantic signals — intentionally broad, not exact strings
            FORWARD_SIGNALS = (
                "ticket", "book", "buy", "shop", "purchase", "get",
                "select", "choose", "proceed", "continue", "next",
                "view", "see", "check", "explore", "available",
            )
            matched_signals = sum(1 for s in FORWARD_SIGNALS if s in text or s in href)
            score += matched_signals * 3

            # URL hints
            TICKET_URL_SIGNALS = ("ticket", "book", "fixture", "match", "ipl", "schedule")
            if any(s in href for s in TICKET_URL_SIGNALS):
                score += 12

            if score > 0:
                candidates.append((score, el))

        if not candidates:
            return None
        candidates.sort(key=lambda x: x[0], reverse=True)
        best_score, best_el = candidates[0]
        logging.info("Primary CTA (score=%s): '%s'", best_score, self._el_text(best_el)[:80])
        return best_el

    def find_all_primary_ctas(self) -> "List[WebElement]":
        """Return all prominent CTA elements (used to distinguish match-list vs match-detail)."""
        els: "List[WebElement]" = []
        candidates: "List[Tuple[int, WebElement]]" = []
        TICKET_URL_SIGNALS = ("ticket", "book", "fixture", "match", "ipl")
        FORWARD_SIGNALS = ("ticket", "book", "buy", "purchase", "get ticket",
                           "select", "proceed")
        interactive_xpaths = ["//button", "//a", "//*[@role='button']"]
        seen: set = set()
        for xp in interactive_xpaths:
            try:
                for el in self.driver.find_elements(By.XPATH, xp):
                    if id(el) not in seen:
                        seen.add(id(el))
                        els.append(el)
            except WebDriverException:
                pass

        for el in els:
            try:
                if not el.is_displayed() or not el.is_enabled():
                    continue
                text = self._el_text(el).lower()
                href = (el.get_attribute("href") or "").lower()
                cls  = (el.get_attribute("class") or "").lower()
                if any(n in text for n in IGNORE_KEYWORDS | NEGATIVE_KEYWORDS):
                    continue
                # Skip links to external domains (social media icons, etc.)
                if href and el.tag_name.lower() == "a":
                    href_domain = urllib.parse.urlparse(href).netloc.lower()
                    current_domain = urllib.parse.urlparse(self.driver.current_url).netloc.lower()
                    if href_domain and current_domain and href_domain != current_domain:
                        continue
                score = 0
                if any(s in text for s in FORWARD_SIGNALS):
                    score += 10
                if any(s in href for s in TICKET_URL_SIGNALS):
                    score += 8
                if any(c in cls for c in ("primary", "cta", "btn")):
                    score += 5
                if score >= 8:
                    candidates.append((score, el))
            except (StaleElementReferenceException, WebDriverException):
                pass

        candidates.sort(key=lambda x: x[0], reverse=True)
        # Deduplicate by text to avoid counting the same "Buy Tickets" 10 times
        seen_text: set = set()
        result: "List[WebElement]" = []
        for _, el in candidates:
            t = self._el_text(el).lower().strip()
            if t not in seen_text:
                seen_text.add(t)
                result.append(el)
        return result

    
    def has_seat_map(self) -> bool:
        """Detect if page has an interactive seat/venue map."""
        map_signals = (
            "//*[contains(@class, 'seat') and contains(@class, 'map')]",
            "//*[contains(@class, 'venue') and contains(@class, 'map')]",
            "//*[contains(@class, 'stadium')]",
            "//*[contains(@id, 'seatmap') or contains(@id, 'seat-map') or contains(@id, 'venueMap')]",
            "//svg[contains(@class, 'seat') or contains(@class, 'map') or contains(@class, 'venue')]",
            "//canvas",
            "//iframe[contains(@src, 'seat') or contains(@src, 'map') or contains(@src, 'venue')]",
        )
        for xp in map_signals:
            try:
                els = self.driver.find_elements(By.XPATH, xp)
                for el in els:
                    if el.is_displayed():
                        return True
            except WebDriverException:
                pass
        return False

    def find_available_seats(self) -> List[WebElement]:
        """Find clickable available seat elements."""
        xpaths = [
            "//*[contains(@class, 'available') and (contains(@class, 'seat') or contains(@class, 'section') or contains(@class, 'zone'))]",
            "//*[@data-status='available' or @data-available='true' or @data-state='available']",
            "//*[contains(@class, 'bookable') or contains(@class, 'selectable')]",
            "//*[contains(@class, 'seat') and not(contains(@class, 'unavailable')) and not(contains(@class, 'sold')) and not(contains(@class, 'blocked')) and not(contains(@class, 'disabled'))]",
        ]
        result: List[WebElement] = []
        seen: set = set()
        for xp in xpaths:
            try:
                for el in self.driver.find_elements(By.XPATH, xp):
                    eid = id(el)
                    if eid not in seen and el.is_displayed():
                        seen.add(eid)
                        result.append(el)
            except WebDriverException:
                pass
        return result

    def page_has_text(self, keywords: Sequence[str]) -> bool:
        """Check if page body contains any of the given keywords."""
        try:
            body = self.driver.find_element(By.TAG_NAME, "body").text.lower()
            return any(kw in body for kw in keywords)
        except WebDriverException:
            return False

    def log_page_summary(self) -> None:
        """Log what interactive elements are on the page (for debugging)."""
        elements = self._get_all_interactive()
        buttons = []
        for el in elements[:30]:  # limit to 30
            text = self._el_text(el).strip()
            if text and len(text) < 60:
                buttons.append(f"{el.tag_name}:'{text}'")
        logging.info("Page elements (%s visible): %s", len(elements), " | ".join(buttons[:15]))


# ══════════════════════════════════════════════════════════════════════════════
# MONITOR — uses PageAnalyzer for all detection
# ══════════════════════════════════════════════════════════════════════════════


# ══════════════════════════════════════════════════════════════════════════════
# PAGE STAGE CLASSIFIER
# Understands where in the RCB ticket flow we are without relying on
# hardcoded button-text strings.
# ══════════════════════════════════════════════════════════════════════════════

from enum import Enum, auto

class PageStage(Enum):
    UNKNOWN         = auto()  # can't determine
    HOME            = auto()  # shop/fixtures landing page
    TICKETS_NAV     = auto()  # a "Tickets" section/tab is visible but not entered
    MATCH_LIST      = auto()  # list of upcoming matches with buy/book CTAs
    MATCH_DETAIL    = auto()  # single match detail page — stands not visible yet
    STAND_LIST      = auto()  # stand/category list is visible
    QTY_POPUP       = auto()  # "how many tickets" popup is open
    SEAT_MAP        = auto()  # venue seat map is visible
    CHECKOUT        = auto()  # cart / checkout form
    PAYMENT         = auto()  # payment gateway

    # Failure states
    SOLD_OUT        = auto()
    LOGIN_WALL      = auto()
    ERROR_PAGE      = auto()


class WebsiteMonitor:
    """Monitors a page for availability & drives through checkout dynamically."""

    def __init__(self, config: Config, profile_dir: Optional[str] = None) -> None:
        self.config = config
        self._profile_dir = profile_dir or CHROME_PROFILE_DIR
        self.driver: Optional[webdriver.Chrome] = None
        self.wait: Optional[WebDriverWait] = None
        self.short_wait: Optional[WebDriverWait] = None
        self.page: Optional[PageAnalyzer] = None
        # Multi-match tracking: hrefs of matches we already booked
        self._booked_matches: set = set()
        self._current_match_id: Optional[str] = None
        self._is_stealth: bool = False
        self.config.screenshot_dir.mkdir(parents=True, exist_ok=True)
        self._setup_driver()

    # ── Driver ────────────────────────────────────────────────────────────────

    @staticmethod
    def _kill_stale_chrome(profile_path: Path) -> None:
        """Kill any Chrome processes still using *this* profile directory."""
        if os.name != "nt":
            return
        import subprocess
        profile_str = str(profile_path).lower()
        try:
            # Use PowerShell Get-CimInstance (works on all modern Windows)
            ps_cmd = (
                "Get-CimInstance Win32_Process -Filter \"Name='chrome.exe'\" "
                "| Select-Object ProcessId,CommandLine "
                "| ForEach-Object { $_.ProcessId.ToString() + '|' + $_.CommandLine }"
            )
            result = subprocess.run(
                ["powershell", "-NoProfile", "-Command", ps_cmd],
                capture_output=True, text=True, timeout=15,
            )
            for line in result.stdout.splitlines():
                line = line.strip()
                if "|" not in line:
                    continue
                pid_str, cmd = line.split("|", 1)
                if profile_str in cmd.lower():
                    pid = int(pid_str)
                    logging.info("Killing stale Chrome PID %s", pid)
                    subprocess.run(["taskkill", "/F", "/PID", str(pid)],
                                   capture_output=True, timeout=5)
            time.sleep(1)
        except Exception as exc:
            logging.warning("Could not clean up stale Chrome: %s", exc)

    def _setup_driver(self) -> None:
        options = webdriver.ChromeOptions()
        options.add_argument("--start-maximized")
        options.add_argument("--disable-gpu")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--disable-notifications")
        options.add_argument("--disable-notifications")

        profile_path = Path(self._profile_dir).resolve()
        profile_path.mkdir(parents=True, exist_ok=True)
        # Kill any Chrome processes still holding this profile from a previous run
        self._kill_stale_chrome(profile_path)
        for lock in ("SingletonLock", "SingletonSocket", "SingletonCookie"):
            lf = profile_path / lock
            if lf.exists():
                try:
                    lf.unlink()
                except OSError:
                    pass
        options.add_argument(f"--user-data-dir={profile_path}")
        options.add_argument("--profile-directory=RCBMonitor")
        logging.info("Chrome profile: %s", profile_path)

        self.driver = webdriver.Chrome(options=options)
        self.driver.set_page_load_timeout(45)
        self.wait = WebDriverWait(self.driver, 15)
        self.short_wait = WebDriverWait(self.driver, 5)
        self.page = PageAnalyzer(self.driver)
        logging.info("Chrome driver initialized")

    def _setup_stealth_driver(self) -> None:
        """Initialize a StealthDriverAdapter (Scrapling + Camoufox) instead of
        Selenium.  Falls back to normal _setup_driver() if anything goes wrong.
        """
        try:
            adapter = StealthDriverAdapter()
            self.driver = adapter          # duck-typed Selenium driver
            self.wait = None               # not applicable for Playwright
            self.short_wait = None
            self.page = PageAnalyzer(self.driver)
            self._is_stealth = True
            logging.info("Stealth driver (Scrapling/Camoufox) initialized")
        except Exception as exc:
            logging.warning("Stealth driver setup failed (%s) — falling back to Selenium", exc)
            self._is_stealth = False
            self._setup_driver()

    # ── Session health ────────────────────────────────────────────────────────

    def _is_session_alive(self) -> bool:
        """Quick check — can we still talk to the browser?"""
        try:
            _ = self.driver.current_url        # lightweight WebDriver call
            return True
        except Exception:
            return False

    def _restart_driver(self) -> None:
        """Tear down the dead session and spin up a fresh one."""
        logging.warning("Session dead — restarting Chrome driver …")
        try:
            if self.driver:
                self.driver.quit()
        except Exception:
            pass
        self.driver = None
        self.wait = None
        self.short_wait = None
        self.page = None
        time.sleep(2)                          # brief cooldown
        self._setup_driver()
        logging.info("Driver restarted — navigating to target …")
        self.driver.get(self.config.target_url)
        self._wait_ready_robust()
        if self._is_login_page():
            self._handle_login_wall()

    def _ensure_session(self) -> None:
        """If the session is dead, restart automatically."""
        if not self._is_session_alive():
            self._restart_driver()

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _screenshot(self, label: str) -> Path:
        assert self.driver
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        path = self.config.screenshot_dir / f"{ts}-{label}.png"
        try:
            self.driver.save_screenshot(str(path))
            logging.info("Screenshot: %s", path)
        except WebDriverException:
            logging.warning("Screenshot failed (session dead?) — will auto-restart: %s", path)
        return path

    def _cleanup_old_screenshots(self, cycle: int) -> None:
        """Delete screenshots older than the last SCREENSHOT_KEEP_CYCLES cycles."""
        try:
            files = sorted(
                self.config.screenshot_dir.glob("*.png"),
                key=lambda f: f.stat().st_mtime,
            )
            # Keep screenshots from the last N cycles.  Each cycle produces ~1-3
            # screenshots; keep a generous buffer so we don't lose useful ones.
            max_keep = SCREENSHOT_KEEP_CYCLES * 5
            to_delete = files[:-max_keep] if len(files) > max_keep else []
            for f in to_delete:
                f.unlink(missing_ok=True)
            if to_delete:
                logging.info("Cleaned up %d old screenshots (kept latest %d)", len(to_delete), max_keep)
        except Exception as exc:
            logging.warning("Screenshot cleanup failed: %s", exc)

    @staticmethod
    def _cleanup_chrome_cache(profile_dir: str) -> None:
        """Purge expendable Chrome cache/crash data to reclaim disk space."""
        expendable = (
            "ShaderCache", "GrShaderCache", "GraphiteDawnCache",
            "BrowserMetrics", "BrowserMetrics-spare.pma",
            "Crashpad/reports",
        )
        import shutil
        cleaned = 0
        for name in expendable:
            target = Path(profile_dir) / name
            if target.is_dir():
                shutil.rmtree(target, ignore_errors=True)
                cleaned += 1
            elif target.is_file():
                try:
                    target.unlink()
                    cleaned += 1
                except OSError:
                    pass
        if cleaned:
            logging.info("Cleaned %d Chrome cache entries in %s", cleaned, profile_dir)

    def _wait_ready(self) -> None:
        assert self.driver and self.wait
        self.wait.until(lambda d: d.execute_script("return document.readyState") == "complete")
        self.wait.until(EC.presence_of_element_located((By.TAG_NAME, "body")))

    def _wait_ready_robust(self, retries: int = 5, base_delay: float = 1.5) -> None:
        """Retry-aware page-ready wait for high-load scenarios (busy RCB site)."""
        assert self.driver
        # Stealth adapter (Playwright-based) — simple wait + reload page_source
        if getattr(self, '_is_stealth', False):
            for attempt in range(retries):
                try:
                    time.sleep(2)
                    self.driver.page_source = self.driver._page.content() if self.driver._page else ""
                    return
                except Exception as exc:
                    if attempt < retries - 1:
                        time.sleep(base_delay * (2 ** attempt))
            return
        for attempt in range(retries):
            try:
                WebDriverWait(self.driver, 20).until(
                    lambda d: d.execute_script("return document.readyState") == "complete"
                )
                WebDriverWait(self.driver, 10).until(
                    EC.presence_of_element_located((By.TAG_NAME, "body"))
                )
                # Check for generic error/server-busy pages
                try:
                    body = self.driver.find_element(By.TAG_NAME, "body").text.lower()
                    if any(s in body for s in ("503", "502", "504", "too many requests",
                                                "server error", "try again", "please wait")):
                        raise WebDriverException("Server busy page detected")
                except WebDriverException as e:
                    if "busy" in str(e) or "503" in str(e):
                        raise
                return  # success
            except (TimeoutException, WebDriverException) as exc:
                wait_time = base_delay * (2 ** attempt)
                logging.warning("Page load attempt %s/%s failed (%s) — waiting %.1fs",
                                attempt + 1, retries, exc, wait_time)
                if attempt < retries - 1:
                    time.sleep(wait_time)
                    try:
                        self.driver.refresh()
                    except WebDriverException:
                        pass
        logging.error("Page did not load after %s retries", retries)

    def _click(self, el: WebElement) -> None:
        assert self.driver
        try:
            el.click()
        except WebDriverException:
            self.driver.execute_script("arguments[0].click();", el)

    def _scroll_to(self, el: WebElement) -> None:
        assert self.driver
        self.driver.execute_script(
            "arguments[0].scrollIntoView({behavior:'smooth',block:'center'});", el
        )
        time.sleep(0.4)

    @staticmethod
    def _safe_text(el: WebElement) -> str:
        return (el.text or el.get_attribute("textContent") or "").strip()

    @staticmethod
    def _extract_price(text: str) -> Optional[int]:
        m = re.search(r"(?:₹|Rs\.?\s?)([\d,]+)", text)
        return int(m.group(1).replace(",", "")) if m else None

    def _find_clickable_xpath(
        self, xpaths: Sequence[str], wait: Optional[WebDriverWait] = None
    ) -> Optional[WebElement]:
        assert self.driver
        w = wait or self.short_wait
        assert w
        for xp in xpaths:
            try:
                el = w.until(EC.element_to_be_clickable((By.XPATH, xp)))
                if el.is_displayed() and el.is_enabled():
                    return el
            except (TimeoutException, StaleElementReferenceException):
                pass
            except WebDriverException:
                pass
        return None

    def _find_element_xpath(
        self, xpaths: Sequence[str], wait: Optional[WebDriverWait] = None
    ) -> Optional[WebElement]:
        assert self.driver
        w = wait or self.short_wait
        assert w
        for xp in xpaths:
            try:
                return w.until(EC.presence_of_element_located((By.XPATH, xp)))
            except (TimeoutException, StaleElementReferenceException):
                pass
        return None

    # ── Login ─────────────────────────────────────────────────────────────────

    def _is_login_page(self) -> bool:
        """Only returns True for RCB-site login walls, not unrelated login pages."""
        assert self.driver
        url = self.driver.current_url.lower()
        # Must be on the RCB domain (or its auth subdomain) to count as a login wall
        rcb_domains = ("royalchallengers.com", "rcb.com")
        if not any(d in url for d in rcb_domains):
            return False
        if "/auth" in url and "callbackurl" in url:
            return True
        if any(seg in url for seg in ("/login", "/signin", "/auth?", "/auth/")):
            return True
        try:
            body = self.driver.find_element(By.TAG_NAME, "body").text.lower()
            if ("enter mobile no" in body or "enter mobile" in body) and "otp" not in body:
                phone_input = self._find_element_xpath(LOGIN_MOBILE_INPUT_XPATHS)
                if phone_input:
                    return True
        except WebDriverException:
            pass
        return False

    def _handle_login_wall(self) -> bool:
        if not self._is_login_page():
            return True
        if not CONTACT_PHONE:
            logging.error("Login wall detected but CONTACT_PHONE not set!")
            self._screenshot("login-no-phone")
            self._play_short_alert()
            print("\n" + "!" * 60)
            print("  Login required! Set CONTACT_PHONE in .env and restart.")
            print("!" * 60 + "\n")
            return False

        logging.info("Login wall detected — entering phone: %s", CONTACT_PHONE)
        self._screenshot("login-page")

        phone_input = self._find_element_xpath(LOGIN_MOBILE_INPUT_XPATHS)
        if not phone_input:
            logging.error("Could not find mobile input on login page")
            return False

        self._scroll_to(phone_input)
        phone_input.clear()
        phone_input.send_keys(CONTACT_PHONE)
        time.sleep(0.5)

        continue_btn = self._find_clickable_xpath(LOGIN_CONTINUE_XPATHS, wait=self.short_wait)
        if continue_btn:
            self._click(continue_btn)
            time.sleep(3)
            self._wait_ready()
        else:
            phone_input.send_keys(Keys.RETURN)
            time.sleep(3)
            self._wait_ready()

        self._screenshot("login-otp-sent")
        self._play_short_alert()
        logging.warning("OTP sent to %s — waiting for manual entry (%ss)", CONTACT_PHONE, OTP_TIMEOUT)

        if ENABLE_NOTIFICATIONS:
            try:
                notification.notify(
                    title="RCB Monitor — Enter OTP!",
                    message=f"OTP sent to {CONTACT_PHONE}. Enter it in the browser!",
                    app_name="RCB Monitor", timeout=OTP_TIMEOUT,
                )
            except Exception:
                pass

        print("\n" + "!" * 60)
        print(f"  OTP sent to {CONTACT_PHONE}!")
        print("  Enter OTP in the browser, then press ENTER here.")
        print(f"  (Auto-continues in {OTP_TIMEOUT}s)")
        print("!" * 60 + "\n")

        done = threading.Event()
        def _wi():
            try:
                input()
            except EOFError:
                pass
            done.set()
        threading.Thread(target=_wi, daemon=True).start()
        done.wait(timeout=OTP_TIMEOUT)

        self._screenshot("login-after-otp")
        logging.info("Waiting for redirect …")
        for _ in range(10):
            time.sleep(1)
            if not self._is_login_page():
                break

        if self._is_login_page():
            verify_btn = self._find_clickable_xpath(LOGIN_VERIFY_XPATHS, wait=self.short_wait)
            if verify_btn:
                label = self._safe_text(verify_btn).lower()
                if any(w in label for w in ("verify", "submit", "login")):
                    self._click(verify_btn)
                    time.sleep(3)
                    self._wait_ready()

        for _ in range(5):
            time.sleep(1)
            if not self._is_login_page():
                break

        if self._is_login_page():
            logging.warning("Still on login page — complete manually")
            self._play_short_alert()
            print("\n!!! Complete login manually in the browser, then press ENTER !!!\n")
            input()
            if self._is_login_page():
                return False

        logging.info("Login successful — now on: %s", self.driver.current_url)
        self._screenshot("login-success")
        return True

    # ── CAPTCHA ───────────────────────────────────────────────────────────────

    def _handle_captcha(self) -> None:
        assert self.driver and self.page
        if self.page.page_has_text(self.config.captcha_markers):
            logging.warning("CAPTCHA detected — pausing 30s")
            self._screenshot("captcha")
            time.sleep(30)
            self._wait_ready()

    # ── Ticket page discovery ─────────────────────────────────────────────────

    def _find_ticket_page(self) -> bool:
        """Scan the current page for ticket/booking links and navigate to the
        most relevant one. Returns True if we navigated away to a better page.
        Only active in live-tickets mode.
        """
        assert self.driver and self.page
        if self.config.mode != "live-tickets":
            return False

        current_url = self.driver.current_url
        logging.info("Scanning for ticket links on: %s", current_url)

        # Collect all anchor tags with hrefs
        try:
            anchors = self.driver.find_elements(By.TAG_NAME, "a")
        except WebDriverException:
            return False

        scored: List[Tuple[int, str, str]] = []  # (score, href, text)
        target_domain = urllib.parse.urlparse(self.config.target_url).netloc.lower()
        for a in anchors:
            try:
                href = (a.get_attribute("href") or "").strip()
                text = (a.text or a.get_attribute("aria-label") or "").strip().lower()
                if not href or href.startswith("javascript"):
                    continue

                # Only follow links on our own domain — tickets are sold here only
                href_domain = urllib.parse.urlparse(href).netloc.lower()
                if not (target_domain and target_domain in href_domain):
                    continue

                score = 0
                href_lower = href.lower()

                # Score by link keywords in href
                for kw in TICKET_LINK_KEYWORDS:
                    if kw in href_lower:
                        score += 5

                # Score by visible link text
                for kw in ("ticket", "book ticket", "buy ticket", "book now", "get tickets"):
                    if kw in text:
                        score += 8

                if score > 0:
                    scored.append((score, href, text))
            except (StaleElementReferenceException, WebDriverException):
                continue

        if not scored:
            logging.info("No ticket links found on current page")
            return False

        # Sort by score descending
        scored.sort(key=lambda x: x[0], reverse=True)
        best_score, best_href, best_text = scored[0]
        logging.info("Top ticket link candidates:")
        for s, h, t in scored[:5]:
            logging.info("  score=%-3s text='%-40s' href=%s", s, t[:40], h[:80])

        if best_href != current_url:
            logging.info("Navigating to ticket page (score=%s): %s", best_score, best_href)
            self.driver.get(best_href)
            self._wait_ready()
            self._screenshot("ticket-page-navigated")
            return True

        return False

    # ── Ticket stand selection ────────────────────────────────────────────────

    def _select_stand(self, stand_index: int = 0) -> bool:
        """Click the stand at `stand_index` from the prioritised stand list.

        Stand ordering:
          1. Stands matching PREFERRED_STANDS entries (in user-specified order)
          2. Remaining stands sorted cheapest-first

        Called by each parallel worker with its own assigned index.
        Returns True if clicked successfully.
        """
        assert self.page and self.driver
        # Robust retry — page may still be loading stand list
        stands: "List[Tuple[str, int, WebElement]]" = []
        for attempt in range(6):
            stands = self.page.find_stand_buttons()
            if stands:
                break
            logging.info("  Stand list empty (attempt %s/6) — retrying in 1s", attempt + 1)
            time.sleep(1)
            try:
                self.driver.refresh()
                self._wait_ready_robust()
            except WebDriverException:
                pass

        if not stands:
            logging.warning("[stand-%s] No stand rows found after retries", stand_index)
            return False

        # Re-order stands: PREFERRED_STANDS first (in configured priority),
        # then the remaining cheapest-first
        if PREFERRED_STANDS:
            preferred: "List[Tuple[str, int, WebElement]]" = []
            remaining: "List[Tuple[str, int, WebElement]]" = list(stands)
            for pref in PREFERRED_STANDS:
                for s in remaining[:]:
                    if pref in s[0]:  # s[0] is already lowercased
                        preferred.append(s)
                        remaining.remove(s)
                        break  # one match per preference entry
            stands = preferred + remaining
            if preferred:
                logging.info("[stand-%s] Preferred stands matched: %s",
                             stand_index, [n[:40] for n, _, _ in preferred])

        logging.info("[stand-%s] Available stands (priority order):", stand_index)
        for i, (name, price, _) in enumerate(stands):
            logging.info("  [%s] ₹%s  %s", i, price, name[:80])

        if stand_index >= len(stands):
            logging.warning("[stand-%s] Index out of range (only %s stands)", stand_index, len(stands))
            return False

        name, price, el = stands[stand_index]
        logging.info("[stand-%s] Targeting: ₹%s  %s", stand_index, price, name[:80])
        self._scroll_to(el)
        self._click(el)
        self._screenshot(f"stand-{stand_index}-clicked")
        return True

    def _handle_ticket_quantity_popup(self) -> bool:
        """Handle the 'How many tickets?' popup — selects TICKET_QUANTITY (1–6)
        and clicks Continue. Returns True if handled.
        """
        assert self.page and self.driver
        logging.info("Waiting for ticket quantity popup …")

        # Give the popup up to 6 seconds to appear
        popup = None
        for _ in range(12):
            popup = self.page.find_ticket_quantity_popup()
            if popup:
                break
            time.sleep(0.5)

        if not popup:
            logging.warning("Ticket quantity popup did not appear — continuing without it")
            return False

        self._screenshot("qty-popup-appeared")

        qty_btns = self.page.find_quantity_buttons_in_popup(popup)
        desired = max(1, min(TICKET_QUANTITY, 6))  # clamp 1–6

        if qty_btns:
            logging.info("Quantity buttons found: %s", [n for n, _ in qty_btns])
            # Find the button for desired quantity; fallback to nearest available
            chosen_btn = None
            for n, btn in qty_btns:
                if n == desired:
                    chosen_btn = btn
                    break
            if not chosen_btn:
                # Pick the largest available <= desired, or smallest if none
                lower = [(n, b) for n, b in qty_btns if n <= desired]
                chosen_btn = lower[-1][1] if lower else qty_btns[0][1]
                logging.warning("Exact qty %s not available — using closest", desired)

            self._click(chosen_btn)
            logging.info("Selected ticket quantity: %s", desired)
            time.sleep(0.5)
            self._screenshot("qty-selected")
        else:
            logging.warning("No numbered qty buttons found in popup — skipping qty selection")

        # Click Continue
        continue_btn = self.page.find_popup_continue_button(popup)
        if continue_btn:
            logging.info("Clicking Continue in qty popup")
            self._click(continue_btn)
            time.sleep(2)
            self._screenshot("qty-popup-continued")
            return True
        else:
            # Try pressing Enter as fallback
            logging.warning("No Continue button found in popup — pressing Enter")
            from selenium.webdriver.common.keys import Keys
            try:
                popup.send_keys(Keys.RETURN)
            except WebDriverException:
                pass
            time.sleep(2)
            return True

    # ── Dynamic availability check (works for ANY page) ──────────────────────

    def _check_available(self) -> Optional[WebElement]:
        """Legacy compatibility shim — delegates to _advance_to_stands()."""
        return self._advance_to_stands()

    def _advance_to_stands(self, max_steps: int = 6) -> Optional[WebElement]:
        """State-machine driver: advances through the RCB ticket flow
        until we reach the STAND_LIST (or MATCH_LIST) stage and returns the
        element that represents 'tickets are open and we should proceed'.

        Returns a sentinel WebElement (the CTA that got us to this point)
        if tickets are available, or None if not yet / sold out.

        Does NOT click stands — that's _select_stand()'s job.
        """
        assert self.page and self.driver

        for step in range(max_steps):
            try:
                # Safety: abort if we've navigated off our target domain
                target_domain = urllib.parse.urlparse(self.config.target_url).netloc.lower()
                current_domain = urllib.parse.urlparse(self.driver.current_url).netloc.lower()
                if target_domain and target_domain not in current_domain:
                    logging.warning("Off-site (%s) — navigating back to %s",
                                    current_domain, self.config.target_url)
                    self.driver.get(self.config.target_url)
                    self._wait_ready_robust()
                    continue

                stage = self.page.classify_page(self.driver.current_url.lower())
                logging.info("[stage-step %s] Page stage: %s  URL: %s",
                             step, stage.name, self.driver.current_url[:80])
                self.page.log_page_summary()

                if stage == PageStage.ERROR_PAGE:
                    logging.warning("Server error page — will retry next cycle")
                    return None

                if stage == PageStage.SOLD_OUT:
                    logging.info("Sold out signal on page")
                    return None

                if stage == PageStage.LOGIN_WALL:
                    self._handle_login_wall()
                    continue

                if stage in (PageStage.PAYMENT, PageStage.CHECKOUT):
                    # Somehow already past stands — shouldn't happen during polling
                    logging.warning("Unexpected stage %s during availability check", stage.name)
                    return None

                if stage == PageStage.QTY_POPUP:
                    # Popup appeared unexpectedly — ticket selection already triggered
                    # Return a dummy sentinel so caller knows to proceed
                    logging.info("QTY popup visible — tickets are live!")
                    try:
                        return self.driver.find_element(By.TAG_NAME, "body")
                    except WebDriverException:
                        return None

                if stage == PageStage.SEAT_MAP:
                    logging.info("Seat map visible — tickets are live!")
                    try:
                        return self.driver.find_element(By.TAG_NAME, "body")
                    except WebDriverException:
                        return None

                if stage == PageStage.STAND_LIST:
                    logging.info("Stand list visible — tickets are OPEN!")
                    # Return the first stand element as the 'available' signal
                    stands = self.page.find_stand_buttons()
                    if stands:
                        return stands[0][2]  # (name, price, element)
                    # Stands detected by text but no elements found yet — use body
                    try:
                        return self.driver.find_element(By.TAG_NAME, "body")
                    except WebDriverException:
                        return None

                if stage == PageStage.MATCH_LIST:
                    logging.info("Match list visible — looking for un-booked matches")
                    ctas = self.page.find_all_primary_ctas()
                    if ctas:
                        # Skip matches we already booked (by href)
                        chosen = None
                        for cta in ctas:
                            try:
                                href = (cta.get_attribute("href") or "").strip()
                            except WebDriverException:
                                continue
                            if href and href in self._booked_matches:
                                logging.info("  Skipping already-booked match: %s", href[:80])
                                continue
                            chosen = cta
                            self._current_match_id = href or self._safe_text(cta)[:80]
                            break
                        if chosen is None:
                            logging.info("All visible matches already booked!")
                            return None
                        logging.info("Clicking match: %s", self._current_match_id[:80] if self._current_match_id else "?")
                        self._scroll_to(chosen)
                        self._click(chosen)
                        time.sleep(2)
                        self._wait_ready_robust(retries=4)
                        continue
                    return None

                if stage == PageStage.MATCH_DETAIL:
                    logging.info("Match detail — looking for ticket entry CTA")
                    cta = self.page.find_primary_cta()
                    if cta:
                        self._scroll_to(cta)
                        self._click(cta)
                        time.sleep(2)
                        self._wait_ready_robust(retries=4)
                        continue
                    return None

                if stage in (PageStage.HOME, PageStage.TICKETS_NAV):
                    logging.info("Home/nav — looking for tickets entry CTA")
                    cta = self.page.find_primary_cta()
                    if cta:
                        self._scroll_to(cta)
                        self._click(cta)
                        time.sleep(2)
                        self._wait_ready_robust(retries=4)
                        continue
                    logging.info("No CTA found on home/nav page — tickets not open yet")
                    return None

                # UNKNOWN — we don't recognise this page as any ticket flow stage.
                # This is NOT availability — return None so we wait for next poll.
                logging.info("Unknown page stage — no ticket signals. Waiting for next poll.")
                return None

            except WebDriverException as exc:
                logging.warning("[stage-step %s] WebDriver error: %s — retrying", step, exc)
                time.sleep(1)

        logging.warning("Reached max stage steps without reaching stands")
        return None

    # ── Dynamic option selection ──────────────────────────────────────────────

    def _select_product_options(self) -> None:
        """Dynamically detect and select product options in page order (top-to-bottom)."""
        assert self.page
        groups = self.page.find_product_options()
        if not groups:
            logging.info("No product option groups detected")
            return

        preferred = {
            "size": MERCH_SIZE.lower() if MERCH_SIZE else "",
            "category": MERCH_CATEGORY.lower() if MERCH_CATEGORY else "",
        }

        for label, options, y_pos in groups:
            pref = preferred.get(label, "")
            logging.info("Selecting option '%s' (y=%s, %s choices)", label, y_pos, len(options))

            if not options:
                continue

            # If it's a <select>, handle specially
            if options[0].tag_name.lower() == "select":
                sel_el = options[0]
                sel = Select(sel_el)
                if pref:
                    for opt in sel.options:
                        if pref in (opt.text or "").lower() or pref in (opt.get_attribute("value") or "").lower():
                            sel.select_by_value(opt.get_attribute("value"))
                            logging.info("Selected %s: %s (dropdown)", label, opt.text)
                            break
                continue

            # For buttons, try to find one matching preference
            selected = False
            if pref:
                for opt in options:
                    opt_text = self._safe_text(opt).lower().strip()
                    if opt_text == pref or pref in opt_text:
                        self._click(opt)
                        logging.info("Selected %s: '%s'", label, opt_text)
                        selected = True
                        time.sleep(0.5)
                        break
            if not selected:
                # Click the first enabled option as default
                for opt in options:
                    try:
                        if opt.is_enabled() and opt.is_displayed():
                            self._click(opt)
                            logging.info("Auto-selected first %s: '%s'", label, self._safe_text(opt))
                            time.sleep(0.5)
                            break
                    except WebDriverException:
                        pass

    # ── Dynamic quantity ──────────────────────────────────────────────────────

    def _set_quantity(self, desired: int) -> None:
        assert self.page
        logging.info("Setting quantity to %s (min=%s)", desired, self.config.min_quantity)
        el = self.page.find_quantity_input()
        if not el:
            logging.info("No quantity field found — using default")
            return

        tag = el.tag_name.lower()
        if tag == "select":
            sel = Select(el)
            options = [o.get_attribute("value") for o in sel.options]
            target = str(desired)
            if target in options:
                sel.select_by_value(target)
            else:
                nums = [int(v) for v in options if v.isdigit()]
                # Pick highest available that is >= min_quantity
                pick = max((n for n in nums if n <= desired), default=max(nums, default=1))
                if pick < self.config.min_quantity:
                    logging.warning("Max available quantity (%s) is below MIN_QUANTITY (%s)", pick, self.config.min_quantity)
                sel.select_by_value(str(pick))
                logging.info("Qty clamped to %s", pick)
        else:
            self._scroll_to(el)
            el.clear()
            el.send_keys(str(desired))
            time.sleep(0.3)
            actual = el.get_attribute("value")
            if actual and actual.isdigit() and int(actual) != desired:
                mx = el.get_attribute("max")
                cap = min(desired, int(mx)) if mx and mx.isdigit() else desired
                if cap < self.config.min_quantity:
                    logging.warning("Page max quantity (%s) is below MIN_QUANTITY (%s) — proceeding anyway", cap, self.config.min_quantity)
                # Use JavaScript to force value + fire change events
                self.driver.execute_script(
                    "var el=arguments[0]; var nativeInputValueSetter="
                    "Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype,'value').set;"
                    "nativeInputValueSetter.call(el, arguments[1]);"
                    "el.dispatchEvent(new Event('input',{bubbles:true}));"
                    "el.dispatchEvent(new Event('change',{bubbles:true}));",
                    el, str(cap)
                )
                time.sleep(0.5)
                logging.info("Forced quantity via JS to %s", cap)
            else:
                logging.info("Quantity field accepted value: %s", actual)
        logging.info("Quantity set")

    # ── Dynamic cart navigation ───────────────────────────────────────────────

    def _go_to_cart(self) -> bool:
        assert self.driver and self.page
        logging.info("Navigating to cart …")
        time.sleep(1)

        btn = self.page.find_cart_button()
        if btn:
            label = self._safe_text(btn) or btn.get_attribute("class") or "icon"
            logging.info("Clicking cart element: '%s'", label[:60])
            self._click(btn)
            time.sleep(3)
            self._wait_ready()
            self._screenshot("cart-page")
            return True

        # Fallback: direct URL — check that we actually landed on a cart page
        # (not a redirect back to merchandise listing)
        base = re.match(r"(https?://[^/]+)", self.driver.current_url)
        if base:
            for path in ("/cart", "/bag", "/checkout"):
                url = base.group(1) + path
                logging.info("Trying direct: %s", url)
                self.driver.get(url)
                self._wait_ready()
                # Verify we're actually on a cart page, not redirected
                current = self.driver.current_url.lower()
                if any(seg in current for seg in ("/cart", "/bag", "/basket", "/checkout")):
                    self._screenshot("cart-page-direct")
                    return True
                # Also check body for cart-specific elements
                try:
                    body = self.driver.find_element(By.TAG_NAME, "body").text.lower()
                    if any(w in body for w in ("subtotal", "total price", "order summary", "your cart", "your bag", "shopping bag", "shopping cart")):
                        self._screenshot("cart-page-direct")
                        return True
                except WebDriverException:
                    pass

        logging.warning("Could not navigate to cart")
        return False

    def _proceed_to_checkout(self) -> bool:
        assert self.page
        logging.info("Looking for checkout button …")
        btn = self.page.find_checkout_button()
        if btn:
            logging.info("Clicking: '%s'", self._safe_text(btn))
            self._click(btn)
            time.sleep(3)
            self._wait_ready()
            self._screenshot("checkout-page")
            return True
        logging.info("No explicit checkout button — may already be on checkout")
        return False

    # ── Dynamic form filling ──────────────────────────────────────────────────

    def _fill_forms(self) -> None:
        assert self.page
        logging.info("Scanning for form fields …")

        # 1) Name fields
        name_fields = self.page.find_name_fields()
        if name_fields:
            field_types = [ft for _, ft in name_fields]
            logging.info("Found %s name field(s): %s", len(name_fields), field_types)
            has_first_last = "first" in field_types and "last" in field_types
            if has_first_last:
                # Split the first person's full name into first/last
                full_name = NAMES[0] if NAMES else ""
                parts = full_name.split(None, 1)
                first_name = parts[0] if parts else full_name
                last_name = parts[1] if len(parts) > 1 else ""
                for field, ftype in name_fields:
                    val = first_name if ftype == "first" else last_name if ftype == "last" else full_name
                    try:
                        self._scroll_to(field)
                        field.clear()
                        field.send_keys(val)
                        logging.info("Filled %s name: %s", ftype, val)
                    except WebDriverException as exc:
                        logging.warning("Could not fill %s name: %s", ftype, exc)
            else:
                # Full name fields — one per attendee
                for idx, (field, _) in enumerate(name_fields):
                    name = NAMES[idx] if idx < len(NAMES) else NAMES[-1]
                    try:
                        self._scroll_to(field)
                        field.clear()
                        field.send_keys(name)
                        logging.info("Filled name #%s: %s", idx + 1, name)
                    except WebDriverException as exc:
                        logging.warning("Could not fill name #%s: %s", idx + 1, exc)
        else:
            logging.info("No name/attendee fields found — skipping")

        # 2) Email
        if CONTACT_EMAIL:
            for ef in self.page.find_email_fields():
                try:
                    ef.clear()
                    ef.send_keys(CONTACT_EMAIL)
                    logging.info("Filled email: %s", CONTACT_EMAIL)
                except WebDriverException:
                    pass

        # 3) Phone
        if CONTACT_PHONE:
            for pf in self.page.find_phone_fields():
                try:
                    pf.clear()
                    pf.send_keys(CONTACT_PHONE)
                    logging.info("Filled phone: %s", CONTACT_PHONE)
                except WebDriverException:
                    pass

        # 4) Gender
        if GENDER:
            gender_radios = self.page.find_gender_radios()
            if gender_radios:
                logging.info("Found gender options: %s", list(gender_radios.keys()))
                target = GENDER.lower().strip()
                # Try exact match first, then partial
                clicked = False
                for key, el in gender_radios.items():
                    if key == target or target in key or key in target:
                        try:
                            self._click(el)
                            logging.info("Selected gender: %s", key)
                            clicked = True
                        except WebDriverException:
                            # Try clicking the label instead
                            try:
                                label = self.driver.find_element(By.XPATH, f"//label[@for='{el.get_attribute('id')}']")
                                self._click(label)
                                logging.info("Selected gender via label: %s", key)
                                clicked = True
                            except (NoSuchElementException, WebDriverException):
                                pass
                        break
                if not clicked:
                    logging.warning("Could not select gender '%s' from %s", target, list(gender_radios.keys()))
            else:
                logging.info("No gender field found")

        # 5) Shipping address
        addr_fields = self.page.find_address_fields()
        if addr_fields:
            logging.info("Found address fields: %s", list(addr_fields.keys()))
            addr_values = {
                "address1": ADDRESS_LINE1,
                "address2": ADDRESS_LINE2,
                "landmark": ADDRESS_LANDMARK,
                "city": ADDRESS_CITY,
                "state": ADDRESS_STATE,
                "pincode": ADDRESS_PINCODE,
            }
            for field_key, el in addr_fields.items():
                val = addr_values.get(field_key, "")
                if not val:
                    continue
                try:
                    self._scroll_to(el)
                    tag = el.tag_name.lower()
                    if tag == "select":
                        sel = Select(el)
                        for opt in sel.options:
                            if val.lower() in (opt.text or "").lower() or val.lower() in (opt.get_attribute("value") or "").lower():
                                sel.select_by_value(opt.get_attribute("value"))
                                logging.info("Selected %s: %s (dropdown)", field_key, opt.text)
                                break
                    else:
                        el.clear()
                        el.send_keys(val)
                        logging.info("Filled %s: %s", field_key, val)
                except WebDriverException as exc:
                    logging.warning("Could not fill %s: %s", field_key, exc)
        else:
            logging.info("No shipping address fields found")

        self._screenshot("forms-filled")

    # ── Dynamic UPI payment ───────────────────────────────────────────────────

    def _try_upi_payment(self) -> bool:
        assert self.page
        logging.info("Looking for UPI payment option …")

        upi_el = self.page.find_upi_option()
        if upi_el:
            logging.info("Clicking UPI option")
            self._click(upi_el)
            time.sleep(2)
        else:
            logging.info("No UPI tab/radio found — checking for VPA field directly")

        self._screenshot("payment-page")

        vpa_field = self.page.find_vpa_input()
        if vpa_field:
            self._scroll_to(vpa_field)
            vpa_field.clear()
            vpa_field.send_keys(self.config.upi_vpa)
            logging.info("Entered VPA: %s", self.config.upi_vpa)
            self._screenshot("upi-vpa-entered")
            time.sleep(1)

            # Click VERIFY AND PAY / PAY NOW / Submit
            pay_btn = self.page.find_pay_button()
            if pay_btn:
                logging.info("Clicking pay button: '%s'", (pay_btn.text or "").strip()[:40])
                self._click(pay_btn)
                time.sleep(3)
                self._screenshot("upi-request-sent")
                return True
            else:
                logging.warning("VPA entered but no pay button found")
                self._screenshot("no-pay-button")
                return True
        else:
            logging.warning("No VPA input found on page")
            self._screenshot("no-upi-field")
            return False

    # ── Seat map handling ─────────────────────────────────────────────────────

    def _handle_seat_map(self) -> bool:
        assert self.driver and self.page
        logging.info("Checking for seat map …")

        # Check for iframe
        switched = False
        for xp in (
            "//iframe[contains(@src,'seat') or contains(@src,'map') or contains(@src,'venue')]",
            "//iframe[contains(@id,'seat') or contains(@id,'map')]",
        ):
            for iframe in self.driver.find_elements(By.XPATH, xp):
                if iframe.is_displayed():
                    try:
                        self.driver.switch_to.frame(iframe)
                        switched = True
                        self._wait_ready()
                        break
                    except WebDriverException:
                        pass
            if switched:
                break

        has_map = switched or self.page.has_seat_map()
        if not has_map:
            logging.info("No seat map detected")
            return False

        self._screenshot("seat-map-detected")

        # Try auto-select
        seats = self.page.find_available_seats()
        desired = self.config.quantity
        selected = 0

        if seats:
            # Group by parent for "together" selection
            groups: dict = {}
            for s in seats:
                try:
                    pid = id(s.find_element(By.XPATH, "./.."))
                except WebDriverException:
                    pid = 0
                groups.setdefault(pid, []).append(s)

            # Pick the group with the most consecutive seats, but at least min_quantity
            desired = self.config.quantity
            min_qty = self.config.min_quantity
            best = max(groups.values(), key=len)
            best_count = len(best)

            if best_count < min_qty:
                logging.warning(
                    "Best consecutive seat group has only %s seats (min required: %s) — falling back to manual",
                    best_count, min_qty,
                )
                self._manual_seat_pause()
                if switched:
                    try:
                        self.driver.switch_to.default_content()
                    except WebDriverException:
                        pass
                self._screenshot("seats-done")
                return True

            # Select up to desired, at least min_qty
            seats_to_select = best[:desired]
            for s in seats_to_select:
                try:
                    self._scroll_to(s)
                    self._click(s)
                    selected += 1
                    time.sleep(0.3)
                except WebDriverException:
                    pass

        if selected > 0:
            logging.info("Auto-selected %s/%s seats (min=%s)", selected, desired, self.config.min_quantity)
            self._screenshot("seats-auto-selected")

        if selected < self.config.min_quantity:
            self._manual_seat_pause()

        if switched:
            try:
                self.driver.switch_to.default_content()
            except WebDriverException:
                pass

        self._screenshot("seats-done")
        return True

    def _manual_seat_pause(self) -> None:
        msg = f"Select {self.config.quantity} seats manually! ({MANUAL_SEAT_TIMEOUT}s or press ENTER)"
        logging.warning(msg)
        if ENABLE_NOTIFICATIONS:
            try:
                notification.notify(title="RCB — Select Seats!", message=msg, app_name="RCB Monitor", timeout=MANUAL_SEAT_TIMEOUT)
            except Exception:
                pass
        self._play_short_alert()
        print(f"\n{'!'*60}\n  {msg}\n{'!'*60}\n")
        done = threading.Event()
        def _wi():
            try:
                input()
            except EOFError:
                pass
            done.set()
        threading.Thread(target=_wi, daemon=True).start()
        done.wait(timeout=MANUAL_SEAT_TIMEOUT)
        self._screenshot("after-seat-selection")

    # ── Universal checkout flow ───────────────────────────────────────────────

    def _checkout_flow(self, purchase_btn: WebElement, stand_index: int = 0) -> None:
        """Universal checkout flow — works for merch, tickets, anything."""
        assert self.driver and self.page
        logging.info("══ Starting checkout flow ══")

        # ── TICKET MODE: stand selection + quantity popup ──────────────────
        if self.config.mode == "live-tickets":
            # _advance_to_stands() already navigated us to the stand list.
            # Only click the trigger if it looks like a match/fixture link
            # (not a stand element or <body> sentinel from the state machine).
            tag = (purchase_btn.tag_name or "").lower()
            btn_text = self._safe_text(purchase_btn).lower()
            is_stand_page = self.page.classify_page(self.driver.current_url.lower()) == PageStage.STAND_LIST
            if not is_stand_page and tag in ("a", "button") and btn_text:
                self._scroll_to(purchase_btn)
                self._click(purchase_btn)
                logging.info("Clicked match/ticket entry: '%s'", btn_text)
                time.sleep(3)
                self._wait_ready()
                self._screenshot("ticket-page-loaded")
            else:
                logging.info("Already on stand list — skipping trigger re-click")

            # Step T1: Select preferred stand
            stand_clicked = self._select_stand(stand_index)

            if not stand_clicked:
                logging.warning("Stand selection failed — aborting checkout "
                                "(no ticket was actually selected)")
                return

            # Step T2: Handle "How many tickets?" popup
            self._handle_ticket_quantity_popup()
            time.sleep(2)
            self._wait_ready()

            # Step T3: Seat map — user picks seats (or auto-select if possible)
            if self.page.has_seat_map():
                self._handle_seat_map()
                time.sleep(2)
                self._wait_ready()
            else:
                logging.info("No seat map detected after stand selection")

            # Verify we're now in a genuine ticket checkout flow, not merchandise
            stage = self.page.classify_page(self.driver.current_url.lower())
            if stage not in (PageStage.CHECKOUT, PageStage.PAYMENT,
                             PageStage.QTY_POPUP, PageStage.SEAT_MAP):
                logging.warning("After stand click, page stage is %s (not a "
                                "ticket checkout) — aborting to avoid "
                                "accidental merchandise purchase", stage.name)
                return

            # From here fall through to standard checkout (forms + payment)

        # ── MERCH MODE: standard product option + quantity flow ────────────
        else:
            # Step 1: Select product options (size, color, etc.)
            self._select_product_options()
            time.sleep(1)

            # Re-find purchase button (DOM may have updated after option selection)
            fresh_btn = self.page.find_purchase_button()
            if fresh_btn:
                purchase_btn = fresh_btn

            # Step 2: Set quantity
            self._set_quantity(self.config.quantity)
            time.sleep(0.5)

            # Step 3: Click the purchase button
            self._scroll_to(purchase_btn)
            self._click(purchase_btn)
            logging.info("Clicked: '%s'", self._safe_text(purchase_btn))
            time.sleep(2)
            self._screenshot("purchase-clicked")

            # Step 3b: Retry on popup (size/option not selected etc.)
            for add_attempt in range(3):
                popup = self.page.find_popup_or_alert()
                if not popup:
                    break
                popup_text = (popup.text or "").lower()
                logging.warning("Popup detected (attempt %s): '%s'", add_attempt + 1, popup_text[:120])
                self._screenshot(f"popup-attempt-{add_attempt + 1}")
                needs_retry = any(kw in popup_text for kw in (
                    "select", "choose", "required", "please", "size", "category",
                    "variant", "option", "color", "colour",
                ))
                if not needs_retry:
                    break
                for close_xp in (
                    ".//button[contains(@class, 'close') or contains(@aria-label, 'Close') or contains(@aria-label, 'close')]",
                    ".//button[contains(text(), '\u00d7') or contains(text(), 'OK') or contains(text(), 'ok')]",
                    ".//*[contains(@class, 'dismiss') or contains(@class, 'close')]",
                ):
                    try:
                        for cb in popup.find_elements(By.XPATH, close_xp):
                            if cb.is_displayed():
                                self._click(cb)
                                time.sleep(1)
                                break
                    except WebDriverException:
                        pass
                time.sleep(1)
                self._select_product_options()
                time.sleep(1)
                fresh_btn = self.page.find_purchase_button()
                if fresh_btn:
                    self._scroll_to(fresh_btn)
                    self._click(fresh_btn)
                    logging.info("Re-clicked purchase button (attempt %s)", add_attempt + 1)
                    time.sleep(2)
                    self._screenshot(f"purchase-retry-{add_attempt + 1}")

            # Handle seat map for merch (unlikely but guard)
            if self.page.has_seat_map():
                self._handle_seat_map()
                time.sleep(2)
                self._wait_ready()

        # ── COMMON: cart → checkout → forms → payment ─────────────────────

        # Check for cart overlay/drawer
        time.sleep(2)
        overlay = self.page.find_cart_overlay()
        if overlay:
            logging.info("Cart overlay detected — looking for checkout button inside")
            self._screenshot("cart-overlay")
            try:
                for ob in overlay.find_elements(By.XPATH, ".//button | .//a | .//*[@role='button']"):
                    text = self._safe_text(ob).lower()
                    if any(kw in text for kw in ("checkout", "proceed", "view cart", "view bag", "go to cart", "go to bag")):
                        logging.info("Clicking overlay button: '%s'", text)
                        self._click(ob)
                        time.sleep(3)
                        self._wait_ready()
                        break
            except WebDriverException:
                pass

        # Navigate to cart if not already there
        url_lower = self.driver.current_url.lower()
        on_cart_page = any(seg in url_lower for seg in ("/cart", "/bag", "/basket", "/checkout"))
        if not on_cart_page:
            if not self._go_to_cart():
                logging.info("Cart nav failed — continuing from current page")

        # Cart empty check
        try:
            body_text = self.driver.find_element(By.TAG_NAME, "body").text.lower()
            if any(kw in body_text for kw in ("bag is empty", "cart is empty", "basket is empty", "no items", "start shopping")):
                logging.warning("Cart is empty! Going back to retry.")
                self._screenshot("cart-empty")
                self.driver.get(self.config.target_url)
                self._wait_ready()
                raise RuntimeError("Cart was empty — purchase likely failed")
        except RuntimeError:
            raise
        except WebDriverException:
            pass

        # Adjust quantity on cart page (merch only)
        if self.config.mode != "live-tickets":
            qty_el = self.page.find_quantity_input()
            if qty_el:
                self._set_quantity(self.config.quantity)
        self._screenshot("cart-state")

        self._proceed_to_checkout()
        self._fill_forms()

        checkout_url = self.driver.current_url
        # Try to advance again (some sites have multi-step checkout)
        # but only if we're not already on a payment gateway
        if not any(d in checkout_url.lower() for d in ("juspay", "razorpay", "paytm", "cashfree", "ccavenue", "billdesk")):
            self._proceed_to_checkout()

        time.sleep(4)
        self._wait_ready()
        self._screenshot("payment-gateway-loaded")

        payment_url = self.driver.current_url
        if payment_url != checkout_url:
            self.driver.execute_script("window.open(arguments[0], '_blank');", checkout_url)
            logging.info("Opened checkout page in new tab: %s", checkout_url)
            self.driver.switch_to.window(self.driver.window_handles[0])
            time.sleep(1)

        upi_sent = self._try_upi_payment()
        self._send_notification("checkout")
        self._siren_alert(upi_sent)

    # ── Alerts ────────────────────────────────────────────────────────────────

    def _send_notification(self, stage: str) -> None:
        """Send email + WhatsApp notifications.
        stage: 'available' or 'checkout'
        """
        url = self.driver.current_url if self.driver else "N/A"
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        if stage == "available":
            subject = "\U0001f6a8 RCB: Tickets AVAILABLE!"
            body = f"Tickets are AVAILABLE on {self.config.target_url}\n\nBot is proceeding to checkout now.\nTime: {ts}\n"
        else:
            subject = "\u2705 RCB: Checkout reached!"
            body = f"Bot reached the payment page.\n\nPayment URL: {url}\nTime: {ts}\n"

        # ── Email (SMTP auto-send, fallback: Gmail compose in default browser) ─
        recipient = NOTIFY_EMAIL or CONTACT_EMAIL
        if recipient:
            sent = False
            if SMTP_EMAIL and SMTP_PASSWORD:
                try:
                    msg = MIMEText(body)
                    msg["Subject"] = subject
                    msg["From"] = SMTP_EMAIL
                    msg["To"] = recipient
                    with smtplib.SMTP("smtp.gmail.com", 587, timeout=10) as srv:
                        srv.starttls()
                        srv.login(SMTP_EMAIL, SMTP_PASSWORD)
                        srv.sendmail(SMTP_EMAIL, [recipient], msg.as_string())
                    logging.info("Email SENT via SMTP to %s [%s]", recipient, stage)
                    sent = True
                except Exception as exc:
                    logging.warning("SMTP send failed: %s", exc)
            if not sent:
                try:
                    import ctypes
                    gmail_url = (
                        f"https://mail.google.com/mail/?view=cm"
                        f"&to={urllib.parse.quote(recipient)}"
                        f"&su={urllib.parse.quote(subject)}"
                        f"&body={urllib.parse.quote(body)}"
                    )
                    # Open in default browser (already logged into Gmail)
                    webbrowser.open(gmail_url)
                    logging.info("Gmail compose opened for %s [%s]", recipient, stage)
                    # Wait for Gmail to fully load
                    time.sleep(8)
                    # Use ctypes to find the browser window and send Ctrl+Enter
                    user32 = ctypes.windll.user32
                    # Use EnumWindows to find the Gmail/Compose window
                    target_hwnd = ctypes.c_void_p(0)
                    WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
                    def _find_gmail(hwnd, _):
                        length = user32.GetWindowTextLengthW(hwnd)
                        if length > 0:
                            buf = ctypes.create_unicode_buffer(length + 1)
                            user32.GetWindowTextW(hwnd, buf, length + 1)
                            title = buf.value
                            if any(kw in title for kw in ("Compose", "Gmail", "New Message")):
                                if user32.IsWindowVisible(hwnd):
                                    target_hwnd.value = hwnd
                                    return False  # stop enumeration
                        return True
                    user32.EnumWindows(WNDENUMPROC(_find_gmail), 0)
                    hwnd = target_hwnd.value
                    if hwnd:
                        user32.ShowWindow(hwnd, 9)  # SW_RESTORE
                        user32.SetForegroundWindow(hwnd)
                        time.sleep(1)
                        # Ctrl+Enter to send
                        VK_CONTROL = 0x11
                        VK_RETURN = 0x0D
                        user32.keybd_event(VK_CONTROL, 0, 0, 0)
                        time.sleep(0.05)
                        user32.keybd_event(VK_RETURN, 0, 0, 0)
                        time.sleep(0.05)
                        user32.keybd_event(VK_RETURN, 0, 2, 0)  # key up
                        user32.keybd_event(VK_CONTROL, 0, 2, 0)  # key up
                        logging.info("Gmail Ctrl+Enter sent via ctypes [%s]", stage)
                    else:
                        logging.warning("Gmail window not found, trying fallback [%s]", stage)
                        # Fallback: Alt+Tab to last window then Ctrl+Enter
                        VK_MENU = 0x12
                        VK_TAB = 0x09
                        VK_CONTROL = 0x11
                        VK_RETURN = 0x0D
                        user32.keybd_event(VK_MENU, 0, 0, 0)
                        user32.keybd_event(VK_TAB, 0, 0, 0)
                        time.sleep(0.05)
                        user32.keybd_event(VK_TAB, 0, 2, 0)
                        user32.keybd_event(VK_MENU, 0, 2, 0)
                        time.sleep(1)
                        user32.keybd_event(VK_CONTROL, 0, 0, 0)
                        time.sleep(0.05)
                        user32.keybd_event(VK_RETURN, 0, 0, 0)
                        time.sleep(0.05)
                        user32.keybd_event(VK_RETURN, 0, 2, 0)
                        user32.keybd_event(VK_CONTROL, 0, 2, 0)
                        logging.info("Gmail fallback Alt+Tab+Ctrl+Enter [%s]", stage)
                    time.sleep(3)
                except Exception as exc:
                    logging.warning("Gmail compose failed: %s", exc)

        # ── WhatsApp (ctypes for reliable window focus + keypress) ────────
        if NOTIFY_WHATSAPP and os.name == "nt":
            import ctypes
            wa_text = f"{subject}\n{body}"
            for phone in NOTIFY_WHATSAPP:
                try:
                    # Open WhatsApp with text pre-filled in the URL
                    encoded = urllib.parse.quote(wa_text)
                    wa_url = f"whatsapp://send?phone={phone}&text={encoded}"
                    os.startfile(wa_url)
                    time.sleep(5)

                    # Find WhatsApp window and force it to foreground
                    user32 = ctypes.windll.user32
                    hwnd = user32.FindWindowW(None, "WhatsApp")
                    if not hwnd:
                        # Try other window titles
                        for title in ("WhatsApp Desktop", "WhatsApp (2)"):
                            hwnd = user32.FindWindowW(None, title)
                            if hwnd:
                                break
                    if hwnd:
                        user32.ShowWindow(hwnd, 9)  # SW_RESTORE
                        user32.SetForegroundWindow(hwnd)
                        time.sleep(1)
                        # Send Enter key to dispatch the pre-filled message
                        VK_RETURN = 0x0D
                        user32.keybd_event(VK_RETURN, 0, 0, 0)  # key down
                        time.sleep(0.05)
                        user32.keybd_event(VK_RETURN, 0, 2, 0)  # key up
                        logging.info("WhatsApp sent to %s [%s]", phone, stage)
                    else:
                        logging.warning("WhatsApp window not found for %s", phone)
                    time.sleep(3)
                except Exception as exc:
                    logging.warning("WhatsApp failed for %s: %s", phone, exc)

    def _siren_alert(self, upi_sent: bool) -> None:
        msg = "UPI VPA entered! Click PAY NOW manually" if upi_sent else "Checkout reached — manual payment needed"
        logging.warning(msg)
        if ENABLE_NOTIFICATIONS:
            try:
                notification.notify(title="RCB Monitor — Action Required!", message=msg, app_name="RCB Monitor", timeout=30)
            except Exception:
                pass
        self._play_siren()
        print(f"\n{'='*60}\n  {msg}\n  Press ENTER to close browser.\n{'='*60}\n")
        input()

    @staticmethod
    def _play_wav_loop(wav_path: str, duration: int) -> None:
        """Loop a WAV file for *duration* seconds via the system audio driver."""
        import winsound
        end = time.monotonic() + duration
        while time.monotonic() < end:
            winsound.PlaySound(wav_path, winsound.SND_FILENAME)

    @staticmethod
    def _get_alarm_wav() -> str:
        """Return path to a Windows alarm WAV, with fallbacks."""
        candidates = [
            r"C:\Windows\Media\Alarm01.wav",
            r"C:\Windows\Media\Windows Critical Stop.wav",
            r"C:\Windows\Media\Windows Exclamation.wav",
            r"C:\Windows\Media\chord.wav",
        ]
        for p in candidates:
            if os.path.exists(p):
                return p
        return ""

    @staticmethod
    def _play_siren() -> None:
        """Loud alternating siren — runs for 15 seconds."""
        if os.name != "nt":
            for _ in range(20):
                print("\a", end="", flush=True)
                time.sleep(0.5)
            return
        try:
            import winsound
            wav = WebsiteMonitor._get_alarm_wav()
            if wav:
                WebsiteMonitor._play_wav_loop(wav, 15)
            else:
                # Fallback to Beep if no WAV found
                end = time.monotonic() + 15
                while time.monotonic() < end:
                    winsound.Beep(2500, 400)
                    winsound.Beep(1000, 400)
        except Exception:
            for _ in range(20):
                print("\a", end="", flush=True)
                time.sleep(0.5)

    @staticmethod
    def _play_short_alert() -> None:
        if os.name != "nt":
            for _ in range(5):
                print("\a", end="", flush=True)
                time.sleep(0.3)
            return
        try:
            import winsound
            wav = WebsiteMonitor._get_alarm_wav()
            if wav:
                WebsiteMonitor._play_wav_loop(wav, 5)
            else:
                for _ in range(5):
                    winsound.Beep(2500, 400)
                    winsound.Beep(1500, 400)
        except Exception:
            for _ in range(5):
                print("\a", end="", flush=True)
                time.sleep(0.3)

    @staticmethod
    def _play_detection_siren() -> None:
        """Loud siren when availability first detected — runs for 60 seconds."""
        if os.name != "nt":
            for _ in range(60):
                print("\a", end="", flush=True)
                time.sleep(1)
            return
        try:
            import winsound
            wav = WebsiteMonitor._get_alarm_wav()
            if wav:
                WebsiteMonitor._play_wav_loop(wav, 60)
            else:
                end = time.monotonic() + 60
                while time.monotonic() < end:
                    winsound.Beep(3000, 250)
                    winsound.Beep(2000, 250)
        except Exception:
            for _ in range(60):
                print("\a", end="", flush=True)
                time.sleep(1)

    def _notify_available(self) -> None:
        logging.warning("*** AVAILABILITY FOUND! ***")
        if ENABLE_NOTIFICATIONS:
            try:
                notification.notify(
                    title="\U0001f6a8 AVAILABLE! \U0001f6a8",
                    message=f"GO NOW → {self.config.target_url}",
                    app_name="RCB Monitor", timeout=30,
                )
            except Exception:
                pass
        # Play siren in background so checkout can proceed immediately
        threading.Thread(target=self._play_detection_siren, daemon=True).start()
        # Notify: tickets available! (stage 1)
        self._send_notification("available")

    # ── Main loop ─────────────────────────────────────────────────────────────

    # ── Parallel worker infrastructure ──────────────────────────────────────

    def run(self) -> None:
        """Main loop: poll until tickets drop, then fire parallel stand workers."""
        try:
            self._open_page()
            cycle = 0
            while True:
                cycle += 1
                started = time.monotonic()
                logging.info("── Poll cycle %s ──", cycle)
                try:
                    btn = self._run_cycle(cycle)
                    if btn is not None:
                        if self.config.mode == "live-tickets":
                            # Tickets found — launch parallel workers
                            success = self._run_parallel_booking(btn)
                            if success:
                                match_id = self._current_match_id or self.driver.current_url
                                self._booked_matches.add(match_id)
                                logging.info(
                                    "\u2705 Match booked! (%d so far). ID: %s",
                                    len(self._booked_matches), match_id[:80],
                                )
                                logging.info("Navigating back to check for more matches …")
                                self._current_match_id = None
                                try:
                                    self.driver.get(self.config.target_url)
                                    self._wait_ready_robust()
                                except WebDriverException:
                                    pass
                                # Continue the polling loop to try the next match
                                continue
                            # If all workers failed, keep polling
                            logging.warning("All parallel workers failed — resuming polling")
                        else:
                            # Merch mode — single browser checkout
                            self._checkout_flow(btn)
                            return
                except Exception as exc:
                    logging.exception("Poll cycle %s error: %s", cycle, exc)
                    self._screenshot(f"cycle-{cycle}-error")
                # Health check — restart Chrome if session died
                self._ensure_session()
                self._sleep(started)
        finally:
            self._teardown()

    def _open_page(self) -> None:
        assert self.driver
        logging.info("Opening %s", self.config.target_url)
        self.driver.get(self.config.target_url)
        self._wait_ready_robust()
        self._screenshot("initial-page")

        if not self._handle_login_wall():
            raise SystemExit(1)

        if self.config.target_url not in self.driver.current_url:
            self.driver.get(self.config.target_url)
            self._wait_ready_robust()
            self._screenshot("target-after-login")

    def _run_cycle(self, cycle: int) -> "Optional[WebElement]":
        """Returns the purchase button if tickets are available, else None."""
        assert self.driver and self.page
        try:
            self.driver.refresh()
        except WebDriverException:
            pass
        self._wait_ready_robust()
        self._handle_captcha()

        if self._is_login_page():
            logging.warning("Session expired — re-authenticating")
            if not self._handle_login_wall():
                return None
            self.driver.get(self.config.target_url)
            self._wait_ready_robust()

        self._find_ticket_page()

        btn = self._check_available()
        if btn:
            self._screenshot(f"cycle-{cycle}-available")
            self._notify_available()
            return btn

        self._screenshot(f"cycle-{cycle}-not-available")
        self._cleanup_old_screenshots(cycle)
        return None

    def _run_parallel_booking(self, trigger_btn: WebElement) -> bool:
        """Spawn up to MAX_STAND_WORKERS threads, each targeting a different stand.
        Returns True if any worker completed checkout successfully.

        Strategy:
          - Worker 0 reuses THIS browser instance (no extra Chrome needed).
          - Workers 1..N each spin up a fresh Chrome with the saved profile.
          - A threading.Event signals the first success; others abort cleanly.
          - Workers are staggered by WORKER_STARTUP_JITTER seconds so they
            don't all hammer the server simultaneously.
        """
        num_workers = MAX_STAND_WORKERS
        success_event = threading.Event()
        results: Dict[int, bool] = {}
        lock = threading.Lock()

        logging.info("Launching %s parallel stand workers …", num_workers)

        def worker(idx: int) -> None:
            if success_event.is_set():
                logging.info("[worker-%s] Skipping — another worker already succeeded", idx)
                return
            # Stagger startup
            time.sleep(idx * WORKER_STARTUP_JITTER)
            if success_event.is_set():
                return

            # Worker 0 reuses self; others create a new monitor instance
            if idx == 0:
                mon = self
            else:
                try:
                    # ── Stealth path: use Scrapling/Camoufox (no Chrome needed) ──
                    if USE_STEALTH_BROWSER and _HAS_SCRAPLING:
                        logging.info("[worker-%s] Using stealth browser (Scrapling/Camoufox)", idx)
                        mon = WebsiteMonitor.__new__(WebsiteMonitor)
                        mon.config = self.config
                        mon._profile_dir = self._profile_dir
                        mon._booked_matches = self._booked_matches
                        mon._current_match_id = self._current_match_id
                        mon._is_stealth = True
                        mon.config.screenshot_dir.mkdir(parents=True, exist_ok=True)
                        mon._setup_stealth_driver()
                        mon.driver.get(self.config.target_url)
                        time.sleep(3)  # let JS settle
                    else:
                        # ── Original Selenium path ────────────────────────────
                        import shutil
                        worker_profile = Path(CHROME_PROFILE_DIR).resolve().parent / f"{Path(CHROME_PROFILE_DIR).name}_worker{idx}"
                        if worker_profile.exists():
                            shutil.rmtree(worker_profile, ignore_errors=True)
                        src_profile = Path(CHROME_PROFILE_DIR).resolve()
                        def _safe_copy2(src, dst):
                            """Copy file, silently skipping locked files (Chrome holds Cookies, Sessions, etc.)."""
                            try:
                                shutil.copy2(src, dst)
                            except (PermissionError, OSError) as e:
                                logging.debug("Skipping locked file during profile copy: %s (%s)", src, e)
                        shutil.copytree(src_profile, worker_profile, dirs_exist_ok=True,
                                        ignore=shutil.ignore_patterns("SingletonLock", "SingletonSocket",
                                                                       "SingletonCookie", "lockfile"),
                                        copy_function=_safe_copy2)
                        mon = WebsiteMonitor(self.config, profile_dir=str(worker_profile))
                        mon.driver.get(self.config.target_url)
                        mon._wait_ready_robust(retries=8)
                    if mon._is_login_page():
                        logging.warning("[worker-%s] Login wall — profile should be shared, checking …", idx)
                        # Profile is shared, so cookies should carry over.
                        # If still on login, wait up to 30s for worker-0 to log in first.
                        for _ in range(30):
                            time.sleep(1)
                            if not mon._is_login_page():
                                break
                        if mon._is_login_page():
                            logging.error("[worker-%s] Still on login page — skipping", idx)
                            mon._teardown()
                            return
                except Exception as exc:
                    logging.exception("[worker-%s] Setup failed: %s", idx, exc)
                    try:
                        mon._teardown()
                    except Exception:
                        pass
                    return

            try:
                # Each worker tries up to MAX_RETRIES times
                for attempt in range(1, self.config.max_retries + 1):
                    if success_event.is_set():
                        logging.info("[worker-%s] Aborting — success already confirmed", idx)
                        return
                    try:
                        logging.info("[worker-%s] Attempt %s — targeting stand index %s", idx, attempt, idx)
                        # Re-find purchase button (page may have changed)
                        if idx == 0 and attempt == 1:
                            btn = trigger_btn
                        else:
                            mon.driver.get(self.config.target_url)
                            mon._wait_ready_robust(retries=6)
                            btn = mon._check_available()
                            if not btn:
                                logging.warning("[worker-%s] Tickets gone on attempt %s", idx, attempt)
                                time.sleep(2)
                                continue
                        mon._checkout_flow(btn, stand_index=idx)
                        # If we get here without exception, it's a success
                        success_event.set()
                        with lock:
                            results[idx] = True
                        logging.info("[worker-%s] SUCCESS — checkout complete!", idx)
                        return
                    except Exception as exc:
                        logging.warning("[worker-%s] Attempt %s failed: %s", idx, attempt, exc)
                        mon._screenshot(f"worker-{idx}-attempt-{attempt}-fail")
                        if attempt < self.config.max_retries:
                            backoff = min(2 ** (attempt - 1), 8)
                            time.sleep(backoff)
                with lock:
                    results[idx] = False
                logging.error("[worker-%s] All %s attempts exhausted", idx, self.config.max_retries)
            finally:
                if idx != 0:
                    try:
                        mon._teardown()
                    except Exception:
                        pass
                    # Clean up worker profile copy (not needed for stealth workers)
                    if not getattr(mon, '_is_stealth', False):
                        try:
                            worker_profile = Path(CHROME_PROFILE_DIR).resolve().parent / f"{Path(CHROME_PROFILE_DIR).name}_worker{idx}"
                            if worker_profile.exists():
                                import shutil
                                shutil.rmtree(worker_profile, ignore_errors=True)
                        except Exception:
                            pass

        with ThreadPoolExecutor(max_workers=num_workers, thread_name_prefix="stand-worker") as pool:
            futures = [pool.submit(worker, i) for i in range(num_workers)]
            # Wait for all workers; success_event will be set by the first winner
            for f in as_completed(futures):
                try:
                    f.result()
                except Exception as exc:
                    logging.exception("Worker future exception: %s", exc)

        any_success = any(results.values())
        logging.info("Parallel booking done. Results: %s  Success: %s", results, any_success)
        return any_success

    def _sleep(self, started: float) -> None:
        remaining = max(0.0, self.config.check_interval - (time.monotonic() - started))
        logging.info("Next poll in %.1fs", remaining)
        time.sleep(remaining)

    def _teardown(self) -> None:
        if self.driver:
            try:
                self.driver.quit()
            except Exception:
                pass
            logging.info("Browser closed")


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    setup_logging()
    logging.info("Starting monitor — mode=%s target=%s qty=%s names=%s", MODE, TARGET_URL, QUANTITY, NAMES)
    # Clean Chrome caches on startup to reclaim disk space
    WebsiteMonitor._cleanup_chrome_cache(CHROME_PROFILE_DIR)
    WebsiteMonitor(Config()).run()
