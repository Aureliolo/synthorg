"""AuditChainConfig -- opt-in configuration for the audit chain sink."""

from enum import StrEnum
from pathlib import Path  # noqa: TC003
from types import MappingProxyType
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator

from synthorg.core.types import NotBlankStr  # noqa: TC001
from synthorg.observability import get_logger
from synthorg.observability.events.audit_chain import AUDIT_CHAIN_CONFIG_INVALID_PRESET

logger = get_logger(__name__)


class TsaPreset(StrEnum):
    """Well-known RFC 3161 TSA presets.

    ``NONE`` disables TSA timestamping (audit chain falls back to
    local clock and emits ``SECURITY_TIMESTAMP_FALLBACK``). ``CUSTOM``
    requires :attr:`AuditChainConfig.tsa_url` to be set. The other
    values resolve to fixed endpoints; operators selecting these
    accept the associated trust anchor.
    """

    NONE = "none"
    FREETSA = "freetsa"
    DIGICERT = "digicert"
    SECTIGO = "sectigo"
    CUSTOM = "custom"


_DEFAULT_PRESET_URLS: MappingProxyType[TsaPreset, str] = MappingProxyType(
    {
        TsaPreset.FREETSA: "https://freetsa.org/tsr",
        TsaPreset.DIGICERT: "http://timestamp.digicert.com",
        TsaPreset.SECTIGO: "http://timestamp.sectigo.com",
    }
)
"""Documented defaults that mirror the ``observability.tsa_endpoint_*``
settings.  Production callers should resolve via the registry-backed
``ObservabilityBridgeConfig`` and pass the resolved mapping into
:func:`resolve_tsa_url`; this constant is the documented baseline and
the resolver's last-resort fallback when the bridge config is not
available (early boot / test stubs)."""


def resolve_tsa_url(
    preset: TsaPreset,
    tsa_url: str | None,
    *,
    preset_urls: MappingProxyType[TsaPreset, str] | None = None,
) -> str | None:
    """Return the effective TSA URL for a preset + override.

    ``CUSTOM`` uses :attr:`AuditChainConfig.tsa_url`. Any other
    non-``NONE`` preset resolves to its canonical endpoint, but
    :attr:`AuditChainConfig.tsa_url` (when set) overrides the
    preset's default so operators can point at a staging TSA for
    testing.

    Args:
        preset: Selected TSA preset.
        tsa_url: Optional explicit URL override.
        preset_urls: Resolved preset URLs (typically built from the
            ``observability.tsa_endpoint_*`` settings via
            ``ObservabilityBridgeConfig``).  When ``None``, falls back
            to the documented defaults in :data:`_DEFAULT_PRESET_URLS`.
    """
    if preset == TsaPreset.NONE:
        return None
    if preset == TsaPreset.CUSTOM:
        return tsa_url
    if tsa_url is not None:
        return tsa_url
    return (preset_urls or _DEFAULT_PRESET_URLS)[preset]


class AuditChainConfig(BaseModel):
    """Configuration for the quantum-safe audit chain.

    Attributes:
        enabled: Whether the audit chain sink is active.
        backend: Signing backend (``"asqav"`` only for now).
        tsa_preset: Well-known TSA preset or ``CUSTOM`` for a
            user-supplied :attr:`tsa_url`.
        tsa_url: Custom RFC 3161 TSA endpoint. Required when
            ``tsa_preset == CUSTOM``; overrides the preset's default
            URL otherwise.
        tsa_timeout_sec: HTTP request timeout for TSA calls. Upper-
            bounded by the audit sink's 5.0s executor deadline.
        tsa_hash_algorithm: Hash algorithm for the TSA MessageImprint.
        tsa_verify_signature: When ``True``, verify the TSA response's
            CMS SignedData signature against :attr:`tsa_trusted_roots_path`.
            When no roots are supplied, signature verification is
            skipped.
        tsa_trusted_roots_path: PEM bundle of root certificates. Each
            PEM cert in the bundle is loaded independently. Required
            when verifying signatures from :attr:`TsaPreset.FREETSA`
            (self-signed CA).
        signing_key_path: Path to the audit chain signing key file.
        chain_storage_path: Path for audit chain persistence.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    enabled: bool = Field(
        default=False,
        description="Whether the audit chain sink is active",
    )
    backend: Literal["asqav"] = Field(
        default="asqav",
        description="Signing backend",
    )
    tsa_preset: TsaPreset = Field(
        default=TsaPreset.NONE,
        description="Well-known TSA preset or CUSTOM for tsa_url",
    )
    tsa_url: NotBlankStr | None = Field(
        default=None,
        description="Custom TSA endpoint; required when preset=CUSTOM",
    )
    tsa_timeout_sec: float = Field(
        default=5.0,
        gt=0,
        le=5.0,
        description=(
            "HTTP timeout in seconds for TSA requests. Capped by the "
            "audit sink's 5.0s _SIGNING_EXECUTOR deadline -- anything "
            "longer guarantees fallback behaviour."
        ),
    )
    tsa_hash_algorithm: Literal["sha256", "sha512"] = Field(
        default="sha256",
        description="Hash algorithm for the TSA MessageImprint",
    )
    tsa_verify_signature: bool = Field(
        default=True,
        description="Verify the TSA response's CMS SignedData signature",
    )
    tsa_trusted_roots_path: Path | None = Field(
        default=None,
        description="PEM bundle of trusted TSA root certificates",
    )
    signing_key_path: Path | None = Field(
        default=None,
        description="Path to signing key file",
    )
    chain_storage_path: Path | None = Field(
        default=None,
        description="Path for chain persistence",
    )

    @model_validator(mode="after")
    def _check_preset_coherence(self) -> AuditChainConfig:
        """Ensure preset + URL combinations are self-consistent.

        Signature verification (``tsa_verify_signature=True``) requires
        an explicit :attr:`tsa_trusted_roots_path` for every non-``NONE``
        preset. The TSA client does not fall back to system trust
        stores -- :meth:`TsaClient._verify_signature` skips verification
        entirely when no roots are supplied, which would silently
        downgrade a configuration that asked for verification. Forcing
        operators to supply roots per preset prevents that foot-gun.
        """
        if self.tsa_preset == TsaPreset.CUSTOM and self.tsa_url is None:
            msg = "tsa_preset=CUSTOM requires tsa_url to be set"
            logger.error(
                AUDIT_CHAIN_CONFIG_INVALID_PRESET,
                reason="custom_without_url",
                tsa_preset=self.tsa_preset.value,
            )
            raise ValueError(msg)
        if (
            self.tsa_preset != TsaPreset.NONE
            and self.tsa_verify_signature
            and self.tsa_trusted_roots_path is None
        ):
            msg = (
                f"tsa_preset={self.tsa_preset.value} with "
                "tsa_verify_signature=True requires "
                "tsa_trusted_roots_path; the TSA client does not "
                "fall back to system trust anchors. Either supply "
                "the preset's root certificate bundle or set "
                "tsa_verify_signature=False (not recommended)."
            )
            logger.error(
                AUDIT_CHAIN_CONFIG_INVALID_PRESET,
                reason="verify_without_trusted_roots",
                tsa_preset=self.tsa_preset.value,
                tsa_verify_signature=self.tsa_verify_signature,
            )
            raise ValueError(msg)
        return self

    @computed_field  # type: ignore[prop-decorator]
    @property
    def effective_tsa_url(self) -> str | None:
        """Return the concrete TSA URL for this config, or ``None``.

        ``None`` means audit events always use the local clock
        (preset=NONE or unresolved CUSTOM preset).
        """
        return resolve_tsa_url(self.tsa_preset, self.tsa_url)
