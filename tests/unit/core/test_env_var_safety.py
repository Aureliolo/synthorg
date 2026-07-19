"""Tests for credential env-var-name safety screening."""

import pytest

from synthorg.core.env_var_safety import validate_credential_env_var_name

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    "name",
    [
        "GITHUB_PERSONAL_ACCESS_TOKEN",
        "BRAVE_API_KEY",
        "SLACK_BOT_TOKEN",
        "PGPASSWORD",
        "_UNDERSCORE_LEAD",
        "a1",
    ],
)
def test_safe_names_pass(name: str) -> None:
    validate_credential_env_var_name(name)


@pytest.mark.parametrize(
    "name",
    [
        "LD_PRELOAD",
        "ld_preload",  # case-insensitive look-alike
        "LD_LIBRARY_PATH",
        "DYLD_INSERT_LIBRARIES",
        "NODE_OPTIONS",
        "PATH",
        "PYTHONPATH",
        "IFS",
    ],
)
def test_dangerous_names_rejected(name: str) -> None:
    with pytest.raises(ValueError, match="process/loader"):
        validate_credential_env_var_name(name)


@pytest.mark.parametrize(
    "name", ["", "1LEADING_DIGIT", "has space", "a=b", "a-b", "a;b"]
)
def test_malformed_names_rejected(name: str) -> None:
    with pytest.raises(ValueError, match="invalid environment variable name"):
        validate_credential_env_var_name(name)
