import json
import logging
import os
import re
import smtplib
import subprocess
import sys
import threading
import time
import urllib.parse
import webbrowser
from dataclasses import dataclass
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
from selenium.webdriver.chrome.service import Service as ChromeService
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.remote.webelement import WebElement
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import Select, WebDriverWait
from webdriver_manager.chrome import ChromeDriverManager

# ── Environment ───────────────────────────────────────────────────────────────

load_dotenv()

MODE = os.getenv("MONITOR_MODE", "test-merch")
TARGET_URL = os.getenv(
    "TARGET_URL",
    "https://shop.royalchallengers.com/merchandise/152"
    if MODE == "test-merch"
    else "https://shop.royalchallengers.com/",
)
CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL", "120"))
MIN_PRICE = int(os.getenv("MIN_PRICE", "2000"))
MAX_PRICE = int(os.getenv("MAX_PRICE", "5000"))
MAX_RETRIES = int(os.getenv("MAX_RETRIES", "3"))
SCREENSHOT_DIR = Path(os.getenv("SCREENSHOT_DIR", "screenshots"))
LOG_FILE = os.getenv("LOG_FILE", "monitor.log")
ENABLE_NOTIFICATIONS = os.getenv("ENABLE_NOTIFICATIONS", "1") == "1"

QUANTITY = int(os.getenv("QUANTITY", "4"))
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
CHROME_PROFILE_DIR = os.getenv("CHROME_PROFILE_DIR", str(Path.cwd() / "chrome_profile"))

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
    "home", "news", "team", "fixtures", "contact", "about", "faq", "help",
    "privacy", "terms", "cookie", "footer", "header", "menu", "nav",
    "search", "filter", "sort", "share", "follow", "instagram", "facebook",
    "twitter", "youtube", "close", "dismiss", "cancel", "back",
    "rcb tv", "rcb bar", "echo of fans", "more", "shop",
}
# UPI keywords
UPI_KEYWORDS = {"upi", "bhim", "google pay", "phonepe", "paytm", "vpa"}

# ── Login selectors (these stay somewhat hardcoded - login pages are consistent)
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
            logging.FileHandler(LOG_FILE, encoding="utf-8"),
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
    upi_vpa: str = UPI_VPA
    captcha_markers: Tuple[str, ...] = (
        "captcha", "i am human", "verify you are human",
        "security check", "cloudflare",
    )


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

    # ── Public detection methods ──────────────────────────────────────────────

    def find_purchase_button(self) -> Optional[WebElement]:
        """Find the primary purchase / add-to-cart / book CTA on the page."""
        match = self._best_match(PURCHASE_KEYWORDS, min_score=4)
        if match:
            el, score, text = match
            logging.info("Found purchase button (score=%s): '%s'", score, text[:80])
            return el
        return None

    def find_checkout_button(self) -> Optional[WebElement]:
        """Find checkout / proceed / place-order button."""
        match = self._best_match(CHECKOUT_KEYWORDS, min_score=5)
        if match:
            el, score, text = match
            logging.info("Found checkout button (score=%s): '%s'", score, text[:80])
            return el
        return None

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

class WebsiteMonitor:
    """Monitors a page for availability & drives through checkout dynamically."""

    def __init__(self, config: Config) -> None:
        self.config = config
        self.driver: Optional[webdriver.Chrome] = None
        self.wait: Optional[WebDriverWait] = None
        self.short_wait: Optional[WebDriverWait] = None
        self.page: Optional[PageAnalyzer] = None
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

        profile_path = Path(CHROME_PROFILE_DIR).resolve()
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

        self.driver = webdriver.Chrome(
            service=ChromeService(ChromeDriverManager().install()),
            options=options,
        )
        self.driver.set_page_load_timeout(45)
        self.wait = WebDriverWait(self.driver, 15)
        self.short_wait = WebDriverWait(self.driver, 5)
        self.page = PageAnalyzer(self.driver)
        logging.info("Chrome driver initialized")

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _screenshot(self, label: str) -> Path:
        assert self.driver
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        path = self.config.screenshot_dir / f"{ts}-{label}.png"
        self.driver.save_screenshot(str(path))
        logging.info("Screenshot: %s", path)
        return path

    def _wait_ready(self) -> None:
        assert self.driver and self.wait
        self.wait.until(lambda d: d.execute_script("return document.readyState") == "complete")
        self.wait.until(EC.presence_of_element_located((By.TAG_NAME, "body")))

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

    # ── Dynamic availability check (works for ANY page) ──────────────────────

    def _check_available(self) -> Optional[WebElement]:
        """Dynamically find a purchase/book button on the current page."""
        assert self.page
        self.page.log_page_summary()

        # Check for sold-out signals
        if self.page.page_has_text(list(NEGATIVE_KEYWORDS)[:6]):
            logging.info("Negative signals on page (sold out / coming soon)")

        btn = self.page.find_purchase_button()
        if btn:
            text = self._safe_text(btn).lower()
            if any(neg in text for neg in NEGATIVE_KEYWORDS):
                logging.info("Purchase button found but has negative text: '%s'", text)
                return None
            return btn

        logging.info("No purchase action button found on page")
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
        logging.info("Setting quantity to %s", desired)
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
                pick = max((n for n in nums if n <= desired), default=max(nums, default=1))
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

            best = max(groups.values(), key=len)
            for s in best[:desired]:
                try:
                    self._scroll_to(s)
                    self._click(s)
                    selected += 1
                    time.sleep(0.3)
                except WebDriverException:
                    pass

        if selected > 0:
            logging.info("Auto-selected %s/%s seats (together=%s)", selected, desired, selected >= desired)
            self._screenshot("seats-auto-selected")

        if selected < desired:
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

    def _checkout_flow(self, purchase_btn: WebElement) -> None:
        """Universal checkout flow — works for merch, tickets, anything."""
        assert self.driver and self.page
        logging.info("══ Starting checkout flow ══")

        # Step 1: Select product options if any (size, category, color)
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

        # Step 3b: Verify the item was actually added — retry with option fix if popup appears
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
            # Close popup
            for close_xp in (
                ".//button[contains(@class, 'close') or contains(@aria-label, 'Close') or contains(@aria-label, 'close')]",
                ".//button[contains(text(), '\u00d7') or contains(text(), 'OK') or contains(text(), 'ok')]",
                ".//*[contains(@class, 'dismiss') or contains(@class, 'close')]",
            ):
                try:
                    close_btns = popup.find_elements(By.XPATH, close_xp)
                    for cb in close_btns:
                        if cb.is_displayed():
                            self._click(cb)
                            time.sleep(1)
                            break
                except WebDriverException:
                    pass
            time.sleep(1)
            # Re-select all options (sorted by page position — top-to-bottom)
            self._select_product_options()
            time.sleep(1)
            # Re-click purchase
            fresh_btn = self.page.find_purchase_button()
            if fresh_btn:
                self._scroll_to(fresh_btn)
                self._click(fresh_btn)
                logging.info("Re-clicked purchase button (attempt %s)", add_attempt + 1)
                time.sleep(2)
                self._screenshot(f"purchase-retry-{add_attempt + 1}")

        # Step 4: Handle seat map if present
        if self.page.has_seat_map():
            self._handle_seat_map()
            time.sleep(2)
            self._wait_ready()

        # Step 5: Check for cart overlay/drawer that appeared after purchase click
        time.sleep(2)
        overlay = self.page.find_cart_overlay()
        if overlay:
            logging.info("Cart overlay detected — looking for checkout button inside")
            self._screenshot("cart-overlay")
            # Look for checkout/proceed button within the overlay
            try:
                overlay_btns = overlay.find_elements(By.XPATH,
                    ".//button | .//a | .//*[@role='button']")
                for ob in overlay_btns:
                    text = self._safe_text(ob).lower()
                    if any(kw in text for kw in ("checkout", "proceed", "view cart", "view bag", "go to cart", "go to bag")):
                        logging.info("Clicking overlay button: '%s'", text)
                        self._click(ob)
                        time.sleep(3)
                        self._wait_ready()
                        break
            except WebDriverException:
                pass

        # Step 6: Navigate to cart if not already there
        url_lower = self.driver.current_url.lower()
        on_cart_page = any(seg in url_lower for seg in ("/cart", "/bag", "/basket", "/checkout"))
        if not on_cart_page:
            if not self._go_to_cart():
                logging.info("Cart nav failed — continuing with checkout from current page")

        # Step 6b: Check if cart is empty — go back and retry if so
        try:
            body_text = self.driver.find_element(By.TAG_NAME, "body").text.lower()
            if any(kw in body_text for kw in ("bag is empty", "cart is empty", "basket is empty", "no items", "start shopping")):
                logging.warning("Cart is empty! Item wasn't added. Going back to retry.")
                self._screenshot("cart-empty")
                self.driver.get(self.config.target_url)
                self._wait_ready()
                raise RuntimeError("Cart was empty — ADD TO BAG likely failed")
        except RuntimeError:
            raise
        except WebDriverException:
            pass

        # Step 7: Adjust quantity on cart page if possible
        qty_el = self.page.find_quantity_input()
        if qty_el:
            self._set_quantity(self.config.quantity)
        self._screenshot("cart-state")

        # Step 8: Proceed to checkout
        self._proceed_to_checkout()

        # Step 8: Fill any forms (name, email, phone)
        self._fill_forms()

        # Step 9: Try to proceed again (some sites have multi-step checkout)
        # Save the checkout page URL so user can come back to verify
        checkout_url = self.driver.current_url
        self._proceed_to_checkout()

        # Wait for payment gateway (Juspay etc.) to fully load
        time.sleep(4)
        self._wait_ready()
        self._screenshot("payment-gateway-loaded")

        # Step 9b: Open the original checkout/cart in a new tab for user validation
        payment_url = self.driver.current_url
        if payment_url != checkout_url:
            # We've been redirected to a payment gateway — open old page for review
            self.driver.execute_script("window.open(arguments[0], '_blank');", checkout_url)
            logging.info("Opened checkout page in new tab for validation: %s", checkout_url)
            # Stay on the payment tab
            self.driver.switch_to.window(self.driver.window_handles[0])
            time.sleep(1)

        # Step 10: UPI payment
        upi_sent = self._try_upi_payment()

        # Step 11: Send email/WhatsApp notification (stage 2: checkout)
        self._send_notification("checkout")

        # Step 12: Final alert
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
    def _play_siren() -> None:
        """Loud alternating siren — runs for 15 seconds."""
        if os.name != "nt":
            for _ in range(20):
                print("\a", end="", flush=True)
                time.sleep(0.5)
            return
        try:
            import winsound
            winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)
            time.sleep(0.3)
            end = time.monotonic() + 15
            while time.monotonic() < end:
                winsound.Beep(2500, 400)
                winsound.Beep(1000, 400)
                winsound.Beep(3000, 400)
                winsound.Beep(800, 400)
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
            winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)
            for _ in range(5):
                winsound.Beep(2500, 400)
                winsound.Beep(1500, 400)
        except Exception:
            for _ in range(5):
                print("\a", end="", flush=True)
                time.sleep(0.3)

    @staticmethod
    def _play_detection_siren() -> None:
        """Loud siren when availability first detected — runs for 10 seconds."""
        if os.name != "nt":
            for _ in range(15):
                print("\a", end="", flush=True)
                time.sleep(0.3)
            return
        try:
            import winsound
            winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)
            end = time.monotonic() + 10
            while time.monotonic() < end:
                winsound.Beep(3000, 250)
                winsound.Beep(2000, 250)
                winsound.Beep(3500, 250)
                winsound.Beep(1500, 250)
        except Exception:
            for _ in range(15):
                print("\a", end="", flush=True)
                time.sleep(0.3)

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

    def run(self) -> None:
        try:
            self._open_page()
            cycle = 0
            while True:
                cycle += 1
                started = time.monotonic()
                logging.info("── Cycle %s ── %s", cycle, self.config.target_url)
                try:
                    if self._run_cycle(cycle):
                        return
                except Exception as exc:
                    logging.exception("Cycle %s failed: %s", cycle, exc)
                    self._screenshot(f"cycle-{cycle}-error")
                self._sleep(started)
        finally:
            self._teardown()

    def _open_page(self) -> None:
        assert self.driver
        logging.info("Opening %s", self.config.target_url)
        self.driver.get(self.config.target_url)
        self._wait_ready()
        self._screenshot("initial-page")

        if not self._handle_login_wall():
            raise SystemExit(1)

        if self.config.target_url not in self.driver.current_url:
            self.driver.get(self.config.target_url)
            self._wait_ready()
            self._screenshot("target-after-login")

    def _run_cycle(self, cycle: int) -> bool:
        assert self.driver and self.page
        logging.info("Refreshing …")
        self.driver.refresh()
        self._wait_ready()
        self._handle_captcha()

        if self._is_login_page():
            logging.warning("Session expired — re-authenticating")
            if not self._handle_login_wall():
                return False
            self.driver.get(self.config.target_url)
            self._wait_ready()

        # Dynamic availability check — works for any page
        btn = self._check_available()
        if btn:
            self._screenshot(f"cycle-{cycle}-available")
            self._notify_available()
            self._retry_wrapper(self._checkout_flow, btn)
            return True

        self._screenshot(f"cycle-{cycle}-not-available")
        return False

    def _retry_wrapper(self, flow_fn, btn: WebElement) -> None:
        for attempt in range(1, self.config.max_retries + 1):
            try:
                logging.info("Checkout attempt %s/%s", attempt, self.config.max_retries)
                flow_fn(btn)
                return
            except Exception as exc:
                logging.exception("Attempt %s failed: %s", attempt, exc)
                self._screenshot(f"attempt-{attempt}-error")
                if attempt < self.config.max_retries:
                    assert self.driver and self.page
                    self.driver.get(self.config.target_url)
                    self._wait_ready()
                    new_btn = self._check_available()
                    if new_btn:
                        btn = new_btn
                    else:
                        logging.warning("Button gone on retry")
                        return
                else:
                    self._siren_alert(False)

    def _sleep(self, started: float) -> None:
        remaining = max(0.0, self.config.check_interval - (time.monotonic() - started))
        logging.info("Sleeping %.1fs", remaining)
        time.sleep(remaining)

    def _teardown(self) -> None:
        if self.driver:
            self.driver.quit()
            logging.info("Browser closed")


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    setup_logging()
    logging.info("Starting monitor — mode=%s target=%s qty=%s names=%s", MODE, TARGET_URL, QUANTITY, NAMES)
    WebsiteMonitor(Config()).run()
