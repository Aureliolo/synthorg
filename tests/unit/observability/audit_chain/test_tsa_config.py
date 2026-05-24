"""Unit tests for AuditChainConfig TSA preset coherence."""

from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from synthorg.observability.audit_chain.config import (
    AuditChainConfig,
    TsaPreset,
    resolve_tsa_url,
)

pytestmark = pytest.mark.unit


def test_default_preset_is_none() -> None:
    config = AuditChainConfig()
    assert config.tsa_preset == TsaPreset.NONE
    assert config.tsa_url is None
    assert config.effective_tsa_url() is None


def test_custom_preset_requires_tsa_url() -> None:
    with pytest.raises(ValidationError, match="requires tsa_url"):
        AuditChainConfig(tsa_preset=TsaPreset.CUSTOM)


def test_custom_preset_with_url_resolves() -> None:
    config = AuditChainConfig(
        tsa_preset=TsaPreset.CUSTOM,
        tsa_url="https://tsa.example.com/tsr",
        tsa_trusted_roots_path=Path("tests/data/custom_roots.pem"),
    )
    assert config.effective_tsa_url() == "https://tsa.example.com/tsr"


@pytest.mark.parametrize(
    ("preset", "roots_path", "expected_url"),
    [
        (
            TsaPreset.FREETSA,
            Path("tests/data/freetsa_roots.pem"),
            "https://freetsa.org/tsr",
        ),
        (
            TsaPreset.DIGICERT,
            Path("tests/data/digicert_roots.pem"),
            "https://timestamp.digicert.com",
        ),
        (
            TsaPreset.SECTIGO,
            Path("tests/data/sectigo_roots.pem"),
            "https://timestamp.sectigo.com",
        ),
    ],
)
def test_preset_resolves_to_canonical_url(
    preset: TsaPreset,
    roots_path: Path,
    expected_url: str,
) -> None:
    """Each non-CUSTOM preset maps to the documented default endpoint."""
    config = AuditChainConfig(
        tsa_preset=preset,
        tsa_trusted_roots_path=roots_path,
    )
    assert config.effective_tsa_url() == expected_url


@pytest.mark.parametrize(
    ("tsa_preset", "extra_kwargs"),
    [
        (TsaPreset.FREETSA, {}),
        (TsaPreset.DIGICERT, {}),
        (TsaPreset.SECTIGO, {}),
        (TsaPreset.CUSTOM, {"tsa_url": "https://tsa.example.com/tsr"}),
    ],
)
def test_tsa_missing_roots_rejected_when_verifying(
    tsa_preset: TsaPreset,
    extra_kwargs: dict[str, Any],  # type: ignore[explicit-any]  # extra_kwargs forwarded to AuditChainConfig which has heterogeneous typed fields (Literal, Path, float, bool)
) -> None:
    """Verifying presets reject configs without ``tsa_trusted_roots_path``."""
    with pytest.raises(ValidationError, match="tsa_trusted_roots_path"):
        AuditChainConfig(
            tsa_preset=tsa_preset,
            tsa_verify_signature=True,
            **extra_kwargs,
        )


def test_freetsa_without_roots_allowed_when_not_verifying() -> None:
    config = AuditChainConfig(
        tsa_preset=TsaPreset.FREETSA,
        tsa_verify_signature=False,
    )
    assert config.effective_tsa_url() == "https://freetsa.org/tsr"


def test_custom_tsa_url_overrides_preset() -> None:
    """A non-NONE non-CUSTOM preset with tsa_url uses the override."""
    config = AuditChainConfig(
        tsa_preset=TsaPreset.DIGICERT,
        tsa_url="https://staging-tsa.example.com/tsr",
        tsa_trusted_roots_path=Path("tests/data/digicert_roots.pem"),
    )
    assert config.effective_tsa_url() == "https://staging-tsa.example.com/tsr"


@pytest.mark.parametrize("tsa_timeout_sec", [0.0, 5.01, 60.0])
def test_timeout_range_enforced(tsa_timeout_sec: float) -> None:
    """Values outside the configured ``(0, 5.0]`` bound are rejected."""
    with pytest.raises(ValidationError):
        AuditChainConfig(tsa_timeout_sec=tsa_timeout_sec)


@pytest.mark.parametrize("value", [0.1, 2.5, 5.0])
def test_timeout_accepts_boundary_values(value: float) -> None:
    """The validator permits values in the allowed ``(0, 5.0]`` range."""
    cfg = AuditChainConfig(tsa_timeout_sec=value)
    assert cfg.tsa_timeout_sec == value


@pytest.mark.parametrize(
    ("preset", "override", "expected"),
    [
        # NONE ignores any override and resolves to no URL.
        (TsaPreset.NONE, None, None),
        (TsaPreset.NONE, "override", None),
        # CUSTOM requires an override; without one there is no URL.
        (TsaPreset.CUSTOM, "x", "x"),
        (TsaPreset.CUSTOM, None, None),
        # Named presets resolve to their documented canonical URL when
        # no override is supplied, and accept overrides transparently.
        (TsaPreset.DIGICERT, None, "https://timestamp.digicert.com"),
        (TsaPreset.FREETSA, "override", "override"),
    ],
)
def test_resolve_tsa_url_helper(
    preset: TsaPreset,
    override: str | None,
    expected: str | None,
) -> None:
    """``resolve_tsa_url`` applies preset/override semantics per case."""
    assert resolve_tsa_url(preset, override) == expected


def test_resolve_tsa_url_with_explicit_preset_urls() -> None:
    """An injected ``preset_urls`` mapping overrides the documented default."""
    from types import MappingProxyType

    custom_urls = MappingProxyType(
        {
            TsaPreset.FREETSA: "https://tsa.example.com/tsr",
            TsaPreset.DIGICERT: "https://timestamp.digicert.example.com",
            TsaPreset.SECTIGO: "https://timestamp.sectigo.example.com",
        }
    )
    assert (
        resolve_tsa_url(TsaPreset.FREETSA, None, preset_urls=custom_urls)
        == "https://tsa.example.com/tsr"
    )


def test_resolve_tsa_url_falls_back_when_preset_urls_none() -> None:
    """``preset_urls=None`` falls back to the documented baseline."""
    assert (
        resolve_tsa_url(TsaPreset.DIGICERT, None, preset_urls=None)
        == "https://timestamp.digicert.com"
    )


def test_effective_tsa_url_threads_preset_urls_through() -> None:
    """``AuditChainConfig.effective_tsa_url`` honours operator overrides.

    Without this, the bridge-resolved ``observability.tsa_endpoint_*``
    settings would be silently ignored when callers reach for the
    config-level convenience accessor instead of ``resolve_tsa_url``
    directly.
    """
    from types import MappingProxyType

    config = AuditChainConfig(
        tsa_preset=TsaPreset.FREETSA,
        tsa_verify_signature=False,
    )
    overrides = MappingProxyType(
        {TsaPreset.FREETSA: "https://private-tsa.example.com/tsr"}
    )
    assert (
        config.effective_tsa_url(preset_urls=overrides)
        == "https://private-tsa.example.com/tsr"
    )
    # ``preset_urls=None`` keeps the documented baseline.
    assert config.effective_tsa_url() == "https://freetsa.org/tsr"


def test_resolve_tsa_url_incomplete_preset_urls_falls_back() -> None:
    """A partial mapping must not raise; missing presets fall back to the default."""
    from types import MappingProxyType

    partial = MappingProxyType(
        {TsaPreset.FREETSA: "https://only-freetsa.example.com/tsr"}
    )
    # FREETSA is in the override; takes precedence.
    assert (
        resolve_tsa_url(TsaPreset.FREETSA, None, preset_urls=partial)
        == "https://only-freetsa.example.com/tsr"
    )
    # DIGICERT is missing; falls back to documented default rather than KeyError.
    assert (
        resolve_tsa_url(TsaPreset.DIGICERT, None, preset_urls=partial)
        == "https://timestamp.digicert.com"
    )
