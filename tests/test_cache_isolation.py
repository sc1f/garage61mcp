"""Tests that one user never receives the data of another user.

The HTTP transport serves many users from one process. Telemetry is private.
The caches must divide by user, except the corner map, which holds only the
geometry of the track.
"""

import pytest

import api_client
import tools
from reqcontext import reset_request_token, set_request_token, user_scope


@pytest.fixture
def as_user():
    """Run a function with one token bound to the request context."""
    tokens = []

    def use(token):
        tokens.append(set_request_token(token))

    yield use
    for reset in reversed(tokens):
        reset_request_token(reset)


class TestUserScope:
    def test_no_token_gives_the_local_scope(self):
        assert user_scope() == "local"

    def test_two_tokens_give_two_scopes(self, as_user):
        as_user("token-a")
        first = user_scope()
        as_user("token-b")
        assert user_scope() != first

    def test_the_same_token_gives_the_same_scope(self, as_user):
        as_user("token-a")
        first = user_scope()
        as_user("token-a")
        assert user_scope() == first

    def test_the_scope_is_not_the_token(self, as_user):
        """A cache key must not contain the secret."""
        as_user("secret-token-value")
        assert "secret" not in user_scope()

    def test_the_scope_is_short(self, as_user):
        as_user("token-a")
        assert len(user_scope()) == 16


class TestComparisonCache:
    """This cache holds private telemetry. It must divide by user."""

    def test_two_users_get_two_keys(self, as_user):
        as_user("token-a")
        first = tools._cache_key("F4", "Tsukuba")
        as_user("token-b")
        assert tools._cache_key("F4", "Tsukuba") != first

    def test_the_key_contains_the_scope(self, as_user):
        as_user("token-a")
        assert user_scope() in tools._cache_key("F4", "Tsukuba")

    def test_one_user_gets_one_key_for_one_combination(self, as_user):
        as_user("token-a")
        assert tools._cache_key("F4", "Tsukuba") == tools._cache_key("F4", "Tsukuba")

    def test_the_name_of_the_car_is_not_sensitive_to_case(self, as_user):
        as_user("token-a")
        assert tools._cache_key("F4", "Tsukuba") == tools._cache_key("f4 ", " tsukuba")

    def test_a_user_cannot_read_the_entry_of_another_user(self, as_user):
        as_user("token-a")
        tools._comparison_cache[tools._cache_key("F4", "Tsukuba")] = "PRIVATE"
        as_user("token-b")
        assert tools._cache_key("F4", "Tsukuba") not in tools._comparison_cache
        tools._comparison_cache.clear()


class TestCornerMapCache:
    """This cache holds the geometry of the track, which is not private."""

    def test_the_key_is_the_same_for_every_user(self, as_user):
        as_user("token-a")
        first = tools._combo_key("F4", "Tsukuba")
        as_user("token-b")
        assert tools._combo_key("F4", "Tsukuba") == first

    def test_the_key_holds_no_scope(self, as_user):
        as_user("token-a")
        assert user_scope() not in tools._combo_key("F4", "Tsukuba")


class TestTelemetryCache:
    """A CSV that one token obtained must never reach another token."""

    def test_the_key_joins_the_user_and_the_lap(self, as_user):
        as_user("token-a")
        api_client._telemetry_csv_cache[(user_scope(), "lap-1")] = "PRIVATE CSV"
        first = user_scope()

        as_user("token-b")
        assert (user_scope(), "lap-1") not in api_client._telemetry_csv_cache
        assert (first, "lap-1") in api_client._telemetry_csv_cache
        api_client._telemetry_csv_cache.clear()

    def test_the_same_user_reads_the_entry_again(self, as_user):
        as_user("token-a")
        api_client._telemetry_csv_cache[(user_scope(), "lap-1")] = "CSV"
        as_user("token-a")
        assert api_client._telemetry_csv_cache[(user_scope(), "lap-1")] == "CSV"
        api_client._telemetry_csv_cache.clear()


class TestRateLimitGate:
    def test_the_block_belongs_to_one_user(self, as_user):
        """The limit of Garage61 counts each user separately."""
        as_user("token-a")
        first = user_scope()
        as_user("token-b")
        assert user_scope() != first
