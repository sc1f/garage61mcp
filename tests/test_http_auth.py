"""Tests for the credentials that the HTTP transport accepts.

Clients differ in which header names they permit. The claude.ai connector
accepts only names from a list, thus the server must read the credentials from
several names, and from the URL.
"""

import pytest

from http_server import _extract_credentials


def h(**kw):
    """Header names arrive in lower case, as ASGI delivers them."""
    return {k.replace("_", "-"): v for k, v in kw.items()}


class TestAccessKey:
    @pytest.mark.parametrize("name", [
        "x-mcp-access-key", "x-api-key", "api-key", "apikey", "x-apikey",
        "access-key", "x-key",
    ])
    def test_each_accepted_name(self, name):
        key, _ = _extract_credentials({name: "KEY"}, {})
        assert key == "KEY", f"{name} was not read"

    def test_from_the_url(self):
        key, _ = _extract_credentials({}, {"key": ["KEY"]})
        assert key == "KEY"

    def test_absent_gives_an_empty_value(self):
        key, _ = _extract_credentials({}, {})
        assert key == ""


class TestToken:
    def test_authorization_with_the_scheme_word(self):
        _, token = _extract_credentials({"authorization": "Bearer TOKEN"}, {})
        assert token == "TOKEN"

    def test_authorization_without_the_scheme_word(self):
        """Some clients add the word for you and some expect the bare value."""
        _, token = _extract_credentials({"authorization": "TOKEN"}, {})
        assert token == "TOKEN"

    @pytest.mark.parametrize("name", [
        "x-garage61-token", "x-auth-token", "x-access-token", "x-api-token",
        "api-token", "x-token",
    ])
    def test_each_accepted_name(self, name):
        _, token = _extract_credentials({name: "TOKEN"}, {})
        assert token == "TOKEN", f"{name} was not read"

    def test_from_the_url(self):
        _, token = _extract_credentials({}, {"token": ["TOKEN"]})
        assert token == "TOKEN"


class TestJoinedForm:
    """A client that permits only one header sends both credentials in it."""

    def test_the_value_divides_at_the_first_colon(self):
        key, token = _extract_credentials({"authorization": "Bearer KEY:TOKEN"}, {})
        assert (key, token) == ("KEY", "TOKEN")

    def test_a_token_that_contains_a_colon_stays_complete(self):
        """The value divides only when no other header gave a key."""
        key, token = _extract_credentials(
            {"x-api-key": "KEY", "authorization": "Bearer to:ken"}, {})
        assert (key, token) == ("KEY", "to:ken")

    def test_an_empty_part_is_not_a_division(self):
        key, token = _extract_credentials({"authorization": "Bearer :TOKEN"}, {})
        assert token == ":TOKEN"
        assert key == ""


class TestPrecedence:
    def test_authorization_comes_before_the_other_names(self):
        _, token = _extract_credentials(
            {"authorization": "Bearer FIRST", "x-auth-token": "SECOND"}, {})
        assert token == "FIRST"

    def test_a_header_comes_before_the_url(self):
        key, token = _extract_credentials(
            {"x-api-key": "HEADER", "authorization": "Bearer HTOKEN"},
            {"key": ["QUERY"], "token": ["QTOKEN"]})
        assert (key, token) == ("HEADER", "HTOKEN")

    def test_the_connector_combination(self):
        """The pair that the claude.ai connector permits."""
        key, token = _extract_credentials(
            h(x_api_key="KEY", authorization="Bearer TOKEN"), {})
        assert (key, token) == ("KEY", "TOKEN")
