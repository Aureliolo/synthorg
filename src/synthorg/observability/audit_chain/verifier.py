"""AuditChainVerifier -- verify hash chain and EvidencePackage signatures."""

from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from synthorg.core.critical_errors import reraise_critical
from synthorg.observability import get_logger
from synthorg.observability.audit_chain.chain import HashChain
from synthorg.observability.audit_chain.protocol import AuditChainSigner
from synthorg.observability.events.security import (
    SECURITY_AUDIT_CHAIN_BREAK_DETECTED,
    SECURITY_AUDIT_CHAIN_VERIFY_COMPLETE,
    SECURITY_AUDIT_CHAIN_VERIFY_START,
)
from synthorg.observability.metrics_hub import record_audit_chain_verification

logger = get_logger(__name__)


class ChainVerificationResult(BaseModel):
    """Result of verifying an audit chain.

    Attributes:
        valid: Whether the entire chain is intact.
        entries_checked: Number of entries verified.
        first_break_position: Position of first broken link, if any.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    valid: bool = Field(description="Whether the chain is intact")
    entries_checked: int = Field(
        ge=0,
        description="Number of entries verified",
    )
    first_break_position: int | None = Field(
        default=None,
        description="Position of first broken link",
    )

    @model_validator(mode="after")
    def _validate_consistency(self) -> Self:
        """Ensure break position aligns with validity."""
        if self.valid and self.first_break_position is not None:
            msg = "first_break_position must be None when valid=True"
            raise ValueError(msg)
        if not self.valid and self.first_break_position is None:
            msg = "first_break_position required when valid=False"
            raise ValueError(msg)
        return self


class AuditChainVerifier:
    """Verify audit chain integrity and EvidencePackage signatures.

    Args:
        signer: Signing backend for signature verification.
    """

    def __init__(self, signer: AuditChainSigner) -> None:
        self._signer = signer

    async def verify_chain(self, chain: HashChain) -> ChainVerificationResult:
        """Verify the entire hash chain.

        Checks hash continuity and verifies each signature. Emits one
        ``synthorg_audit_chain_verifications_total`` increment per call
        with ``outcome="valid"|"broken"`` so dashboards expose chain
        tampering as a queryable rate. Exceptions raised by the signer
        (crypto / network / key-unavailable) are reported as
        ``outcome="broken"`` before re-raising so a transient verifier
        failure cannot create a tamper-detection blind spot.

        Args:
            chain: Hash chain to verify.

        Returns:
            Verification result with validity and break position.
        """
        try:
            result = await self._verify_chain_inner(chain)
        except Exception as exc:
            reraise_critical(exc)
            record_audit_chain_verification(
                outcome="broken",
                entries_checked=0,
                first_break_position=None,
            )
            raise
        record_audit_chain_verification(
            outcome="valid" if result.valid else "broken",
            entries_checked=result.entries_checked,
            first_break_position=result.first_break_position,
        )
        return result

    async def _verify_chain_inner(
        self,
        chain: HashChain,
    ) -> ChainVerificationResult:
        """Core verification body; metric emission lives in ``verify_chain``."""
        logger.debug(
            SECURITY_AUDIT_CHAIN_VERIFY_START,
            entry_count=len(chain.entries),
        )

        entries = chain.entries
        if not entries:
            return ChainVerificationResult(
                valid=True,
                entries_checked=0,
            )

        # Check hash continuity.
        if not chain.verify_integrity():
            # Find the first break.
            expected_prev = chain.initial_hash
            for entry in entries:
                if entry.previous_hash != expected_prev:
                    logger.error(
                        SECURITY_AUDIT_CHAIN_BREAK_DETECTED,
                        position=entry.position,
                        expected=expected_prev,
                        actual=entry.previous_hash,
                    )
                    return ChainVerificationResult(
                        valid=False,
                        entries_checked=entry.position,
                        first_break_position=entry.position,
                    )
                expected_prev = HashChain._link_hash(  # noqa: SLF001
                    expected_prev,
                    entry.event_hash,
                    entry.signature,
                    entry.timestamp,
                )

            # No entry mismatch found but verify_integrity returned
            # False -- tail hash corruption.
            tail_pos = entries[-1].position + 1
            logger.error(
                SECURITY_AUDIT_CHAIN_BREAK_DETECTED,
                position=tail_pos,
                expected=expected_prev,
                actual=chain.tail_hash,
                reason="tail_hash_mismatch",
            )
            return ChainVerificationResult(
                valid=False,
                entries_checked=len(entries),
                first_break_position=tail_pos,
            )

        # Verify each entry's signature against its canonical payload.
        for entry in entries:
            sig_valid = await self._signer.verify(
                entry.canonical_payload,
                entry.signature,
            )
            if not sig_valid:
                logger.error(
                    SECURITY_AUDIT_CHAIN_BREAK_DETECTED,
                    position=entry.position,
                    reason="signature_invalid",
                )
                return ChainVerificationResult(
                    valid=False,
                    entries_checked=entry.position,
                    first_break_position=entry.position,
                )

        logger.debug(
            SECURITY_AUDIT_CHAIN_VERIFY_COMPLETE,
            entries_checked=len(entries),
            valid=True,
        )

        return ChainVerificationResult(
            valid=True,
            entries_checked=len(entries),
        )

    async def verify_evidence_package(self, pkg: object) -> bool:
        """Verify that an EvidencePackage has sufficient valid signatures.

        Args:
            pkg: An ``EvidencePackage`` instance.

        Returns:
            ``True`` if ``is_fully_signed`` and all signatures verify.
        """
        if not getattr(pkg, "is_fully_signed", False):
            return False

        canonical: bytes = (
            getattr(pkg, "canonical_bytes", None)
            or getattr(pkg, "signed_bytes", b"")
            or b""
        )
        signatures = getattr(pkg, "signatures", ())
        if not canonical or not signatures:
            return False
        try:
            sig_iter = iter(signatures)
        except TypeError:
            return False

        for sig in sig_iter:
            sig_bytes = getattr(sig, "signature_bytes", None)
            if not isinstance(sig_bytes, bytes):
                return False
            valid = await self._signer.verify(
                canonical,
                sig_bytes,
            )
            if not valid:
                return False

        return True
