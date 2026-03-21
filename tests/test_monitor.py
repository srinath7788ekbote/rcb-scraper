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

    def test_ticket_partner_domains_has_bookmyshow(self):
        """TICKET_PARTNER_DOMAINS includes bookmyshow.com."""
        from monitor import TICKET_PARTNER_DOMAINS
        assert "bookmyshow.com" in TICKET_PARTNER_DOMAINS

    def test_ticket_partner_domains_has_insider(self):
        """TICKET_PARTNER_DOMAINS includes insider.in."""
        from monitor import TICKET_PARTNER_DOMAINS
        assert "insider.in" in TICKET_PARTNER_DOMAINS

    def test_ticket_link_keywords_has_ticket(self):
        """TICKET_LINK_KEYWORDS includes 'ticket'."""
        from monitor import TICKET_LINK_KEYWORDS
        assert "ticket" in TICKET_LINK_KEYWORDS

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
    def test_detects_bookmyshow_partner(self):
        """_find_ticket_page flags BookMyShow links as partner."""
        mon = self._make_monitor()
        bms_link = make_element(
            text="Book Tickets", tag_name="a",
            attrs={"href": "https://in.bookmyshow.com/events/rcb-vs-csk", "aria-label": ""},
        )
        mon.driver.find_elements.return_value = [bms_link]
        # Mock _screenshot, _play_short_alert, _wait_ready
        mon._screenshot = MagicMock()
        mon._play_short_alert = MagicMock()
        mon._wait_ready = MagicMock()
        result = mon._find_ticket_page()
        assert result is True
        # Should navigate to the partner URL
        mon.driver.get.assert_called_once()
        call_url = mon.driver.get.call_args[0][0]
        assert "bookmyshow" in call_url

    @patch("monitor.ENABLE_NOTIFICATIONS", False)
    def test_detects_insider_partner(self):
        """_find_ticket_page flags Insider links as partner."""
        mon = self._make_monitor()
        insider_link = make_element(
            text="Get Tickets", tag_name="a",
            attrs={"href": "https://insider.in/rcb-match/event", "aria-label": ""},
        )
        mon.driver.find_elements.return_value = [insider_link]
        mon._screenshot = MagicMock()
        mon._play_short_alert = MagicMock()
        mon._wait_ready = MagicMock()
        result = mon._find_ticket_page()
        assert result is True

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
