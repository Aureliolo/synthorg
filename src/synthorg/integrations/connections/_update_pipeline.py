"""Update-path helpers for :class:`ConnectionCatalog`.

PATCH candidate composition, the vendor-endpoint repair, and the
idempotent-no-op filter live in this mixin so the main catalog module
stays focused on orchestration, mirroring ``_create_pipeline``. The
mixin reaches back into the host catalog for ``_resolve_base_url``; the
``TYPE_CHECKING`` block declares that surface so ``mypy`` type-checks
the mixin in isolation.
"""

import copy
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from synthorg.core.critical_errors import reraise_critical
from synthorg.core.types import NotBlankStr
from synthorg.integrations.connections.http_vendor import METADATA_KEY_VENDOR
from synthorg.integrations.connections.models import Connection, ConnectionType
from synthorg.integrations.connections.repo_scope import validate_repo_scope_entry
from synthorg.integrations.errors import (
    InvalidConnectionEndpointError,
    InvalidRepoScopeError,
)
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.integrations import CONNECTION_VALIDATION_FAILED

logger = get_logger(__name__)


def _checked_scope_entry(entry: str) -> str:
    """Return ``entry`` once it passes repo-scope validation.

    Returns:
        The unchanged entry.

    Raises:
        InvalidRepoScopeError: When the entry is malformed or over-broad.
    """
    try:
        validate_repo_scope_entry(entry)
    except ValueError as exc:
        raise InvalidRepoScopeError(safe_error_description(exc)) from exc
    return entry


class _UnsetType:
    """Sentinel type for omitted PATCH fields.

    Defining a dedicated type lets ``mypy`` narrow ``value is _UNSET``
    properly; the previous ``| object`` annotation accepted any value
    and defeated narrowing in strict mode.
    """


_UNSET = _UnsetType()
"""Sentinel value to distinguish 'not provided' from None."""


def materialise_update(
    existing: Connection,
    candidate: dict[str, object],
) -> Connection | None:
    """Apply only the candidate fields that actually differ.

    An idempotent PATCH must not bump ``updated_at`` or emit a phantom
    ``CONNECTION_UPDATED`` audit row, so a candidate matching the persisted
    row in every field is reported as no change at all rather than saved.

    Returns:
        The updated row, or ``None`` when nothing actually changed.
    """
    real_updates = {
        key: value
        for key, value in candidate.items()
        if getattr(existing, key) != value
    }
    if not real_updates:
        return None
    real_updates["updated_at"] = datetime.now(UTC)
    # ``model_copy(update=...)`` skips ``@model_validator``s, so any nested
    # mutable container we pass in (here ``metadata``) would leak shared
    # references to callers post-construction. Deep-copy on the way in so
    # the persisted row owns its own mapping; matches the create path's
    # defensive deepcopy.
    if "metadata" in real_updates:
        real_updates["metadata"] = copy.deepcopy(real_updates["metadata"])
    return existing.model_copy(update=real_updates)


class ConnectionUpdateMixin:
    """Update-path helper methods mixed into :class:`ConnectionCatalog`."""

    if TYPE_CHECKING:

        def _resolve_base_url(
            self,
            connection_type: ConnectionType,
            base_url: str | None,
            metadata: dict[str, str] | None,
        ) -> str | None:
            """Supplied by :class:`ConnectionCreateMixin`."""
            ...

    def _candidate_or_report(
        self,
        name: str,
        *,
        base_url: str | _UnsetType | None,
        metadata: dict[str, str] | _UnsetType | None,
        health_check_enabled: bool | _UnsetType | None,
        webhook_receipt_retention_days: int | _UnsetType | None,
        sensitive: bool | _UnsetType,
        allowed_repos: tuple[str, ...] | _UnsetType,
    ) -> dict[str, object]:
        """Compose the PATCH candidate, attributing any rejection.

        Returns:
            The proposed update, seeded with no ``updated_at`` so an
            unchanged PATCH can still be recognised as a no-op.

        Raises:
            Exception: Whatever the field validation raised, once the
                rejection carries the connection it applied to.
        """
        try:
            return self._build_update_candidate(
                base_url=base_url,
                metadata=metadata,
                health_check_enabled=health_check_enabled,
                webhook_receipt_retention_days=webhook_receipt_retention_days,
                sensitive=sensitive,
                allowed_repos=allowed_repos,
            )
        except Exception as exc:
            reraise_critical(exc)
            # ``NotBlankStr`` rejections (e.g. caller passed an empty
            # ``base_url``) bubble with no resource attribution of their
            # own, so the audit log could not say WHICH PATCH was
            # rejected or why.
            logger.warning(
                CONNECTION_VALIDATION_FAILED,
                connection_name=name,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise

    def _build_update_candidate(
        self,
        *,
        base_url: str | _UnsetType | None,
        metadata: dict[str, str] | _UnsetType | None,
        health_check_enabled: bool | _UnsetType | None,
        webhook_receipt_retention_days: int | _UnsetType | None,
        sensitive: bool | _UnsetType,
        allowed_repos: tuple[str, ...] | _UnsetType,
    ) -> dict[str, object]:
        """Compose the PATCH candidate dict, normalising explicit nulls.

        The returned mapping is the proposed update *before* the
        idempotent-no-op filter compares it against the existing row.

        Returns:
            A dict of only the fields whose new values were explicitly
            supplied (omitting ``_UNSET`` sentinels).
        """
        candidate: dict[str, object] = {}
        if base_url is not _UNSET:
            candidate["base_url"] = NotBlankStr(base_url) if base_url else None
        if metadata is not _UNSET:
            # Normalise explicit ``null`` to the canonical empty
            # mapping used by ``create()``; ``model_copy`` does
            # not re-run validators so a raw ``None`` would
            # persist as ``metadata=None`` on the row even
            # though ``Connection.metadata`` is typed
            # ``dict[str, str]``.
            candidate["metadata"] = metadata if metadata is not None else {}
        if health_check_enabled is not _UNSET:
            # Same reasoning as ``metadata`` above;
            # ``create()`` always materialises
            # ``health_check_enabled=True`` so an explicit-null
            # clear normalises to the same default.
            candidate["health_check_enabled"] = (
                health_check_enabled if health_check_enabled is not None else True
            )
        if webhook_receipt_retention_days is not _UNSET:
            # ``None`` is a meaningful value here -- it clears the
            # per-connection override and falls back to the global
            # default.  Pass through verbatim.
            candidate["webhook_receipt_retention_days"] = webhook_receipt_retention_days
        if sensitive is not _UNSET:
            candidate["sensitive"] = sensitive
        if not isinstance(allowed_repos, _UnsetType):
            # The scope is a security boundary, so it is validated here at
            # the persistence entry rather than only in the API DTO: any
            # other caller of update() would otherwise be able to persist
            # an over-broad entry that the forge tools then honour.
            candidate["allowed_repos"] = tuple(
                NotBlankStr(_checked_scope_entry(r)) for r in allowed_repos
            )
        return candidate

    def _repair_vendor_base_url(
        self,
        existing: Connection,
        candidate: dict[str, object],
    ) -> None:
        """Keep a generic-HTTP endpoint honest across a PATCH.

        A vendor preset hides the base-URL field, so the form submits an
        explicit null for it on every save. Taken literally that clears the
        endpoint of a working connection and leaves no way to restore it,
        since the field stays hidden. The endpoint is mandatory for this
        type, so a null is never a meaningful value: re-derive it from the
        vendor the update actually declares.

        The stored endpoint is only a safe fallback while the vendor is
        unchanged. Carrying it across a vendor switch would persist a
        connection labelled for one service and pointed at another, which
        no later read can detect, so a switch onto a vendor with no
        endpoint of its own is refused instead.

        Raises:
            InvalidConnectionEndpointError: If the update moves the
                connection onto a vendor that supplies no endpoint and
                names no replacement.
        """
        if existing.connection_type is not ConnectionType.GENERIC_HTTP:
            return
        if candidate.get("base_url"):
            return
        declared = candidate.get("metadata", existing.metadata)
        metadata = declared if isinstance(declared, dict) else None
        # Compared as declared labels, not as resolved presets: ``resolve_vendor``
        # answers ``None`` for "custom" AND for anything it does not recognise,
        # so comparing presets would read a switch between two such labels as no
        # change at all -- and leave the old endpoint attached to the new label,
        # the exact mislabelling the refusal below exists to prevent.
        vendor_changed = (metadata or {}).get(METADATA_KEY_VENDOR, "") != (
            existing.metadata.get(METADATA_KEY_VENDOR, "")
        )
        # An absent key with an unchanged vendor is simply a PATCH that does
        # not concern the endpoint. An absent key across a vendor switch is
        # not: leaving it alone would strand the previous vendor's URL just
        # as surely as honouring a null would.
        if "base_url" not in candidate and not vendor_changed:
            return
        resolved = self._resolve_base_url(existing.connection_type, None, metadata)
        if resolved:
            candidate["base_url"] = NotBlankStr(resolved)
            return
        if vendor_changed:
            msg = (
                "Changing vendor requires a base_url: the new vendor supplies "
                "no endpoint of its own and the previous vendor's endpoint "
                "does not carry over"
            )
            raise InvalidConnectionEndpointError(msg)
        candidate["base_url"] = existing.base_url
