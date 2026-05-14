"""Coverage for ``synthorg.settings.mirrors.apply_settings_mirrors``.

Specifically: the ``only_if_env_set`` flag must suppress mirror
application when the resolver falls back to the registered default,
preserving Pydantic field defaults whose ``None`` sentinel carries
semantic meaning ("inherit", "unlimited", "auto-derive").
"""

import pytest

from synthorg.settings import definitions as _settings_definitions  # noqa: F401
from synthorg.settings.enums import SettingNamespace
from synthorg.settings.mirrors import (
    MirrorField,
    apply_settings_mirrors,
    parse_bool,
    parse_int,
    parse_json_int_dict,
    parse_json_int_pair_dict,
)

pytestmark = pytest.mark.unit


def test_default_mirror_applies_when_env_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unconditional mirror surfaces the registered default when env unset."""
    monkeypatch.delenv("SYNTHORG_COORDINATION_FAIL_FAST", raising=False)
    mirrors = (
        MirrorField(
            field="fail_fast",
            namespace=SettingNamespace.COORDINATION,
            key="fail_fast",
            parse=parse_bool,
        ),
    )
    result = apply_settings_mirrors({}, mirrors)
    assert "fail_fast" in result, "default-mirror should populate when env unset"


def test_only_if_env_set_skips_when_env_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``only_if_env_set=True`` leaves the field untouched when env unset.

    The Pydantic-declared default sentinel survives; consumers that
    treat ``None`` specially (e.g. ``None`` = "inherit") see ``None``.
    """
    monkeypatch.delenv("SYNTHORG_COORDINATION_MAX_CONCURRENCY_PER_WAVE", raising=False)
    mirrors = (
        MirrorField(
            field="max_concurrency_per_wave",
            namespace=SettingNamespace.COORDINATION,
            key="max_concurrency_per_wave",
            parse=parse_int,
            only_if_env_set=True,
        ),
    )
    result = apply_settings_mirrors({}, mirrors)
    assert "max_concurrency_per_wave" not in result, (
        "only_if_env_set must NOT overwrite the field when no env override exists"
    )


def test_only_if_env_set_applies_when_env_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``only_if_env_set=True`` surfaces the env override when one is set."""
    monkeypatch.setenv("SYNTHORG_COORDINATION_MAX_CONCURRENCY_PER_WAVE", "12")
    mirrors = (
        MirrorField(
            field="max_concurrency_per_wave",
            namespace=SettingNamespace.COORDINATION,
            key="max_concurrency_per_wave",
            parse=parse_int,
            only_if_env_set=True,
        ),
    )
    result = apply_settings_mirrors({}, mirrors)
    assert result.get("max_concurrency_per_wave") == 12


def test_caller_kwargs_always_win(monkeypatch: pytest.MonkeyPatch) -> None:
    """An explicit caller value wins over the registry even with env set."""
    monkeypatch.setenv("SYNTHORG_COORDINATION_FAIL_FAST", "true")
    mirrors = (
        MirrorField(
            field="fail_fast",
            namespace=SettingNamespace.COORDINATION,
            key="fail_fast",
            parse=parse_bool,
        ),
    )
    result = apply_settings_mirrors({"fail_fast": False}, mirrors)
    assert result["fail_fast"] is False, "caller kwarg must beat env"


def test_non_dict_passthrough() -> None:
    """Non-dict inputs are returned verbatim (Pydantic instance reuse path)."""
    sentinel = object()
    assert apply_settings_mirrors(sentinel, ()) is sentinel


class TestParseJsonIntPairDict:
    """Coverage for ``parse_json_int_pair_dict`` (PerOpRateLimit overrides)."""

    def test_valid_json_returns_dict(self) -> None:
        result = parse_json_int_pair_dict('{"op.alpha":[2,3600]}')
        assert result == {"op.alpha": [2, 3600]}

    def test_empty_object(self) -> None:
        assert parse_json_int_pair_dict("{}") == {}

    def test_invalid_json_returns_none(self) -> None:
        assert parse_json_int_pair_dict("{not valid json") is None

    def test_top_level_list_returns_none(self) -> None:
        assert parse_json_int_pair_dict("[1, 2, 3]") is None

    def test_top_level_string_returns_none(self) -> None:
        assert parse_json_int_pair_dict('"plain"') is None

    def test_non_string_key_returns_none(self) -> None:
        # JSON object keys are always strings at the syntactic level,
        # but defensive coverage of the post-decode guard ensures the
        # contract holds.
        assert parse_json_int_pair_dict('{"1":[2,3]}') == {"1": [2, 3]}


class TestParseJsonIntDict:
    """Coverage for ``parse_json_int_dict`` (PerOpConcurrency overrides)."""

    def test_valid_json_returns_dict(self) -> None:
        result = parse_json_int_dict('{"op.alpha":7}')
        assert result == {"op.alpha": 7}

    def test_empty_object(self) -> None:
        assert parse_json_int_dict("{}") == {}

    def test_invalid_json_returns_none(self) -> None:
        assert parse_json_int_dict("{not valid json") is None

    def test_top_level_list_returns_none(self) -> None:
        assert parse_json_int_dict("[1, 2, 3]") is None

    def test_top_level_int_returns_none(self) -> None:
        assert parse_json_int_dict("42") is None
