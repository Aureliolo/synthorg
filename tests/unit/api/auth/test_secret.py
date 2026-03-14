"""Tests for JWT secret resolution chain."""

from unittest.mock import AsyncMock, patch

import pytest

from synthorg.api.auth.secret import resolve_jwt_secret


def _make_persistence(stored_secret: str | None = None) -> AsyncMock:
    """Build a fake persistence backend for secret resolution tests."""
    persistence = AsyncMock()
    persistence.get_setting = AsyncMock(return_value=stored_secret)
    persistence.set_setting = AsyncMock()
    return persistence


@pytest.mark.unit
class TestResolveJwtSecret:
    async def test_env_var_takes_priority(self) -> None:
        secret = "env-secret-that-is-at-least-32-characters!!"
        persistence = _make_persistence(stored_secret="stored-secret-32-chars-long!!!!")
        with patch.dict("os.environ", {"SYNTHORG_JWT_SECRET": secret}):
            result = await resolve_jwt_secret(persistence)

        assert result == secret
        persistence.get_setting.assert_not_called()

    async def test_stored_secret_used_when_no_env_var(self) -> None:
        stored = "stored-secret-that-is-at-least-32-chars!!"
        persistence = _make_persistence(stored_secret=stored)
        with patch.dict("os.environ", {}, clear=True):
            result = await resolve_jwt_secret(persistence)

        assert result == stored

    async def test_generates_and_persists_when_nothing_stored(self) -> None:
        persistence = _make_persistence(stored_secret=None)
        with patch.dict("os.environ", {}, clear=True):
            result = await resolve_jwt_secret(persistence)

        assert len(result) >= 32
        persistence.set_setting.assert_awaited_once_with("jwt_secret", result)

    async def test_env_var_too_short_raises(self) -> None:
        persistence = _make_persistence()
        with (
            patch.dict("os.environ", {"SYNTHORG_JWT_SECRET": "short"}),
            pytest.raises(ValueError, match="at least 32 characters"),
        ):
            await resolve_jwt_secret(persistence)

    async def test_env_var_whitespace_stripped(self) -> None:
        secret = "  env-secret-that-is-at-least-32-characters!!  "
        persistence = _make_persistence()
        with patch.dict("os.environ", {"SYNTHORG_JWT_SECRET": secret}):
            result = await resolve_jwt_secret(persistence)

        assert result == secret.strip()

    async def test_empty_env_var_falls_through(self) -> None:
        stored = "stored-secret-that-is-at-least-32-chars!!"
        persistence = _make_persistence(stored_secret=stored)
        with patch.dict("os.environ", {"SYNTHORG_JWT_SECRET": ""}):
            result = await resolve_jwt_secret(persistence)

        assert result == stored

    async def test_whitespace_only_env_var_falls_through(self) -> None:
        stored = "stored-secret-that-is-at-least-32-chars!!"
        persistence = _make_persistence(stored_secret=stored)
        with patch.dict("os.environ", {"SYNTHORG_JWT_SECRET": "   "}):
            result = await resolve_jwt_secret(persistence)

        assert result == stored
