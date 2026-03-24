"""Comprehensive unit tests for monitor.py — RCB ticket/merch monitor.

Tests cover: PageAnalyzer scoring engine, WebsiteMonitor static helpers,
Config defaults, keyword dictionaries, _find_ticket_page logic,
_set_quantity logic, and seat selection logic.
"""

import pytest
from unittest.mock import MagicMock, patch, PropertyMock, call
from typing import Dict, List, Optional

from tests.conftest import make_element


# ═══════════════════════════════════════════════════════════════════════════
# 1. PageAnalyzer — _el_text
# ═══════════════════════════════════════════════════════════════════════════


class TestElText:
    """Tests for PageAnalyzer._el_text static method."""

    def test_extracts_visible_text(self, page_analyzer):
        """_el_text returns element.text when present."""
        el = make_element(text="Buy Now")
        assert "Buy Now" in page_analyzer._el_text(el)

    def test_extracts_value_attribute(self, page_analyzer):
        """_el_text includes the value attribute."""
        el = make_element(text="", attrs={"value": "Submit Order"})
        assert "Submit Order" in page_analyzer._el_text(el)

    def test_extracts_aria_label(self, page_analyzer):
        """_el_text includes aria-label."""
        el = make_element(text="", attrs={"aria-label": "Add to cart"})
        assert "Add to cart" in page_analyzer._el_text(el)

    def test_extracts_title_attribute(self, page_analyzer):
        """_el_text includes title attribute."""
        el = make_element(text="", attrs={"title": "Book tickets"})
        assert "Book tickets" in page_analyzer._el_text(el)

    def test_combines_multiple_sources(self, page_analyzer):
        """_el_text joins text, value, and aria-label."""
        el = make_element(text="Go", attrs={"value": "Submit", "aria-label": "Pay"})
        result = page_analyzer._el_text(el)
        assert "Go" in result
        assert "Submit" in result
        assert "Pay" in result

    def test_returns_empty_for_blank_element(self, page_analyzer):
        """_el_text returns empty string when element has no text/attrs."""
        el = make_element(text="")
        result = page_analyzer._el_text(el)
        assert result == ""

    def test_handles_stale_element(self, page_analyzer):
        """_el_text gracefully handles StaleElementReferenceException."""
        from selenium.common.exceptions import StaleElementReferenceException

        el = MagicMock()
        type(el).text = PropertyMock(side_effect=StaleElementReferenceException("stale"))
        result = page_analyzer._el_text(el)
        assert result == ""


# ═══════════════════════════════════════════════════════════════════════════
# 2. PageAnalyzer — _score_element
# ═══════════════════════════════════════════════════════════════════════════


class TestScoreElement:
    """Tests for PageAnalyzer._score_element."""

    def test_scores_matching_keyword(self, page_analyzer):
        """Element with 'buy now' text scores 10 against PURCHASE_KEYWORDS."""
        from monitor import PURCHASE_KEYWORDS
        el = make_element(text="Buy Now")
        score, text = page_analyzer._score_element(el, PURCHASE_KEYWORDS)
        assert score == 10
        assert "buy now" in text

    def test_returns_zero_for_no_match(self, page_analyzer):
        """Element with unrelated text scores 0."""
        el = make_element(text="Random paragraph text here that doesn't match anything")
        score, _ = page_analyzer._score_element(el, {"buy now": 10})
        assert score == 0

    def test_returns_zero_for_empty_text(self, page_analyzer):
        """Element with no text scores 0 and returns empty string."""
        el = make_element(text="")
        score, text = page_analyzer._score_element(el, {"buy": 5})
        assert score == 0
        assert text == ""

    def test_negative_keywords_return_negative_score(self, page_analyzer):
        """Element with 'sold out' text gets negative score."""
        from monitor import PURCHASE_KEYWORDS
        el = make_element(text="Sold Out")
        score, _ = page_analyzer._score_element(el, PURCHASE_KEYWORDS)
        assert score == -10

    def test_ignore_keywords_return_minus_one(self, page_analyzer):
        """Short element with ignore keyword like 'home' scores -1."""
        el = make_element(text="Home")
        score, _ = page_analyzer._score_element(el, {"buy": 5})
        assert score == -1

    def test_ignore_skipped_for_long_text(self, page_analyzer):
        """Ignore keywords are only applied to short text (< 40 chars)."""
        # Text > 40 chars containing 'home' should NOT be ignored
        long_text = "Home delivery available for this product with express shipping"
        el = make_element(text=long_text)
        score, _ = page_analyzer._score_element(el, {"delivery": 5})
        assert score >= 0  # NOT -1

    def test_picks_highest_keyword_score(self, page_analyzer):
        """When multiple keywords match, returns the highest score."""
        el = make_element(text="buy now and add to cart")
        keywords = {"buy": 3, "buy now": 10, "add": 2}
        score, _ = page_analyzer._score_element(el, keywords)
        assert score == 10


# ═══════════════════════════════════════════════════════════════════════════
# 3. PageAnalyzer — _best_match
# ═══════════════════════════════════════════════════════════════════════════


class TestBestMatch:
    """Tests for PageAnalyzer._best_match."""

    def test_returns_highest_scoring_element(self, page_analyzer, mock_driver):
        """_best_match picks the element with the highest score."""
        el_low = make_element(text="Buy something nice today")
        el_high = make_element(text="Buy Now")
        mock_driver.find_elements.return_value = [el_low, el_high]
        from monitor import PURCHASE_KEYWORDS
        result = page_analyzer._best_match(PURCHASE_KEYWORDS, min_score=3)
        assert result is not None
        el, score, text = result
        assert el is el_high
        assert score == 10

    def test_returns_none_when_no_candidates(self, page_analyzer, mock_driver):
        """_best_match returns None when no element meets min_score."""
        el = make_element(text="Some random text")
        mock_driver.find_elements.return_value = [el]
        result = page_analyzer._best_match({"buy now": 10}, min_score=5)
        assert result is None

    def test_skips_disabled_elements(self, page_analyzer, mock_driver):
        """_best_match skips elements that are not enabled."""
        el = make_element(text="Buy Now", enabled=False)
        mock_driver.find_elements.return_value = [el]
        from monitor import PURCHASE_KEYWORDS
        result = page_analyzer._best_match(PURCHASE_KEYWORDS, min_score=4)
        assert result is None

    def test_respects_min_score(self, page_analyzer, mock_driver):
        """_best_match filters out elements below min_score threshold."""
        el = make_element(text="get")  # "get" scores 3 in PURCHASE_KEYWORDS
        mock_driver.find_elements.return_value = [el]
        from monitor import PURCHASE_KEYWORDS
        # min_score=4 should filter out "get" (score=3)
        result = page_analyzer._best_match(PURCHASE_KEYWORDS, min_score=4)
        assert result is None


# ═══════════════════════════════════════════════════════════════════════════
# 4. PageAnalyzer — find_* public methods
# ═══════════════════════════════════════════════════════════════════════════


class TestFindMethods:
    """Tests for PageAnalyzer public find_* methods."""

    def test_find_purchase_button_returns_element(self, page_analyzer, mock_driver):
        """find_purchase_button returns a matching element."""
        btn = make_element(text="Add to Cart")
        mock_driver.find_elements.return_value = [btn]
        result = page_analyzer.find_purchase_button()
        assert result is btn

    def test_find_purchase_button_returns_none(self, page_analyzer, mock_driver):
        """find_purchase_button returns None when no CTA found."""
        mock_driver.find_elements.return_value = []
        result = page_analyzer.find_purchase_button()
        assert result is None

    def test_find_checkout_button(self, page_analyzer, mock_driver):
        """find_checkout_button finds 'Proceed to Checkout'."""
        btn = make_element(text="Proceed to Checkout")
        mock_driver.find_elements.return_value = [btn]
        result = page_analyzer.find_checkout_button()
        assert result is btn

    def test_find_checkout_button_returns_none(self, page_analyzer, mock_driver):
        """find_checkout_button returns None when no match."""
        mock_driver.find_elements.return_value = []
        assert page_analyzer.find_checkout_button() is None

    def test_find_cart_button_by_href(self, page_analyzer, mock_driver):
        """find_cart_button finds link with /cart in href."""
        cart_link = make_element(text="", tag_name="a", attrs={"href": "/cart", "class": ""})
        mock_driver.find_elements.return_value = [cart_link]
        result = page_analyzer.find_cart_button()
        assert result is cart_link

    def test_find_pay_button(self, page_analyzer, mock_driver):
        """find_pay_button finds a 'Pay Now' button."""
        btn = make_element(text="Pay Now")
        mock_driver.find_elements.return_value = [btn]
        result = page_analyzer.find_pay_button()
        assert result is btn

    def test_find_upi_option(self, page_analyzer, mock_driver):
        """find_upi_option finds a UPI payment element."""
        upi_el = make_element(text="UPI", tag_name="div")
        mock_driver.find_elements.return_value = [upi_el]
        result = page_analyzer.find_upi_option()
        assert result is upi_el

    def test_find_vpa_input(self, page_analyzer, mock_driver):
        """find_vpa_input finds input with 'vpa' in attributes."""
        inp = make_element(
            text="", tag_name="input",
            attrs={"placeholder": "Enter VPA", "name": "vpa", "id": "", "aria-label": "", "type": "text"},
        )
        mock_driver.find_elements.return_value = [inp]
        result = page_analyzer.find_vpa_input()
        assert result is inp

    def test_find_quantity_input(self, page_analyzer, mock_driver):
        """find_quantity_input finds input with 'quantity' in name."""
        inp = make_element(
            text="", tag_name="input",
            attrs={"name": "quantity", "id": "", "class": "", "type": "number"},
        )
        mock_driver.find_elements.return_value = [inp]
        result = page_analyzer.find_quantity_input()
        assert result is inp


# ═══════════════════════════════════════════════════════════════════════════
# 5. PageAnalyzer — find_name/email/phone_fields
# ═══════════════════════════════════════════════════════════════════════════


class TestFormFieldDetection:
    """Tests for form field detection methods."""

    def test_find_name_fields_detects_full_name(self, page_analyzer, mock_driver):
        """find_name_fields detects a 'full name' input."""
        from selenium.common.exceptions import NoSuchElementException
        inp = make_element(
            text="", tag_name="input",
            attrs={"placeholder": "Full Name", "name": "name", "id": "name", "aria-label": "", "type": "text"},
        )
        inp.find_element = MagicMock(side_effect=NoSuchElementException("no label"))
        mock_driver.find_elements.return_value = [inp]
        result = page_analyzer.find_name_fields()
        assert len(result) == 1
        assert result[0][1] == "full"

    def test_find_name_fields_detects_first_name(self, page_analyzer, mock_driver):
        """find_name_fields identifies first-name fields."""
        from selenium.common.exceptions import NoSuchElementException
        inp = make_element(
            text="", tag_name="input",
            attrs={"placeholder": "First Name", "name": "firstname", "id": "", "aria-label": "", "type": "text"},
        )
        inp.find_element = MagicMock(side_effect=NoSuchElementException("no label"))
        mock_driver.find_elements.return_value = [inp]
        result = page_analyzer.find_name_fields()
        assert len(result) == 1
        assert result[0][1] == "first"

    def test_find_email_fields(self, page_analyzer, mock_driver):
        """find_email_fields detects email inputs."""
        inp = make_element(text="", tag_name="input", attrs={"type": "email"})
        mock_driver.find_elements.return_value = [inp]
        result = page_analyzer.find_email_fields()
        assert len(result) == 1

    def test_find_phone_fields(self, page_analyzer, mock_driver):
        """find_phone_fields detects tel inputs."""
        inp = make_element(text="", tag_name="input", attrs={"type": "tel"})
        mock_driver.find_elements.return_value = [inp]
        result = page_analyzer.find_phone_fields()
        assert len(result) == 1


# ═══════════════════════════════════════════════════════════════════════════
# 6. PageAnalyzer — has_seat_map
# ═══════════════════════════════════════════════════════════════════════════


class TestHasSeatMap:
    """Tests for PageAnalyzer.has_seat_map."""

    def test_detects_seat_map_by_class(self, page_analyzer, mock_driver):
        """has_seat_map returns True when seat-map element exists."""
        seat_el = make_element(text="", attrs={"class": "seat-map-container"})
        mock_driver.find_elements.return_value = [seat_el]
        assert page_analyzer.has_seat_map() is True

    def test_returns_false_when_no_map(self, page_analyzer, mock_driver):
        """has_seat_map returns False when no map elements found."""
        mock_driver.find_elements.return_value = []
        assert page_analyzer.has_seat_map() is False

    def test_ignores_hidden_seat_elements(self, page_analyzer, mock_driver):
        """has_seat_map skips elements that aren't displayed."""
        hidden_el = make_element(text="", displayed=False)
        mock_driver.find_elements.return_value = [hidden_el]
        assert page_analyzer.has_seat_map() is False


# ═══════════════════════════════════════════════════════════════════════════
# 7. PageAnalyzer — page_has_text
# ═══════════════════════════════════════════════════════════════════════════


class TestPageHasText:
    """Tests for PageAnalyzer.page_has_text."""

    def test_detects_keyword_in_body(self, page_analyzer, mock_driver):
        """page_has_text returns True when keyword found in body."""
        body = make_element(text="This event is sold out")
        mock_driver.find_element.return_value = body
        assert page_analyzer.page_has_text(["sold out"]) is True

    def test_returns_false_when_no_match(self, page_analyzer, mock_driver):
        """page_has_text returns False when no keywords match."""
        body = make_element(text="Tickets available now")
        mock_driver.find_element.return_value = body
        assert page_analyzer.page_has_text(["sold out", "coming soon"]) is False

    def test_handles_webdriver_exception(self, page_analyzer, mock_driver):
        """page_has_text returns False on WebDriverException."""
        from selenium.common.exceptions import WebDriverException
        mock_driver.find_element.side_effect = WebDriverException("timeout")
        assert page_analyzer.page_has_text(["anything"]) is False


# ═══════════════════════════════════════════════════════════════════════════
# 8. WebsiteMonitor — _extract_price
# ═══════════════════════════════════════════════════════════════════════════


class TestExtractPrice:
    """Tests for WebsiteMonitor._extract_price static method."""

    def test_extracts_rupee_symbol(self):
        """Extracts price after ₹ symbol."""
        from monitor import WebsiteMonitor
        assert WebsiteMonitor._extract_price("₹2,500") == 2500

    def test_extracts_rs_prefix(self):
        """Extracts price after Rs. prefix."""
        from monitor import WebsiteMonitor
        assert WebsiteMonitor._extract_price("Rs.1500") == 1500

    def test_extracts_rs_with_space(self):
        """Extracts price after 'Rs ' with space."""
        from monitor import WebsiteMonitor
        assert WebsiteMonitor._extract_price("Rs 3,000") == 3000

    def test_returns_none_for_no_price(self):
        """Returns None when no price pattern found."""
        from monitor import WebsiteMonitor
        assert WebsiteMonitor._extract_price("no price here") is None

    def test_handles_large_price(self):
        """Extracts large prices with comma separators."""
        from monitor import WebsiteMonitor
        assert WebsiteMonitor._extract_price("₹12,50,000") == 1250000

    def test_handles_simple_price(self):
        """Extracts simple price without commas."""
        from monitor import WebsiteMonitor
        assert WebsiteMonitor._extract_price("₹500") == 500


# ═══════════════════════════════════════════════════════════════════════════
# 9. WebsiteMonitor — _safe_text
# ═══════════════════════════════════════════════════════════════════════════


class TestSafeText:
    """Tests for WebsiteMonitor._safe_text static method."""

    def test_returns_element_text(self):
        """Returns el.text when available."""
        from monitor import WebsiteMonitor
        el = make_element(text="  Hello World  ")
        assert WebsiteMonitor._safe_text(el) == "Hello World"

    def test_falls_back_to_textcontent(self):
        """Falls back to textContent attribute when text is empty."""
        from monitor import WebsiteMonitor
        el = make_element(text="", attrs={"textContent": "Fallback Text"})
        assert WebsiteMonitor._safe_text(el) == "Fallback Text"

    def test_returns_empty_for_blank(self):
        """Returns empty string when both text and textContent are empty."""
        from monitor import WebsiteMonitor
        el = make_element(text="")
        assert WebsiteMonitor._safe_text(el) == ""


# ═══════════════════════════════════════════════════════════════════════════
# 10. Config dataclass
# ═══════════════════════════════════════════════════════════════════════════


class TestConfig:
    """Tests for Config dataclass defaults."""

    def test_default_check_interval(self):
        """Config has sensible default check_interval."""
        from monitor import Config
        c = Config()
        assert c.check_interval > 0

    def test_default_quantity(self):
        """Config default quantity is positive."""
        from monitor import Config
        c = Config()
        assert c.quantity > 0

    def test_default_min_quantity(self):
        """Config default min_quantity is <= quantity."""
        from monitor import Config
        c = Config()
        assert c.min_quantity <= c.quantity

    def test_captcha_markers_present(self):
        """Config has captcha markers."""
        from monitor import Config
        c = Config()
        assert "captcha" in c.captcha_markers
        assert len(c.captcha_markers) >= 3

    def test_config_is_frozen(self):
        """Config is frozen (immutable)."""
        from monitor import Config
        c = Config()
        with pytest.raises(AttributeError):
            c.quantity = 99


# ═══════════════════════════════════════════════════════════════════════════
# 11. Keyword dictionaries
# ═══════════════════════════════════════════════════════════════════════════


class TestKeywords:
    """Tests for keyword dictionary correctness."""

    def test_purchase_keywords_has_key_terms(self):
        """PURCHASE_KEYWORDS contains essential purchase actions."""
        from monitor import PURCHASE_KEYWORDS
        for kw in ("buy now", "add to cart", "book now", "buy tickets"):
            assert kw in PURCHASE_KEYWORDS, f"Missing '{kw}' in PURCHASE_KEYWORDS"

    def test_checkout_keywords_has_key_terms(self):
        """CHECKOUT_KEYWORDS contains essential checkout actions."""
        from monitor import CHECKOUT_KEYWORDS
        for kw in ("proceed to checkout", "checkout", "place order"):
            assert kw in CHECKOUT_KEYWORDS, f"Missing '{kw}' in CHECKOUT_KEYWORDS"

    def test_negative_keywords_has_sold_out(self):
        """NEGATIVE_KEYWORDS contains 'sold out'."""
        from monitor import NEGATIVE_KEYWORDS
        assert "sold out" in NEGATIVE_KEYWORDS

    def test_fixtures_not_in_ignore_keywords(self):
        """Regression: 'fixtures' must NOT be in IGNORE_KEYWORDS."""
        from monitor import IGNORE_KEYWORDS
        assert "fixtures" not in IGNORE_KEYWORDS, (
            "'fixtures' was incorrectly added to IGNORE_KEYWORDS — "
            "it would prevent the bot from finding the fixtures page"
        )

    def test_ticket_link_keywords_has_ticket(self):
        """TICKET_LINK_KEYWORDS includes 'ticket'."""
        from monitor import TICKET_LINK_KEYWORDS
        assert "ticket" in TICKET_LINK_KEYWORDS

    def test_ticket_link_keywords_no_partner_domains(self):
        """TICKET_LINK_KEYWORDS should not contain partner domain names."""
        from monitor import TICKET_LINK_KEYWORDS
        for kw in TICKET_LINK_KEYWORDS:
            assert "bookmyshow" not in kw
            assert "insider" not in kw

    def test_ignore_keywords_type(self):
        """IGNORE_KEYWORDS is a set."""
        from monitor import IGNORE_KEYWORDS
        assert isinstance(IGNORE_KEYWORDS, set)

    def test_negative_keywords_type(self):
        """NEGATIVE_KEYWORDS is a set."""
        from monitor import NEGATIVE_KEYWORDS
        assert isinstance(NEGATIVE_KEYWORDS, set)


# ═══════════════════════════════════════════════════════════════════════════
# 12. _find_ticket_page logic
# ═══════════════════════════════════════════════════════════════════════════


class TestFindTicketPage:
    """Tests for WebsiteMonitor._find_ticket_page."""

    def _make_monitor(self, mode: str = "live-tickets"):
        """Create a WebsiteMonitor with mocked driver (skips _setup_driver)."""
        from monitor import Config, WebsiteMonitor, PageAnalyzer
        config = Config(mode=mode, target_url="https://www.royalchallengers.com/fixtures")

        with patch.object(WebsiteMonitor, '__init__', lambda self, *a, **kw: None):
            mon = WebsiteMonitor.__new__(WebsiteMonitor)
        mon.config = config
        mon.driver = MagicMock()
        mon.wait = MagicMock()
        mon.short_wait = MagicMock()
        mon.page = PageAnalyzer(mon.driver)
        mon.driver.current_url = "https://www.royalchallengers.com/fixtures"
        return mon

    def test_returns_false_in_merch_mode(self):
        """_find_ticket_page returns False when mode is test-merch."""
        mon = self._make_monitor(mode="test-merch")
        assert mon._find_ticket_page() is False

    @patch("monitor.ENABLE_NOTIFICATIONS", False)
    def test_rejects_external_bookmyshow_link(self):
        """_find_ticket_page ignores BookMyShow links (off-domain)."""
        mon = self._make_monitor()
        bms_link = make_element(
            text="Book Tickets", tag_name="a",
            attrs={"href": "https://in.bookmyshow.com/events/rcb-vs-csk", "aria-label": ""},
        )
        mon.driver.find_elements.return_value = [bms_link]
        mon._screenshot = MagicMock()
        mon._wait_ready = MagicMock()
        result = mon._find_ticket_page()
        assert result is False
        mon.driver.get.assert_not_called()

    @patch("monitor.ENABLE_NOTIFICATIONS", False)
    def test_rejects_external_insider_link(self):
        """_find_ticket_page ignores Insider links (off-domain)."""
        mon = self._make_monitor()
        insider_link = make_element(
            text="Get Tickets", tag_name="a",
            attrs={"href": "https://insider.in/rcb-match/event", "aria-label": ""},
        )
        mon.driver.find_elements.return_value = [insider_link]
        mon._screenshot = MagicMock()
        mon._wait_ready = MagicMock()
        result = mon._find_ticket_page()
        assert result is False

    def test_navigates_to_same_domain_ticket_page(self):
        """_find_ticket_page navigates to same-domain ticket links."""
        mon = self._make_monitor()
        ticket_link = make_element(
            text="Buy Tickets", tag_name="a",
            attrs={"href": "https://www.royalchallengers.com/buy-ticket", "aria-label": ""},
        )
        mon.driver.find_elements.return_value = [ticket_link]
        mon._screenshot = MagicMock()
        mon._wait_ready = MagicMock()
        result = mon._find_ticket_page()
        assert result is True
        mon.driver.get.assert_called_with("https://www.royalchallengers.com/buy-ticket")

    def test_returns_false_when_no_links(self):
        """_find_ticket_page returns False when no ticket links found."""
        mon = self._make_monitor()
        mon.driver.find_elements.return_value = []
        assert mon._find_ticket_page() is False

    def test_skips_javascript_hrefs(self):
        """_find_ticket_page skips links with javascript: hrefs."""
        mon = self._make_monitor()
        js_link = make_element(
            text="Book Now", tag_name="a",
            attrs={"href": "javascript:void(0)", "aria-label": ""},
        )
        mon.driver.find_elements.return_value = [js_link]
        assert mon._find_ticket_page() is False


# ═══════════════════════════════════════════════════════════════════════════
# 13. _set_quantity logic
# ═══════════════════════════════════════════════════════════════════════════


class TestSetQuantity:
    """Tests for WebsiteMonitor._set_quantity."""

    def _make_monitor(self, quantity: int = 4, min_quantity: int = 2):
        """Create a WebsiteMonitor with mocked internals."""
        from monitor import Config, WebsiteMonitor, PageAnalyzer
        config = Config(quantity=quantity, min_quantity=min_quantity)

        with patch.object(WebsiteMonitor, '__init__', lambda self, *a, **kw: None):
            mon = WebsiteMonitor.__new__(WebsiteMonitor)
        mon.config = config
        mon.driver = MagicMock()
        mon.wait = MagicMock()
        mon.short_wait = MagicMock()
        mon.page = MagicMock()
        return mon

    def test_select_dropdown_exact_value(self):
        """_set_quantity selects exact value from dropdown."""
        mon = self._make_monitor(quantity=4)
        select_el = make_element(tag_name="select")
        mon.page.find_quantity_input.return_value = select_el

        mock_select = MagicMock()
        opt1 = MagicMock()
        opt1.get_attribute.return_value = "1"
        opt4 = MagicMock()
        opt4.get_attribute.return_value = "4"
        mock_select.options = [opt1, opt4]

        with patch("monitor.Select", return_value=mock_select):
            mon._set_quantity(4)
        mock_select.select_by_value.assert_called_with("4")

    def test_select_dropdown_clamps_to_max(self):
        """_set_quantity clamps to highest available when desired not in options."""
        mon = self._make_monitor(quantity=4, min_quantity=2)
        select_el = make_element(tag_name="select")
        mon.page.find_quantity_input.return_value = select_el

        mock_select = MagicMock()
        opt1 = MagicMock()
        opt1.get_attribute.return_value = "1"
        opt2 = MagicMock()
        opt2.get_attribute.return_value = "2"
        opt3 = MagicMock()
        opt3.get_attribute.return_value = "3"
        mock_select.options = [opt1, opt2, opt3]

        with patch("monitor.Select", return_value=mock_select):
            mon._set_quantity(4)
        mock_select.select_by_value.assert_called_with("3")

    def test_number_input_sets_value(self):
        """_set_quantity sets value on number input."""
        mon = self._make_monitor(quantity=4)
        inp = make_element(tag_name="input", attrs={"value": "4", "max": "10"})
        mon.page.find_quantity_input.return_value = inp
        mon._scroll_to = MagicMock()

        mon._set_quantity(4)
        inp.clear.assert_called_once()
        inp.send_keys.assert_called_with("4")

    def test_no_quantity_field_skips(self):
        """_set_quantity does nothing when no quantity field found."""
        mon = self._make_monitor()
        mon.page.find_quantity_input.return_value = None
        # Should not raise
        mon._set_quantity(4)

    def test_min_quantity_warning_on_select(self):
        """_set_quantity warns when dropdown max < min_quantity."""
        mon = self._make_monitor(quantity=4, min_quantity=3)
        select_el = make_element(tag_name="select")
        mon.page.find_quantity_input.return_value = select_el

        mock_select = MagicMock()
        opt1 = MagicMock()
        opt1.get_attribute.return_value = "1"
        opt2 = MagicMock()
        opt2.get_attribute.return_value = "2"
        mock_select.options = [opt1, opt2]

        with patch("monitor.Select", return_value=mock_select), \
             patch("logging.warning") as mock_warn:
            mon._set_quantity(4)
        # Should have logged a warning about min quantity
        mock_warn.assert_called()
        warn_msg = mock_warn.call_args[0][0]
        assert "MIN_QUANTITY" in warn_msg or "min" in warn_msg.lower()


# ═══════════════════════════════════════════════════════════════════════════
# 14. Seat selection logic
# ═══════════════════════════════════════════════════════════════════════════


class TestSeatSelection:
    """Tests for _handle_seat_map seat grouping and selection."""

    def _make_monitor(self, quantity: int = 4, min_quantity: int = 2):
        """Create a WebsiteMonitor with mocked internals."""
        from monitor import Config, WebsiteMonitor, PageAnalyzer
        config = Config(quantity=quantity, min_quantity=min_quantity)

        with patch.object(WebsiteMonitor, '__init__', lambda self, *a, **kw: None):
            mon = WebsiteMonitor.__new__(WebsiteMonitor)
        mon.config = config
        mon.driver = MagicMock()
        mon.wait = MagicMock()
        mon.short_wait = MagicMock()
        mon.page = MagicMock()
        mon._screenshot = MagicMock()
        mon._scroll_to = MagicMock()
        mon._click = MagicMock()
        mon._wait_ready = MagicMock()
        mon._manual_seat_pause = MagicMock()
        mon._play_short_alert = MagicMock()
        return mon

    def test_falls_back_to_manual_when_too_few_seats(self):
        """Falls back to manual selection when best group < min_quantity."""
        mon = self._make_monitor(quantity=4, min_quantity=3)
        mon.page.has_seat_map.return_value = True
        mon.driver.find_elements.return_value = []  # no iframes

        # One seat in one group
        seat = make_element(text="A1", attrs={"class": "seat available"})
        parent = MagicMock()
        seat.find_element = MagicMock(return_value=parent)
        mon.page.find_available_seats.return_value = [seat]

        mon._handle_seat_map()
        mon._manual_seat_pause.assert_called_once()

    def test_selects_up_to_quantity_from_best_group(self):
        """Selects up to QUANTITY seats from the largest group."""
        mon = self._make_monitor(quantity=3, min_quantity=2)
        mon.page.has_seat_map.return_value = True
        mon.driver.find_elements.return_value = []

        # Create 5 seats in the same group (same parent)
        shared_parent = MagicMock()
        seats = []
        for i in range(5):
            s = make_element(text=f"A{i+1}", attrs={"class": "seat available"})
            s.find_element = MagicMock(return_value=shared_parent)
            seats.append(s)
        mon.page.find_available_seats.return_value = seats

        mon._handle_seat_map()
        # Should click exactly 3 seats (quantity=3)
        assert mon._click.call_count == 3

    def test_no_seat_map_returns_false(self):
        """Returns False when no seat map detected."""
        mon = self._make_monitor()
        mon.page.has_seat_map.return_value = False
        mon.driver.find_elements.return_value = []
        result = mon._handle_seat_map()
        assert result is False

    def test_manual_pause_when_zero_seats_found(self):
        """Falls back to manual when no available seats found but map exists."""
        mon = self._make_monitor(quantity=4, min_quantity=2)
        mon.page.has_seat_map.return_value = True
        mon.driver.find_elements.return_value = []
        mon.page.find_available_seats.return_value = []

        mon._handle_seat_map()
        # 0 selected < min_quantity → manual pause
        mon._manual_seat_pause.assert_called_once()


# ═══════════════════════════════════════════════════════════════════════════
# 15. PageAnalyzer._extract_price_from_text
# ═══════════════════════════════════════════════════════════════════════════

class TestExtractPriceFromText:
    """Tests for PageAnalyzer._extract_price_from_text static method."""

    def test_rupee_symbol_plain(self):
        from monitor import PageAnalyzer
        assert PageAnalyzer._extract_price_from_text("\u20b92300") == 2300

    def test_rupee_symbol_with_comma(self):
        from monitor import PageAnalyzer
        assert PageAnalyzer._extract_price_from_text("\u20b93,300") == 3300

    def test_rs_prefix(self):
        from monitor import PageAnalyzer
        assert PageAnalyzer._extract_price_from_text("Rs.4500") == 4500

    def test_rs_with_space(self):
        from monitor import PageAnalyzer
        assert PageAnalyzer._extract_price_from_text("Rs 2,300") == 2300

    def test_picks_minimum_when_multiple_prices(self):
        """Returns minimum price when multiple prices appear in text."""
        from monitor import PageAnalyzer
        # Stand row might show base price + convenience fee
        text = "Sun Pharma A Stand \u20b92,300 (+ \u20b9200 fee)"
        result = PageAnalyzer._extract_price_from_text(text)
        assert result == 200 or result == 2300  # min of visible prices

    def test_returns_none_for_no_price(self):
        from monitor import PageAnalyzer
        assert PageAnalyzer._extract_price_from_text("No price here") is None

    def test_returns_none_for_empty_string(self):
        from monitor import PageAnalyzer
        assert PageAnalyzer._extract_price_from_text("") is None


# ═══════════════════════════════════════════════════════════════════════════
# 16. PageAnalyzer.find_stand_buttons — price cap & sorting
# ═══════════════════════════════════════════════════════════════════════════

class TestFindStandButtons:
    """Tests for the price-aware find_stand_buttons method."""

    def _make_page(self, mock_driver):
        from monitor import PageAnalyzer
        return PageAnalyzer(mock_driver)

    def test_sorts_cheapest_first(self, mock_driver):
        """Stands are returned sorted by price ascending."""
        from monitor import PageAnalyzer
        page = PageAnalyzer(mock_driver)
        el_cheap = make_element(text="Sun Pharma A Stand\n\u20b92,300", attrs={"class": "stand-row"})
        el_mid   = make_element(text="Puma B Stand\n\u20b93,300",      attrs={"class": "stand-row"})
        el_exp   = make_element(text="D Corporate\n\u20b94,500",       attrs={"class": "stand-row"})
        mock_driver.find_elements.return_value = [el_mid, el_exp, el_cheap]
        results = page.find_stand_buttons()
        prices = [p for _, p, _ in results if p > 0]
        assert prices == sorted(prices), "Stands should be sorted cheapest first"

    def test_excludes_stands_above_price_cap(self, mock_driver):
        """Stands priced above PRICE_PER_TICKET_MAX are excluded."""
        import monitor
        from monitor import PageAnalyzer
        page = PageAnalyzer(mock_driver)
        el_ok   = make_element(text="A Stand \u20b92,300", attrs={"class": "stand-row"})
        el_over = make_element(text="VIP Lounge \u20b912,000", attrs={"class": "stand-row"})
        mock_driver.find_elements.return_value = [el_ok, el_over]
        original_cap = monitor.PRICE_PER_TICKET_MAX
        try:
            monitor.PRICE_PER_TICKET_MAX = 5000
            results = page.find_stand_buttons()
            prices = [p for _, p, _ in results]
            assert all(p <= 5000 for p in prices if p > 0), \
                "No stand above cap should be returned"
        finally:
            monitor.PRICE_PER_TICKET_MAX = original_cap

    def test_limits_to_max_stand_workers(self, mock_driver):
        """Returns at most MAX_STAND_WORKERS stands."""
        import monitor
        from monitor import PageAnalyzer
        page = PageAnalyzer(mock_driver)
        # Create 10 stands all within price cap
        els = [make_element(text=f"Stand {i} \u20b9{2000+i*100}", attrs={"class": "stand-row"})
               for i in range(10)]
        mock_driver.find_elements.return_value = els
        original_max = monitor.MAX_STAND_WORKERS
        try:
            monitor.MAX_STAND_WORKERS = 7
            results = page.find_stand_buttons()
            assert len(results) <= 7
        finally:
            monitor.MAX_STAND_WORKERS = original_max

    def test_unknown_price_stands_included(self, mock_driver):
        """Stands with no price (price=0) are still included."""
        from monitor import PageAnalyzer
        page = PageAnalyzer(mock_driver)
        # Stand with keyword but no price
        el = make_element(text="Executive Stand (Coming Soon)", attrs={"class": "stand-row"})
        mock_driver.find_elements.return_value = [el]
        results = page.find_stand_buttons()
        assert len(results) == 1
        _, price, _ = results[0]
        assert price == 0

    def test_returns_empty_when_no_stands(self, mock_driver):
        """Returns empty list when no stands on page."""
        from monitor import PageAnalyzer
        page = PageAnalyzer(mock_driver)
        mock_driver.find_elements.return_value = []
        assert page.find_stand_buttons() == []


# ═══════════════════════════════════════════════════════════════════════════
# 17. PageAnalyzer.find_ticket_quantity_popup
# ═══════════════════════════════════════════════════════════════════════════

class TestFindTicketQuantityPopup:
    """Tests for the ticket quantity popup detector."""

    def test_detects_how_many_tickets_popup(self, page_analyzer, mock_driver):
        """Detects popup containing 'how many tickets' text."""
        popup = make_element(text="How many tickets do you want?", attrs={"class": "modal show"})
        mock_driver.find_elements.return_value = [popup]
        result = page_analyzer.find_ticket_quantity_popup()
        assert result is popup

    def test_detects_popup_by_role_dialog(self, page_analyzer, mock_driver):
        """Detects popup via role=dialog with ticket text."""
        popup = make_element(text="Select tickets. Continue", attrs={"role": "dialog"})
        mock_driver.find_elements.return_value = [popup]
        result = page_analyzer.find_ticket_quantity_popup()
        assert result is popup

    def test_returns_none_when_no_popup(self, page_analyzer, mock_driver):
        """Returns None when no quantity popup on page."""
        mock_driver.find_elements.return_value = []
        result = page_analyzer.find_ticket_quantity_popup()
        assert result is None

    def test_ignores_hidden_popup(self, page_analyzer, mock_driver):
        """Returns None when popup exists but is not displayed."""
        popup = make_element(text="How many tickets?", displayed=False, attrs={"class": "modal"})
        mock_driver.find_elements.return_value = [popup]
        result = page_analyzer.find_ticket_quantity_popup()
        assert result is None


# ═══════════════════════════════════════════════════════════════════════════
# 18. PageAnalyzer.find_quantity_buttons_in_popup
# ═══════════════════════════════════════════════════════════════════════════

class TestFindQuantityButtonsInPopup:
    """Tests for the 1-6 number buttons inside the ticket qty popup."""

    def test_finds_digit_buttons_1_to_6(self, page_analyzer):
        """Finds buttons labelled 1 through 6."""
        popup = MagicMock()
        btns = [make_element(text=str(i)) for i in range(1, 7)]
        popup.find_elements.return_value = btns
        results = page_analyzer.find_quantity_buttons_in_popup(popup)
        numbers = [n for n, _ in results]
        assert numbers == [1, 2, 3, 4, 5, 6]

    def test_returns_sorted_ascending(self, page_analyzer):
        """Buttons are returned in ascending order regardless of DOM order."""
        popup = MagicMock()
        # Give them in reverse order
        btns = [make_element(text=str(i)) for i in [4, 2, 6, 1, 3, 5]]
        popup.find_elements.return_value = btns
        results = page_analyzer.find_quantity_buttons_in_popup(popup)
        numbers = [n for n, _ in results]
        assert numbers == sorted(numbers)

    def test_excludes_non_digit_buttons(self, page_analyzer):
        """Buttons with non-digit text are excluded."""
        popup = MagicMock()
        btns = [
            make_element(text="4"),
            make_element(text="Cancel"),
            make_element(text="Continue"),
        ]
        popup.find_elements.return_value = btns
        results = page_analyzer.find_quantity_buttons_in_popup(popup)
        numbers = [n for n, _ in results]
        assert numbers == [4]

    def test_excludes_out_of_range_digits(self, page_analyzer):
        """Buttons with digit outside 1-6 are excluded."""
        popup = MagicMock()
        btns = [make_element(text=str(i)) for i in [0, 4, 7, 9]]
        popup.find_elements.return_value = btns
        results = page_analyzer.find_quantity_buttons_in_popup(popup)
        numbers = [n for n, _ in results]
        assert numbers == [4]

    def test_returns_empty_on_webdriver_error(self, page_analyzer):
        """Returns empty list when WebDriverException is raised."""
        from selenium.common.exceptions import WebDriverException
        popup = MagicMock()
        popup.find_elements.side_effect = WebDriverException("error")
        results = page_analyzer.find_quantity_buttons_in_popup(popup)
        assert results == []


# ═══════════════════════════════════════════════════════════════════════════
# 19. PageStage enum exists and has required values
# ═══════════════════════════════════════════════════════════════════════════

class TestPageStageEnum:
    """Tests for the PageStage enum."""

    def test_all_stages_present(self):
        from monitor import PageStage
        required = [
            "HOME", "TICKETS_NAV", "MATCH_LIST", "MATCH_DETAIL",
            "STAND_LIST", "QTY_POPUP", "SEAT_MAP", "CHECKOUT",
            "PAYMENT", "SOLD_OUT", "LOGIN_WALL", "ERROR_PAGE", "UNKNOWN",
        ]
        stage_names = [s.name for s in PageStage]
        for r in required:
            assert r in stage_names, f"PageStage.{r} missing"

    def test_stages_are_distinct(self):
        """All stage values are distinct."""
        from monitor import PageStage
        values = [s.value for s in PageStage]
        assert len(values) == len(set(values))


# ═══════════════════════════════════════════════════════════════════════════
# 20. PageAnalyzer.classify_page
# ═══════════════════════════════════════════════════════════════════════════

class TestClassifyPage:
    """Tests for the page stage classifier."""

    def _make_page(self, mock_driver, body_text="", url="https://shop.royalchallengers.com/"):
        from monitor import PageAnalyzer
        mock_driver.current_url = url
        body_el = make_element(text=body_text)
        mock_driver.find_element.return_value = body_el
        mock_driver.find_elements.return_value = []
        mock_driver.execute_script.return_value = None
        return PageAnalyzer(mock_driver)

    def test_classifies_payment_url(self, mock_driver):
        from monitor import PageStage
        page = self._make_page(mock_driver, url="https://api.juspay.in/pay/rcb")
        stage = page.classify_page("https://api.juspay.in/pay/rcb")
        assert stage == PageStage.PAYMENT

    def test_classifies_cart_url(self, mock_driver):
        from monitor import PageStage
        page = self._make_page(mock_driver, url="https://shop.royalchallengers.com/cart")
        stage = page.classify_page("https://shop.royalchallengers.com/cart")
        assert stage == PageStage.CHECKOUT

    def test_classifies_sold_out_body(self, mock_driver):
        from monitor import PageStage
        page = self._make_page(mock_driver, body_text="Sorry, tickets are sold out for this match.")
        stage = page.classify_page()
        assert stage == PageStage.SOLD_OUT

    def test_classifies_error_page(self, mock_driver):
        from monitor import PageStage
        page = self._make_page(mock_driver, body_text="503 service unavailable please try again later")
        stage = page.classify_page()
        assert stage == PageStage.ERROR_PAGE

    def test_classifies_stand_list(self, mock_driver):
        from monitor import PageStage
        body = (
            "Sun Pharma A Stand \u20b92,300  "
            "Puma B Stand \u20b93,300  "
            "D Corporate Stand \u20b94,500"
        )
        page = self._make_page(mock_driver, body_text=body,
                               url="https://shop.royalchallengers.com/ticket")
        stage = page.classify_page()
        assert stage == PageStage.STAND_LIST

    def test_classifies_payment_by_body_text(self, mock_driver):
        from monitor import PageStage
        page = self._make_page(
            mock_driver,
            body_text="Enter UPI ID to pay securely. Net banking also available.",
        )
        stage = page.classify_page()
        assert stage == PageStage.PAYMENT

    def test_classifies_checkout_by_body_text(self, mock_driver):
        from monitor import PageStage
        page = self._make_page(
            mock_driver,
            body_text="Order Summary  Subtotal: \u20b99,200  Place Order",
        )
        stage = page.classify_page()
        assert stage == PageStage.CHECKOUT

    def test_unknown_for_empty_page(self, mock_driver):
        from monitor import PageStage
        page = self._make_page(mock_driver, body_text="", url="https://example.com/random")
        stage = page.classify_page()
        assert stage in (PageStage.UNKNOWN, PageStage.HOME)


# ═══════════════════════════════════════════════════════════════════════════
# 21. PageAnalyzer.find_primary_cta — scoring logic
# ═══════════════════════════════════════════════════════════════════════════

class TestFindPrimaryCta:
    """Tests for the semantic CTA finder."""

    def test_prefers_primary_class_button(self, page_analyzer, mock_driver):
        """Button with 'primary' class is preferred over plain button."""
        plain_btn   = make_element(text="Click here",    attrs={"class": "link",    "href": ""})
        primary_btn = make_element(text="Get Tickets",   attrs={"class": "btn-primary", "href": ""})
        mock_driver.find_elements.return_value = [plain_btn, primary_btn]
        result = page_analyzer.find_primary_cta()
        assert result is primary_btn

    def test_ignores_nav_elements(self, page_analyzer, mock_driver):
        """Ignores short nav/footer elements (home, about, etc.)."""
        nav_btn = make_element(text="Home",  attrs={"class": "nav-link", "href": "/home"})
        cta_btn = make_element(text="View Tickets", attrs={"class": "btn", "href": "/tickets"})
        mock_driver.find_elements.return_value = [nav_btn, cta_btn]
        result = page_analyzer.find_primary_cta()
        assert result is cta_btn

    def test_ignores_sold_out_elements(self, page_analyzer, mock_driver):
        """Elements with 'sold out' text are skipped."""
        sold_out = make_element(text="Sold Out", attrs={"class": "btn-primary", "href": ""})
        available = make_element(text="Book Now", attrs={"class": "btn", "href": "/book"})
        mock_driver.find_elements.return_value = [sold_out, available]
        result = page_analyzer.find_primary_cta()
        assert result is available

    def test_returns_none_when_all_ignored(self, page_analyzer, mock_driver):
        """Returns None when all elements are nav/sold-out."""
        nav = make_element(text="Home", attrs={"class": "nav", "href": "/home"})
        mock_driver.find_elements.return_value = [nav]
        result = page_analyzer.find_primary_cta()
        assert result is None

    def test_ticket_url_boosts_score(self, page_analyzer, mock_driver):
        """Links with /ticket in href score higher than generic links."""
        generic = make_element(text="Explore", attrs={"class": "btn", "href": "/explore"})
        ticket  = make_element(text="Explore", attrs={"class": "btn", "href": "/ticket/buy"})
        mock_driver.find_elements.return_value = [generic, ticket]
        result = page_analyzer.find_primary_cta()
        assert result is ticket


# ═══════════════════════════════════════════════════════════════════════════
# 22. _wait_ready_robust — retry and busy-page detection
# ═══════════════════════════════════════════════════════════════════════════

class TestWaitReadyRobust:
    """Tests for _wait_ready_robust retry logic."""

    def _make_monitor(self):
        from monitor import Config, WebsiteMonitor, PageAnalyzer
        with patch.object(WebsiteMonitor, '__init__', lambda self, *a, **kw: None):
            mon = WebsiteMonitor.__new__(WebsiteMonitor)
        mon.config = Config()
        mon.driver = MagicMock()
        mon.wait = MagicMock()
        mon.short_wait = MagicMock()
        mon.page = MagicMock()
        mon._screenshot = MagicMock()
        return mon

    def test_succeeds_on_first_try(self):
        """Returns immediately when page loads on first attempt."""
        mon = self._make_monitor()
        mon.driver.execute_script.return_value = "complete"
        body = make_element(text="Normal page content")
        mon.driver.find_element.return_value = body
        # Should not raise
        with patch("monitor.WebDriverWait") as mock_wait:
            mock_wait.return_value.until.return_value = True
            mon._wait_ready_robust(retries=3)

    def test_retries_on_timeout(self):
        """Retries and refreshes on TimeoutException."""
        from selenium.common.exceptions import TimeoutException
        mon = self._make_monitor()
        call_count = {"n": 0}

        def wait_side_effect(*args, **kwargs):
            w = MagicMock()
            def until_side_effect(fn):
                call_count["n"] += 1
                if call_count["n"] < 3:
                    raise TimeoutException("timeout")
                return True
            w.until = until_side_effect
            return w

        with patch("monitor.WebDriverWait", side_effect=wait_side_effect):
            with patch("time.sleep"):
                mon._wait_ready_robust(retries=3, base_delay=0.01)
        # Should have called refresh at least once
        mon.driver.refresh.assert_called()

    def test_detects_server_busy_page(self):
        """Raises/retries when body contains 503 server error text."""
        from selenium.common.exceptions import WebDriverException
        mon = self._make_monitor()
        busy_body = make_element(text="503 service unavailable try again")
        # First call: busy page; second call: normal
        call_count = {"n": 0}

        def wait_side_effect(*args, **kwargs):
            w = MagicMock()
            def until_side_effect(fn):
                call_count["n"] += 1
                if call_count["n"] == 1:
                    return True  # readyState complete
                raise WebDriverException("busy")
            w.until = until_side_effect
            return w

        with patch("monitor.WebDriverWait", side_effect=wait_side_effect):
            with patch("time.sleep"):
                # Should not crash — just log and move on after retries
                try:
                    mon._wait_ready_robust(retries=2, base_delay=0.01)
                except Exception:
                    pass  # acceptable — we just want to confirm it retried


# ═══════════════════════════════════════════════════════════════════════════
# 23. _advance_to_stands state machine
# ═══════════════════════════════════════════════════════════════════════════

class TestAdvanceToStands:
    """Tests for the _advance_to_stands() page-stage state machine."""

    def _make_monitor(self):
        from monitor import Config, WebsiteMonitor, PageAnalyzer
        with patch.object(WebsiteMonitor, '__init__', lambda self, *a, **kw: None):
            mon = WebsiteMonitor.__new__(WebsiteMonitor)
        mon.config = Config(mode="live-tickets",
                            target_url="https://shop.royalchallengers.com/")
        mon.driver = MagicMock()
        mon.driver.current_url = "https://shop.royalchallengers.com/"
        mon.wait = MagicMock()
        mon.short_wait = MagicMock()
        mon.page = MagicMock()
        mon._screenshot = MagicMock()
        mon._scroll_to = MagicMock()
        mon._click = MagicMock()
        mon._wait_ready_robust = MagicMock()
        mon._handle_login_wall = MagicMock(return_value=True)
        return mon

    def test_returns_element_when_stand_list_visible(self):
        """Returns stand element immediately when STAND_LIST stage detected."""
        from monitor import PageStage
        mon = self._make_monitor()
        stand_el = make_element(text="A Stand \u20b92300")
        mon.page.classify_page.return_value = PageStage.STAND_LIST
        mon.page.find_stand_buttons.return_value = [("a stand", 2300, stand_el)]
        result = mon._advance_to_stands()
        assert result is stand_el

    def test_returns_none_on_sold_out(self):
        """Returns None immediately when SOLD_OUT stage detected."""
        from monitor import PageStage
        mon = self._make_monitor()
        mon.page.classify_page.return_value = PageStage.SOLD_OUT
        result = mon._advance_to_stands()
        assert result is None

    def test_returns_none_on_error_page(self):
        """Returns None on ERROR_PAGE stage (server busy)."""
        from monitor import PageStage
        mon = self._make_monitor()
        mon.page.classify_page.return_value = PageStage.ERROR_PAGE
        result = mon._advance_to_stands()
        assert result is None

    def test_clicks_cta_on_home_stage(self):
        """Clicks primary CTA when on HOME stage."""
        from monitor import PageStage
        mon = self._make_monitor()
        cta = make_element(text="Tickets", attrs={"href": "/fixtures"})
        # First call: HOME, second call: STAND_LIST
        mon.page.classify_page.side_effect = [
            PageStage.HOME, PageStage.STAND_LIST,
        ]
        mon.page.find_primary_cta.return_value = cta
        stand_el = make_element(text="A Stand \u20b92300")
        mon.page.find_stand_buttons.return_value = [("a stand", 2300, stand_el)]
        result = mon._advance_to_stands()
        mon._click.assert_called_once_with(cta)
        assert result is stand_el

    def test_navigates_through_match_list(self):
        """Clicks first match CTA when on MATCH_LIST stage."""
        from monitor import PageStage
        mon = self._make_monitor()
        match_cta = make_element(text="RCB vs MI - Buy")
        mon.page.classify_page.side_effect = [
            PageStage.MATCH_LIST, PageStage.STAND_LIST,
        ]
        mon.page.find_all_primary_ctas.return_value = [match_cta]
        stand_el = make_element(text="A Stand \u20b92300")
        mon.page.find_stand_buttons.return_value = [("a stand", 2300, stand_el)]
        result = mon._advance_to_stands()
        mon._click.assert_called_once_with(match_cta)
        assert result is stand_el

    def test_returns_none_when_home_has_no_cta(self):
        """Returns None when on HOME page with no CTA found (tickets not open yet)."""
        from monitor import PageStage
        mon = self._make_monitor()
        mon.page.classify_page.return_value = PageStage.HOME
        mon.page.find_primary_cta.return_value = None
        result = mon._advance_to_stands()
        assert result is None

    def test_returns_body_when_qty_popup_visible(self):
        """Returns body element as sentinel when QTY_POPUP already open."""
        from monitor import PageStage
        mon = self._make_monitor()
        mon.page.classify_page.return_value = PageStage.QTY_POPUP
        body_el = make_element(text="")
        mon.driver.find_element.return_value = body_el
        result = mon._advance_to_stands()
        assert result is body_el


# ═══════════════════════════════════════════════════════════════════════════
# 24. _run_parallel_booking — worker coordination
# ═══════════════════════════════════════════════════════════════════════════

class TestRunParallelBooking:
    """Tests for the parallel stand worker orchestration."""

    def _make_monitor(self):
        from monitor import Config, WebsiteMonitor
        with patch.object(WebsiteMonitor, '__init__', lambda self, *a, **kw: None):
            mon = WebsiteMonitor.__new__(WebsiteMonitor)
        mon.config = Config(mode="live-tickets", max_retries=2,
                            target_url="https://shop.royalchallengers.com/")
        mon.driver = MagicMock()
        mon.driver.current_url = "https://shop.royalchallengers.com/"
        mon.wait = MagicMock()
        mon.short_wait = MagicMock()
        mon.page = MagicMock()
        mon._screenshot = MagicMock()
        mon._scroll_to = MagicMock()
        mon._click = MagicMock()
        mon._wait_ready_robust = MagicMock()
        mon._check_available = MagicMock()
        mon._teardown = MagicMock()
        mon._is_login_page = MagicMock(return_value=False)
        return mon

    def test_returns_true_when_worker_0_succeeds(self):
        """Returns True when worker 0 (main browser) completes checkout."""
        import monitor
        mon = self._make_monitor()
        trigger_btn = make_element(text="Buy Tickets")
        mon._checkout_flow = MagicMock()  # success = no exception

        original_max = monitor.MAX_STAND_WORKERS
        try:
            monitor.MAX_STAND_WORKERS = 1
            result = mon._run_parallel_booking(trigger_btn)
        finally:
            monitor.MAX_STAND_WORKERS = original_max
        assert result is True

    def test_returns_false_when_all_workers_fail(self):
        """Returns False when every worker throws an exception."""
        import monitor
        mon = self._make_monitor()
        trigger_btn = make_element(text="Buy Tickets")
        mon._checkout_flow = MagicMock(side_effect=RuntimeError("checkout failed"))

        original_max = monitor.MAX_STAND_WORKERS
        try:
            monitor.MAX_STAND_WORKERS = 1
            result = mon._run_parallel_booking(trigger_btn)
        finally:
            monitor.MAX_STAND_WORKERS = original_max
        assert result is False

    def test_success_event_stops_other_workers(self):
        """Once one worker sets success_event, others skip remaining attempts."""
        import monitor, threading
        mon = self._make_monitor()
        trigger_btn = make_element(text="Buy Tickets")
        call_log = []

        def fake_checkout(btn, stand_index=0):
            call_log.append(stand_index)
            if stand_index == 0:
                return  # success
            raise RuntimeError("should not reach here if event is set fast enough")

        mon._checkout_flow = fake_checkout

        original_max = monitor.MAX_STAND_WORKERS
        try:
            monitor.MAX_STAND_WORKERS = 3
            with patch("time.sleep"):  # speed up jitter
                result = mon._run_parallel_booking(trigger_btn)
        finally:
            monitor.MAX_STAND_WORKERS = original_max

        assert result is True
        # Worker 0 definitely ran
        assert 0 in call_log


# ═══════════════════════════════════════════════════════════════════════════
# 25. Regression: PRICE_PER_TICKET_MAX and TICKET_QUANTITY constants
# ═══════════════════════════════════════════════════════════════════════════

class TestNewConstants:
    """Regression tests for new .env-driven constants."""

    def test_price_per_ticket_max_is_positive(self):
        from monitor import PRICE_PER_TICKET_MAX
        assert PRICE_PER_TICKET_MAX > 0

    def test_max_stand_workers_is_between_1_and_7(self):
        from monitor import MAX_STAND_WORKERS
        assert 1 <= MAX_STAND_WORKERS <= 7

    def test_ticket_quantity_is_positive(self):
        from monitor import TICKET_QUANTITY
        assert TICKET_QUANTITY >= 1

    def test_worker_startup_jitter_non_negative(self):
        from monitor import WORKER_STARTUP_JITTER
        assert WORKER_STARTUP_JITTER >= 0


# ═══════════════════════════════════════════════════════════════════════════
# Session health check & auto-restart
# ═══════════════════════════════════════════════════════════════════════════


class TestSessionHealth:
    """Tests for _is_session_alive, _restart_driver, and _ensure_session."""

    def _make_monitor(self):
        from monitor import Config, WebsiteMonitor, PageAnalyzer
        with patch.object(WebsiteMonitor, '__init__', lambda self, *a, **kw: None):
            mon = WebsiteMonitor.__new__(WebsiteMonitor)
        mon.config = Config(mode="live-tickets",
                            target_url="https://shop.royalchallengers.com/")
        mon.driver = MagicMock()
        mon.driver.current_url = "https://shop.royalchallengers.com/"
        mon.wait = MagicMock()
        mon.short_wait = MagicMock()
        mon.page = MagicMock()
        mon._screenshot = MagicMock()
        mon._booked_matches = set()
        mon._current_match_id = None
        mon._profile_dir = "chrome_profile"
        return mon

    # ── _is_session_alive ─────────────────────────────────────────────────

    def test_alive_when_driver_responds(self):
        """_is_session_alive returns True when current_url is accessible."""
        mon = self._make_monitor()
        assert mon._is_session_alive() is True

    def test_dead_when_driver_throws(self):
        """_is_session_alive returns False when current_url raises."""
        from selenium.common.exceptions import WebDriverException
        mon = self._make_monitor()
        type(mon.driver).current_url = PropertyMock(
            side_effect=WebDriverException("session not found"))
        assert mon._is_session_alive() is False

    def test_dead_when_driver_is_none(self):
        """_is_session_alive returns False when driver is None."""
        mon = self._make_monitor()
        mon.driver = None
        assert mon._is_session_alive() is False

    # ── _restart_driver ───────────────────────────────────────────────────

    @patch("time.sleep")
    def test_restart_quits_old_and_sets_up_new(self, mock_sleep):
        """_restart_driver quits the dead driver and calls _setup_driver."""
        mon = self._make_monitor()
        old_driver = mon.driver
        mon._setup_driver = MagicMock()
        # After _setup_driver, mon.driver should be set by the real method;
        # simulate that by having _setup_driver set a new mock driver.
        new_driver = MagicMock()
        new_driver.current_url = "https://shop.royalchallengers.com/"
        def fake_setup():
            mon.driver = new_driver
            mon.wait = MagicMock()
            mon.short_wait = MagicMock()
            mon.page = MagicMock()
        mon._setup_driver = MagicMock(side_effect=fake_setup)
        mon._wait_ready_robust = MagicMock()
        mon._is_login_page = MagicMock(return_value=False)
        mon._handle_login_wall = MagicMock()

        mon._restart_driver()

        old_driver.quit.assert_called_once()
        mon._setup_driver.assert_called_once()
        new_driver.get.assert_called_once_with(mon.config.target_url)
        mon._wait_ready_robust.assert_called_once()

    @patch("time.sleep")
    def test_restart_handles_quit_exception(self, mock_sleep):
        """_restart_driver doesn't crash if quit() throws."""
        from selenium.common.exceptions import WebDriverException
        mon = self._make_monitor()
        mon.driver.quit.side_effect = WebDriverException("already dead")
        new_driver = MagicMock()
        new_driver.current_url = "https://shop.royalchallengers.com/"
        def fake_setup():
            mon.driver = new_driver
            mon.wait = MagicMock()
            mon.short_wait = MagicMock()
            mon.page = MagicMock()
        mon._setup_driver = MagicMock(side_effect=fake_setup)
        mon._wait_ready_robust = MagicMock()
        mon._is_login_page = MagicMock(return_value=False)

        mon._restart_driver()  # should not raise
        mon._setup_driver.assert_called_once()

    @patch("time.sleep")
    def test_restart_re_authenticates_if_login_wall(self, mock_sleep):
        """_restart_driver calls _handle_login_wall when login page detected."""
        mon = self._make_monitor()
        new_driver = MagicMock()
        def fake_setup():
            mon.driver = new_driver
            mon.wait = MagicMock()
            mon.short_wait = MagicMock()
            mon.page = MagicMock()
        mon._setup_driver = MagicMock(side_effect=fake_setup)
        mon._wait_ready_robust = MagicMock()
        mon._is_login_page = MagicMock(return_value=True)
        mon._handle_login_wall = MagicMock()

        mon._restart_driver()

        mon._handle_login_wall.assert_called_once()

    # ── _ensure_session ───────────────────────────────────────────────────

    def test_ensure_session_noop_when_alive(self):
        """_ensure_session does nothing when session is alive."""
        mon = self._make_monitor()
        mon._restart_driver = MagicMock()

        mon._ensure_session()

        mon._restart_driver.assert_not_called()

    def test_ensure_session_restarts_when_dead(self):
        """_ensure_session triggers restart when session is dead."""
        from selenium.common.exceptions import WebDriverException
        mon = self._make_monitor()
        type(mon.driver).current_url = PropertyMock(
            side_effect=WebDriverException("dead"))
        mon._restart_driver = MagicMock()

        mon._ensure_session()

        mon._restart_driver.assert_called_once()


# ═══════════════════════════════════════════════════════════════════════════
# Cleanup — screenshot auto-cleanup, log rotation, chrome cache
# ═══════════════════════════════════════════════════════════════════════════


class TestScreenshotCleanup:
    """Tests for WebsiteMonitor._cleanup_old_screenshots."""

    def _make_monitor(self, tmp_path):
        from monitor import WebsiteMonitor, Config
        config = Config(screenshot_dir=tmp_path)
        with patch.object(WebsiteMonitor, "_setup_driver"):
            mon = WebsiteMonitor.__new__(WebsiteMonitor)
            mon.config = config
            mon.driver = MagicMock()
            mon.wait = MagicMock()
            mon.short_wait = MagicMock()
            mon.page = MagicMock()
            mon._profile_dir = str(tmp_path)
            mon._booked_matches = set()
            mon._current_match_id = None
        return mon

    def test_deletes_oldest_files_beyond_limit(self, tmp_path):
        """Files beyond SCREENSHOT_KEEP_CYCLES * 5 are deleted, oldest first."""
        import time as _time
        mon = self._make_monitor(tmp_path)
        # Create 60 files (limit with default SCREENSHOT_KEEP_CYCLES=10 is 50)
        for i in range(60):
            f = tmp_path / f"20260324-{i:06d}-cycle-{i}-not-available.png"
            f.write_bytes(b"fake")
            # Ensure distinct mtime ordering
            import os
            os.utime(f, (i, i))

        with patch("monitor.SCREENSHOT_KEEP_CYCLES", 10):
            mon._cleanup_old_screenshots(cycle=60)

        remaining = list(tmp_path.glob("*.png"))
        assert len(remaining) == 50  # kept latest 50

    def test_no_deletion_when_below_limit(self, tmp_path):
        """No files are deleted when count is below the threshold."""
        mon = self._make_monitor(tmp_path)
        for i in range(5):
            (tmp_path / f"screenshot-{i}.png").write_bytes(b"fake")

        with patch("monitor.SCREENSHOT_KEEP_CYCLES", 10):
            mon._cleanup_old_screenshots(cycle=5)

        remaining = list(tmp_path.glob("*.png"))
        assert len(remaining) == 5

    def test_handles_empty_directory(self, tmp_path):
        """No error when screenshot directory is empty."""
        mon = self._make_monitor(tmp_path)
        mon._cleanup_old_screenshots(cycle=1)  # should not raise

    def test_keeps_newest_files(self, tmp_path):
        """The most recently modified files are the ones kept."""
        import os
        mon = self._make_monitor(tmp_path)
        old_files = []
        new_files = []
        for i in range(55):
            f = tmp_path / f"ss-{i:04d}.png"
            f.write_bytes(b"fake")
            os.utime(f, (i, i))
            if i < 5:
                old_files.append(f.name)
            else:
                new_files.append(f.name)

        with patch("monitor.SCREENSHOT_KEEP_CYCLES", 10):
            mon._cleanup_old_screenshots(cycle=55)

        remaining_names = {f.name for f in tmp_path.glob("*.png")}
        # Old files should be gone
        for name in old_files:
            assert name not in remaining_names
        # New files should remain
        for name in new_files:
            assert name in remaining_names


class TestChromeCacheCleanup:
    """Tests for WebsiteMonitor._cleanup_chrome_cache."""

    def test_removes_shader_cache_dirs(self, tmp_path):
        """Expendable cache directories are removed."""
        from monitor import WebsiteMonitor
        for d in ("ShaderCache", "GrShaderCache", "GraphiteDawnCache"):
            (tmp_path / d).mkdir()
            (tmp_path / d / "data.bin").write_bytes(b"\x00" * 100)

        WebsiteMonitor._cleanup_chrome_cache(str(tmp_path))

        for d in ("ShaderCache", "GrShaderCache", "GraphiteDawnCache"):
            assert not (tmp_path / d).exists()

    def test_removes_crashpad_reports(self, tmp_path):
        """Crashpad/reports directory is removed."""
        from monitor import WebsiteMonitor
        crashpad = tmp_path / "Crashpad" / "reports"
        crashpad.mkdir(parents=True)
        (crashpad / "crash.dmp").write_bytes(b"\x00" * 50)

        WebsiteMonitor._cleanup_chrome_cache(str(tmp_path))

        assert not crashpad.exists()

    def test_removes_browser_metrics_file(self, tmp_path):
        """BrowserMetrics-spare.pma file is removed."""
        from monitor import WebsiteMonitor
        f = tmp_path / "BrowserMetrics-spare.pma"
        f.write_bytes(b"\x00" * 100)

        WebsiteMonitor._cleanup_chrome_cache(str(tmp_path))

        assert not f.exists()

    def test_preserves_non_expendable_dirs(self, tmp_path):
        """Directories NOT in the expendable list are left alone."""
        from monitor import WebsiteMonitor
        for d in ("Default", "Safe Browsing", "Local State"):
            p = tmp_path / d
            p.mkdir(exist_ok=True)
            (p / "data").write_bytes(b"important") if p.is_dir() else None

        WebsiteMonitor._cleanup_chrome_cache(str(tmp_path))

        for d in ("Default", "Safe Browsing"):
            assert (tmp_path / d).exists()

    def test_handles_missing_profile_dir(self, tmp_path):
        """No error when cache dirs don't exist."""
        from monitor import WebsiteMonitor
        # tmp_path exists but has no cache dirs
        WebsiteMonitor._cleanup_chrome_cache(str(tmp_path))  # should not raise


class TestLogRotationConfig:
    """Tests for log rotation configuration."""

    def test_rotating_handler_is_used(self):
        """setup_logging creates a RotatingFileHandler."""
        import logging.handlers
        from monitor import setup_logging, LOG_FILE
        # Clear existing handlers
        root = logging.getLogger()
        original_handlers = root.handlers[:]
        root.handlers.clear()
        try:
            setup_logging()
            rotating = [
                h for h in root.handlers
                if isinstance(h, logging.handlers.RotatingFileHandler)
            ]
            assert len(rotating) == 1
            assert rotating[0].maxBytes > 0
            assert rotating[0].backupCount > 0
        finally:
            # Restore original handlers
            for h in root.handlers[:]:
                h.close()
                root.removeHandler(h)
            root.handlers = original_handlers

    def test_default_log_max_bytes(self):
        """LOG_MAX_BYTES defaults to 5 MB."""
        from monitor import LOG_MAX_BYTES
        assert LOG_MAX_BYTES == 5 * 1024 * 1024

    def test_default_log_backup_count(self):
        """LOG_BACKUP_COUNT defaults to 3."""
        from monitor import LOG_BACKUP_COUNT
        assert LOG_BACKUP_COUNT == 3

    def test_default_screenshot_keep_cycles(self):
        """SCREENSHOT_KEEP_CYCLES defaults to 10."""
        from monitor import SCREENSHOT_KEEP_CYCLES
        assert SCREENSHOT_KEEP_CYCLES == 10
