# module-kind: code
"""Security verdict and audit-chain recording."""

from synthorg.observability import get_logger
from synthorg.observability.events.security import SECURITY_AUDIT_CHAIN_VERIFY_OUTCOME
from synthorg.observability.prometheus_labels import (
    VALID_AUDIT_APPEND_STATUSES,
    VALID_AUDIT_VERIFICATION_OUTCOMES,
    VALID_VERDICTS,
    fold_auth_failure_reason,
    require_finite,
    require_label,
    require_non_negative,
)
from synthorg.observability.prometheus_recording._base import (
    _RecordingMetricsBase,
)

logger = get_logger(__name__)


class _SecurityRecordingMixin(_RecordingMetricsBase):
    """Security verdict and audit-chain recording."""

    def record_security_verdict(self, verdict: str) -> None:
        """Increment the security verdict counter.

        Called by a thin hook around ``SecOpsService.evaluate_pre_tool()``.

        Args:
            verdict: The verdict string -- one of ``"allow"``,
                ``"deny"``, ``"escalate"``, or ``"output_scan"``
                (see :data:`VALID_VERDICTS`).

        Raises:
            ValueError: If *verdict* is not in the allowed set.
        """
        require_label("security verdict", verdict, VALID_VERDICTS)
        self._security_evaluations.labels(verdict=verdict).inc()

    def record_auth_failure(self, *, reason: str) -> None:
        """Increment the auth-failure counter, folding *reason* to a bound.

        Wired at the auth rejection points (password verify, token
        validation, refresh reject, lockout). The free-form ``reason``
        folds to ``__other__`` so the label set stays bounded.
        """
        self._auth_failures.labels(reason=fold_auth_failure_reason(reason)).inc()

    def record_auth_lockout(self) -> None:
        """Increment the account-lockout counter (no labels)."""
        self._auth_lockouts.inc()

    def record_security_audit_fill_ratio(self, *, ratio: float) -> None:
        """Set the security audit log occupancy fraction.

        Args:
            ratio: ``len(_entries) / _max_entries`` of the
                ``AuditLog``; bounded in ``[0.0, 1.0]`` so a value
                near ``1.0`` signals imminent eviction.

        Raises:
            ValueError: If *ratio* is non-finite or outside [0.0, 1.0].
        """
        require_finite("record_security_audit_fill_ratio: ratio", ratio)
        if not 0.0 <= ratio <= 1.0:
            msg = f"ratio must be in [0.0, 1.0], got {ratio}"
            raise ValueError(msg)
        self._security_audit_log_fill_ratio.set(ratio)

    def record_audit_append(
        self,
        *,
        status: str,
        chain_depth: int,
        timestamp_unix: float,
    ) -> None:
        """Record an audit chain append event.

        Args:
            status: One of ``"signed"`` (TSA granted), ``"fallback"``
                (local clock), or ``"error"``.
            chain_depth: Hash chain length after the append.
            timestamp_unix: Unix epoch seconds of the append.

        Raises:
            ValueError: If *status* is not a valid value or
                *chain_depth* is negative.
        """
        require_label("audit append status", status, VALID_AUDIT_APPEND_STATUSES)
        require_non_negative("record_audit_append: chain_depth", chain_depth)
        require_finite("record_audit_append: timestamp_unix", timestamp_unix)
        self._audit_chain_appends.labels(status=status).inc()
        # An "error" status means no append happened (validation reject,
        # signing timeout, encode failure), so the depth / last-append
        # gauges must keep their prior values rather than being clobbered
        # to the placeholder ``0`` the error path passes.
        if status != "error":
            self._audit_chain_depth.set(chain_depth)
            self._audit_chain_last_append_ts.set(timestamp_unix)

    def record_audit_chain_verification(
        self,
        *,
        outcome: str,
        entries_checked: int,
        first_break_position: int | None = None,
    ) -> None:
        """Record an audit chain integrity verification.

        Increments once per ``verify_chain()`` call regardless of
        chain depth, so dashboards can compute a true per-call
        ``broken`` rate via
        ``rate(synthorg_audit_chain_verifications_total{outcome="broken"}[1h])``.

        Args:
            outcome: ``"valid"`` or ``"broken"``.
            entries_checked: Number of chain entries verified.
            first_break_position: Position of the first broken link
                (logged for offline incident triage; not labelled).

        Raises:
            ValueError: If *outcome* is invalid or *entries_checked*
                is negative.
        """
        require_label(
            "audit verification outcome",
            outcome,
            VALID_AUDIT_VERIFICATION_OUTCOMES,
        )
        require_non_negative(
            "record_audit_chain_verification: entries_checked",
            entries_checked,
        )
        if first_break_position is not None:
            require_non_negative(
                "record_audit_chain_verification: first_break_position",
                first_break_position,
            )
        self._audit_chain_verifications.labels(outcome=outcome).inc()
        logger.info(
            SECURITY_AUDIT_CHAIN_VERIFY_OUTCOME,
            outcome=outcome,
            entries_checked=entries_checked,
            first_break_position=first_break_position,
        )
