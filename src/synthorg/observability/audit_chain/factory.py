# module-kind: code
"""Factory + installer for the audit-chain logging sink.

Assembles the :class:`AuditChainSink` from its collaborators -- the
Ed25519 signer, a timestamp provider (local clock by default, RFC 3161
TSA when configured), and a fresh hash chain -- and attaches it to the
root logger so every ``security.*`` / ``tool.registry.integrity.*`` event
is signed and chained. Gated on :attr:`AuditChainConfig.enabled`, so a
normal boot (the default ``enabled=False``) installs nothing and the
existing Prometheus / timeout wiring simply finds no sink to hook.
"""

import logging
from pathlib import Path
from typing import Final

from synthorg.core.clock import Clock
from synthorg.observability import get_logger
from synthorg.observability.audit_chain.config import AuditChainConfig
from synthorg.observability.audit_chain.signer import build_ed25519_signer
from synthorg.observability.audit_chain.sink import AuditChainSink
from synthorg.observability.audit_chain.timestamping import (
    LocalClockProvider,
    ResilientTimestampProvider,
    TimestampProvider,
)
from synthorg.observability.audit_chain.tsa_client import TsaClient
from synthorg.observability.events.audit_chain import (
    AUDIT_CHAIN_SINK_DISABLED,
    AUDIT_CHAIN_SINK_INSTALLED,
)

logger = get_logger(__name__)

_PEM_CERT_END: Final[bytes] = b"-----END CERTIFICATE-----"


def _load_trusted_roots(path: Path | None) -> tuple[bytes, ...]:
    """Split a PEM bundle into individual certificate byte-blocks.

    The TSA client loads each root independently, so a multi-cert
    bundle must be split rather than passed whole.

    Returns:
        One PEM block per certificate; empty when *path* is unset.
    """
    if path is None or not path.is_file():
        return ()
    raw = path.read_bytes()
    blocks: list[bytes] = []
    for chunk in raw.split(_PEM_CERT_END):
        trimmed = chunk.strip()
        if trimmed:
            blocks.append(trimmed + b"\n" + _PEM_CERT_END + b"\n")
    return tuple(blocks)


def _build_timestamp_provider(config: AuditChainConfig) -> TimestampProvider:
    """Select the timestamp provider for *config*.

    Returns:
        A TSA-backed resilient provider when a TSA URL resolves, else
        the local-clock provider.
    """
    tsa_url = config.effective_tsa_url()
    if tsa_url is None:
        return LocalClockProvider()
    return ResilientTimestampProvider(
        TsaClient(
            tsa_url,
            timeout_sec=config.tsa_timeout_sec,
            hash_algorithm=config.tsa_hash_algorithm,
            trusted_roots=(
                _load_trusted_roots(config.tsa_trusted_roots_path)
                if config.tsa_verify_signature
                else ()
            ),
        ),
    )


def build_audit_chain_sink(
    config: AuditChainConfig,
    *,
    clock: Clock | None = None,
) -> AuditChainSink | None:
    """Build the audit-chain sink, or ``None`` when disabled.

    Args:
        config: Audit-chain configuration.
        clock: Clock seam threaded into the signer.

    Returns:
        A constructed :class:`AuditChainSink`, or ``None`` when
        ``config.enabled`` is False.
    """
    if not config.enabled:
        return None
    signer = build_ed25519_signer(config.signing_key_path, clock=clock)
    return AuditChainSink(
        signer=signer,
        timestamp_provider=_build_timestamp_provider(config),
        config=config,
    )


def install_audit_chain_sink(
    config: AuditChainConfig,
    *,
    clock: Clock | None = None,
) -> AuditChainSink | None:
    """Attach the audit-chain sink to the root logger when enabled.

    Idempotent: returns the existing sink when one is already attached
    (re-entered lifespans / hot reload) without adding a duplicate.

    Args:
        config: Audit-chain configuration.
        clock: Clock seam threaded into the signer.

    Returns:
        The installed (or already-present) sink, or ``None`` when
        disabled.
    """
    root = logging.getLogger()
    for handler in root.handlers:
        if isinstance(handler, AuditChainSink):
            return handler

    if not config.enabled:
        logger.info(AUDIT_CHAIN_SINK_DISABLED, reason="config_disabled")
        return None

    sink = build_audit_chain_sink(config, clock=clock)
    if sink is None:
        return None
    root.addHandler(sink)
    logger.info(
        AUDIT_CHAIN_SINK_INSTALLED,
        backend=config.backend,
        tsa_preset=config.tsa_preset.value,
    )
    return sink


__all__ = ["build_audit_chain_sink", "install_audit_chain_sink"]
