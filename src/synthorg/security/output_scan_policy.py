"""Output scan response policies.

Pluggable strategies that transform ``OutputScanResult`` after the
output scanner runs.  Each policy decides how to handle detected
sensitive data — redact, withhold, log-only, or delegate based on
autonomy level.
"""

from types import MappingProxyType
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from synthorg.core.enums import AutonomyLevel
from synthorg.observability import get_logger
from synthorg.observability.events.security import (
    SECURITY_OUTPUT_SCAN_POLICY_APPLIED,
)
from synthorg.security.models import OutputScanResult, ScanOutcome

if TYPE_CHECKING:
    from collections.abc import Mapping

    from synthorg.security.autonomy.models import EffectiveAutonomy
    from synthorg.security.models import SecurityContext

logger = get_logger(__name__)


@runtime_checkable
class OutputScanResponsePolicy(Protocol):
    """Protocol for output scan response policies.

    Implementations decide how to transform an ``OutputScanResult``
    before it is returned to the invoker.

    Implementations are expected to be stateless / immutable — the
    ``AutonomyTieredPolicy`` stores policy instances by reference
    (shallow copy) and wraps the mapping as read-only.
    """

    @property
    def name(self) -> str:
        """Policy name identifier."""
        ...

    def apply(
        self,
        scan_result: OutputScanResult,
        context: SecurityContext,
    ) -> OutputScanResult:
        """Apply the policy to a scan result.

        Args:
            scan_result: Result from the output scanner.
            context: Security context of the tool invocation.

        Returns:
            Transformed scan result.
        """
        ...


class RedactPolicy:
    """Return scan result as-is (redacted content preserved).

    This is the default policy — the scanner's redaction stands.
    """

    @property
    def name(self) -> str:
        """Policy name identifier."""
        return "redact"

    def apply(
        self,
        scan_result: OutputScanResult,
        context: SecurityContext,  # noqa: ARG002
    ) -> OutputScanResult:
        """Pass through the scan result unchanged.

        Args:
            scan_result: Result from the output scanner.
            context: Security context (unused).

        Returns:
            The original scan result.
        """
        logger.debug(
            SECURITY_OUTPUT_SCAN_POLICY_APPLIED,
            policy="redact",
            has_sensitive_data=scan_result.has_sensitive_data,
        )
        return scan_result


class WithholdPolicy:
    """Clear redacted content when sensitive data is found.

    Sets ``ScanOutcome.WITHHELD`` so the invoker returns a dedicated
    "withheld by policy" error — no partial data is returned.  This
    is distinct from the fail-closed path used for scanner errors.
    The ``findings`` tuple is deliberately preserved so that audit
    consumers can categorise what was detected without seeing the
    actual content.
    """

    @property
    def name(self) -> str:
        """Policy name identifier."""
        return "withhold"

    def apply(
        self,
        scan_result: OutputScanResult,
        context: SecurityContext,  # noqa: ARG002
    ) -> OutputScanResult:
        """Clear redacted content on sensitive results.

        Args:
            scan_result: Result from the output scanner.
            context: Security context (unused).

        Returns:
            Scan result with ``redacted_content`` cleared if sensitive.
        """
        logger.debug(
            SECURITY_OUTPUT_SCAN_POLICY_APPLIED,
            policy="withhold",
            has_sensitive_data=scan_result.has_sensitive_data,
        )
        if not scan_result.has_sensitive_data:
            return scan_result
        return scan_result.model_copy(
            update={"redacted_content": None, "outcome": ScanOutcome.WITHHELD},
        )


class LogOnlyPolicy:
    """Discard scan findings, returning a clean result.

    The caller should treat the original tool output as unmodified.
    Suitable for audit-only mode or high-trust agents where output
    scanning is informational rather than enforced.  The audit entry
    written by ``SecOpsService.scan_output`` before this policy runs
    preserves the original findings.
    """

    @property
    def name(self) -> str:
        """Policy name identifier."""
        return "log_only"

    def apply(
        self,
        scan_result: OutputScanResult,
        context: SecurityContext,
    ) -> OutputScanResult:
        """Return a clean ``OutputScanResult`` regardless of findings.

        Suppresses enforcement while preserving the audit log entry
        written by ``SecOpsService.scan_output``.

        Args:
            scan_result: Result from the output scanner.
            context: Security context of the tool invocation.

        Returns:
            Clean ``OutputScanResult`` with ``has_sensitive_data=False``.
        """
        if scan_result.has_sensitive_data:
            logger.warning(
                SECURITY_OUTPUT_SCAN_POLICY_APPLIED,
                policy="log_only",
                has_sensitive_data=True,
                findings=scan_result.findings,
                tool_name=context.tool_name,
                agent_id=context.agent_id,
                note="Sensitive data detected but passed through by log_only policy",
            )
            return OutputScanResult(outcome=ScanOutcome.LOG_ONLY)
        logger.debug(
            SECURITY_OUTPUT_SCAN_POLICY_APPLIED,
            policy="log_only",
            has_sensitive_data=False,
        )
        return OutputScanResult()


# Default autonomy-to-policy mapping (read-only).
_DEFAULT_AUTONOMY_POLICY_MAP: Mapping[AutonomyLevel, OutputScanResponsePolicy] = (
    MappingProxyType(
        {
            AutonomyLevel.FULL: LogOnlyPolicy(),
            AutonomyLevel.SEMI: RedactPolicy(),
            AutonomyLevel.SUPERVISED: RedactPolicy(),
            AutonomyLevel.LOCKED: WithholdPolicy(),
        }
    )
)


# Intentional import-time validation: ensures the default policy map
# covers every AutonomyLevel member.  If a new level is added to the
# enum without updating the map, this fails loudly at module load
# rather than silently falling back at runtime.  A companion unit test
# (test_default_map_covers_all_autonomy_levels) also asserts this in CI.
_expected_levels = set(AutonomyLevel)
_mapped_levels = set(_DEFAULT_AUTONOMY_POLICY_MAP.keys())
if _mapped_levels != _expected_levels:
    _msg = (
        f"_DEFAULT_AUTONOMY_POLICY_MAP is out of sync with AutonomyLevel: "
        f"missing={_expected_levels - _mapped_levels}, "
        f"extra={_mapped_levels - _expected_levels}"
    )
    raise RuntimeError(_msg)
del _expected_levels, _mapped_levels


class AutonomyTieredPolicy:
    """Delegate to sub-policies based on the effective autonomy level.

    Uses a configurable mapping from ``AutonomyLevel`` to a concrete
    policy.  Falls back to ``RedactPolicy`` when no autonomy is set
    or when the autonomy level has no entry in the policy map.
    """

    def __init__(
        self,
        *,
        effective_autonomy: EffectiveAutonomy | None,
        policy_map: Mapping[AutonomyLevel, OutputScanResponsePolicy] | None = None,
    ) -> None:
        """Initialize with autonomy and optional policy map.

        Args:
            effective_autonomy: Resolved autonomy for the current run.
            policy_map: Mapping from autonomy level to policy. Uses
                defaults when ``None``.
        """
        self._effective_autonomy = effective_autonomy
        raw = policy_map if policy_map is not None else _DEFAULT_AUTONOMY_POLICY_MAP
        # Shallow copy decouples from the caller's mapping; MappingProxyType
        # prevents mutation.  Policy instances are treated as immutable /
        # stateless, so deep-copying is unnecessary and would break callers
        # passing non-copyable policies (mocks, policies with resources).
        self._policy_map: Mapping[AutonomyLevel, OutputScanResponsePolicy] = (
            MappingProxyType(dict(raw))
        )
        self._fallback: OutputScanResponsePolicy = RedactPolicy()

    @property
    def name(self) -> str:
        """Policy name identifier."""
        return "autonomy_tiered"

    def apply(
        self,
        scan_result: OutputScanResult,
        context: SecurityContext,
    ) -> OutputScanResult:
        """Delegate to the sub-policy for the current autonomy level.

        Args:
            scan_result: Result from the output scanner.
            context: Security context of the tool invocation.

        Returns:
            Transformed scan result from the delegated policy.
        """
        if self._effective_autonomy is None:
            delegate = self._fallback
            autonomy_level = None
        else:
            autonomy_level = self._effective_autonomy.level
            mapped = self._policy_map.get(autonomy_level)
            if mapped is not None:
                delegate = mapped
            else:
                delegate = self._fallback
                logger.warning(
                    SECURITY_OUTPUT_SCAN_POLICY_APPLIED,
                    policy="autonomy_tiered",
                    autonomy_level=autonomy_level.value,
                    fallback_to=self._fallback.name,
                    note="No policy mapped for autonomy level — falling back",
                )

        logger.debug(
            SECURITY_OUTPUT_SCAN_POLICY_APPLIED,
            policy="autonomy_tiered",
            delegate=delegate.name,
            autonomy_level=(
                autonomy_level.value if autonomy_level is not None else None
            ),
        )
        return delegate.apply(scan_result, context)
