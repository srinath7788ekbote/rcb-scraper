"""Shared fixtures for monitor tests."""

import pytest
from unittest.mock import MagicMock, PropertyMock
from typing import Dict, List, Optional


def make_element(
    text: str = "",
    tag_name: str = "button",
    displayed: bool = True,
    enabled: bool = True,
    attrs: Optional[Dict[str, str]] = None,
) -> MagicMock:
    """Factory for mock WebElements with configurable attributes."""
    el = MagicMock()
    el.text = text
    el.tag_name = tag_name
    el.is_displayed.return_value = displayed
    el.is_enabled.return_value = enabled
    el.location = {"x": 0, "y": 0}

    _attrs = attrs or {}

    def _get_attribute(name: str) -> Optional[str]:
        return _attrs.get(name)

    el.get_attribute = MagicMock(side_effect=_get_attribute)
    el.find_elements = MagicMock(return_value=[])
    from selenium.common.exceptions import NoSuchElementException
    el.find_element = MagicMock(side_effect=NoSuchElementException("not found"))
    return el


@pytest.fixture
def mock_driver():
    """A mock Selenium Chrome WebDriver."""
    driver = MagicMock()
    driver.current_url = "https://shop.royalchallengers.com/merchandise/152"
    driver.find_elements = MagicMock(return_value=[])
    driver.find_element = MagicMock()

    body_el = make_element(text="", tag_name="body")
    driver.find_element.return_value = body_el
    driver.execute_script = MagicMock(return_value="complete")
    driver.window_handles = ["main"]
    return driver


@pytest.fixture
def page_analyzer(mock_driver):
    """A PageAnalyzer instance backed by a mock driver."""
    # Import here to avoid module-level side effects
    from monitor import PageAnalyzer
    return PageAnalyzer(mock_driver)
