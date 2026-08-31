# module-kind: controller
"""Audit-chain verification controller -- CEO-only on-demand re-check.

The chain is verified once at boot (right after hydration) and on a
cadence (``AuditChainVerificationScheduler``); this endpoint lets an
operator ask the same question on demand, e.g. while investigating an
incident, without waiting for the next scheduled sweep.
"""

from typing import Self

from litestar import Controller, post
from pydantic import BaseModel, ConfigDict, Field, model_validator

from synthorg.api.dto import ApiResponse
from synthorg.api.guards import require_ceo
from synthorg.api.rate_limits import per_op_rate_limit_from_policy
from synthorg.core.domain_errors import ServiceUnavailableError
from synthorg.observability import get_logger
from synthorg.observability.events.api import API_AUDIT_CHAIN_VERIFY_REQUESTED
from synthorg.observability.sinks import iter_logging_handlers

logger = get_logger(__name__)


class AuditChainVerificationResponse(BaseModel):
    """Typed view of a :class:`ChainVerificationResult`."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    valid: bool
    entries_checked: int = Field(ge=0)
    first_break_position: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def _validate_consistency(self) -> Self:
        """Ensure break position aligns with validity.

        Mirrors :meth:`ChainVerificationResult._validate_consistency`: a
        wire-facing DTO built by hand from that domain result must not
        silently accept a shape the domain type itself would reject.

        Returns:
            The validated :class:`AuditChainVerificationResponse` instance.

        Raises:
            ValueError: If ``first_break_position`` is set when
                ``valid=True`` or absent when ``valid=False``.
        """
        if self.valid and self.first_break_position is not None:
            msg = "first_break_position must be None when valid=True"
            raise ValueError(msg)
        if not self.valid and self.first_break_position is None:
            msg = "first_break_position required when valid=False"
            raise ValueError(msg)
        return self


async def _verify_live_chain() -> AuditChainVerificationResponse:
    """Re-verify the live audit chain's hash continuity and signatures.

    Extracted from the controller method so it is testable without a
    Litestar-mounted controller instance.

    Returns:
        The verification result.

    Raises:
        ServiceUnavailableError: When ``audit_chain.enabled`` is False, so
            no sink is installed to verify.
    """
    from synthorg.observability.audit_chain.sink import (  # noqa: PLC0415
        AuditChainSink,
    )

    sink = next(
        (h for h in iter_logging_handlers() if isinstance(h, AuditChainSink)),
        None,
    )
    if sink is None:
        msg = "audit_chain.enabled is False; no sink is installed to verify"
        raise ServiceUnavailableError(msg)
    result = await sink.verify_chain()
    logger.info(
        API_AUDIT_CHAIN_VERIFY_REQUESTED,
        valid=result.valid,
        entries_checked=result.entries_checked,
    )
    return AuditChainVerificationResponse(
        valid=result.valid,
        entries_checked=result.entries_checked,
        first_break_position=result.first_break_position,
    )


class AuditChainController(Controller):
    """CEO-only on-demand audit-chain verification.

    Under ``/observability/audit-chain`` (the app router adds the
    ``/api/v1`` prefix).
    """

    path = "/observability/audit-chain"
    tags = ("observability",)
    guards = [require_ceo]  # noqa: RUF012

    @post(
        "/verify",
        guards=[
            per_op_rate_limit_from_policy(
                "observability.audit_chain_verify", key="user"
            ),
        ],
    )
    async def verify(self) -> ApiResponse[AuditChainVerificationResponse]:
        """Re-verify the live audit chain's hash continuity and signatures.

        Returns:
            The verification result (HTTP 200 either way; a broken chain
            is a valid answer, not a request failure).

        Raises:
            ServiceUnavailableError: When ``audit_chain.enabled`` is False,
                so no sink is installed to verify.
        """
        return ApiResponse(data=await _verify_live_chain())


__all__ = ["AuditChainController"]
