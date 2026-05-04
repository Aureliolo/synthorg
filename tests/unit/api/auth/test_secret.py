"""Tests for JWT secret resolution."""

from unittest.mock import patch

import pytest

from synthorg.api.auth.secret import resolve_jwt_secret


@pytest.mark.unit
class TestResolveJwtSecret:
    def test_env_var_returns_secret(self) -> None:
        secret = "env-secret-that-is-at-least-32-characters!!"
        with patch.dict("os.environ", {"SYNTHORG_JWT_SECRET": secret}):
            result = resolve_jwt_secret()

        assert result == secret

    def test_env_var_whitespace_stripped(self) -> None:
        secret = "  env-secret-that-is-at-least-32-characters!!  "
        with patch.dict("os.environ", {"SYNTHORG_JWT_SECRET": secret}):
            result = resolve_jwt_secret()

        assert result == secret.strip()

    def test_exact_min_length_accepted(self) -> None:
        from synthorg.core.auth.config import MIN_SECRET_LENGTH

        secret = "x" * MIN_SECRET_LENGTH
        with patch.dict("os.environ", {"SYNTHORG_JWT_SECRET": secret}):
            assert resolve_jwt_secret() == secret

    def test_one_below_min_length_raises(self) -> None:
        from synthorg.core.auth.config import MIN_SECRET_LENGTH

        short = "x" * (MIN_SECRET_LENGTH - 1)
        with (
            patch.dict("os.environ", {"SYNTHORG_JWT_SECRET": short}),
            pytest.raises(ValueError, match="at least 32 characters"),
        ):
            resolve_jwt_secret()

    @pytest.mark.parametrize(
        ("env_value", "match_text"),
        [
            ("short", "at least 32 characters"),
            (None, "is not set"),
            ("", "set but empty"),
            ("   ", "set but empty"),
        ],
        ids=["too-short", "missing", "empty", "whitespace-only"],
    )
    def test_invalid_env_var_raises(
        self,
        env_value: str | None,
        match_text: str,
    ) -> None:
        env = {} if env_value is None else {"SYNTHORG_JWT_SECRET": env_value}
        with (
            patch.dict("os.environ", env, clear=(env_value is None)),
            pytest.raises(ValueError, match=match_text),
        ):
            resolve_jwt_secret()
