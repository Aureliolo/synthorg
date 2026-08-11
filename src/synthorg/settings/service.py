# module-kind: complex_service
"""Settings service: resolution, validation, caching, and notifications.

Provides the central service layer that merges setting values from
three sources in priority order: DB > env > code default. For settings
flagged ``compose_set=True`` the DB tier is bypassed and the chain
collapses to env > default.

One cohesive responsibility: the setting-value lifecycle. Caching
(for non-sensitive entries), encryption (for sensitive entries),
compose-set bypass, audit-namespace tagging, and bus-based change
notifications are all facets of "resolve / persist / notify a setting
value" with shared invariants (the registry, the repository, the
encryptor, the version semantics); splitting them fragments the
single audit chain operators rely on.
"""

import os
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Final

from synthorg.communication.bus_protocol import MessageBus
from synthorg.communication.enums import MessageType
from synthorg.communication.message import Message, MessageMetadata, TextPart
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.iso_datetime import now_iso_utc
from synthorg.core.types import NotBlankStr
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.settings import (
    SETTINGS_CACHE_INVALIDATED,
    SETTINGS_DELETE_FAILED,
    SETTINGS_ENCRYPTION_ERROR,
    SETTINGS_NOT_FOUND,
    SETTINGS_NOTIFICATION_FAILED,
    SETTINGS_NOTIFICATION_PUBLISHED,
    SETTINGS_VALIDATION_FAILED,
    SETTINGS_VALUE_DELETED,
    SETTINGS_VALUE_RESOLVED,
    SETTINGS_VALUE_SET,
    SETTINGS_VERSION_CONFLICT,
)
from synthorg.observability.metrics_hub import record_settings_mutation
from synthorg.observability.tracing.instrumentation import get_tracer
from synthorg.persistence.settings_protocol import SettingRow, SettingsRepository
from synthorg.settings._cross_field_context import guard_cross_field_rules
from synthorg.settings._setting_audit import emit_security_setting_changed
from synthorg.settings._value_rules import (
    SENSITIVE_MASK,
    env_var_name,
    reject_if_read_only,
    validate_value,
)
from synthorg.settings.encryption import SettingsEncryptor
from synthorg.settings.enums import SettingsImportSource, SettingSource
from synthorg.settings.errors import (
    SettingNotFoundError,
    SettingsEncryptionError,
    SettingValidationError,
)
from synthorg.settings.models import (
    SettingDefinition,
    SettingEntry,
    SettingValue,
)
from synthorg.settings.registry import SettingsRegistry
from synthorg.settings.write_governance import (
    SettingsWriteGovernance,
    guard_security_delete,
    guard_security_writes,
)

logger = get_logger(__name__)
_tracer = get_tracer(__name__)

# Only explicitly-overridden settings have a row, so one page covers every
# deployment; the bound exists so a corrupted table cannot stream unboundedly.
ALL_OVERRIDES_LIMIT: Final[int] = 10_000


def _warn_if_overrides_truncated(rows: Sequence[SettingRow], *, source: str) -> None:
    """Warn when the override fetch may have hit its bound.

    A full page means the read cannot prove it saw every override, and the
    ones past the bound resolve as though they were never set: the dashboard
    would show a default the system is not enforcing, silently.

    Args:
        rows: The rows the bounded read returned.
        source: The calling read, for the log.
    """
    if len(rows) < ALL_OVERRIDES_LIMIT:
        return
    logger.warning(
        SETTINGS_VALIDATION_FAILED,
        action=source,
        reason="override_read_hit_limit",
        limit=ALL_OVERRIDES_LIMIT,
    )


class SettingsService:
    """Central settings service with resolution, cache, and notifications.

    Resolution order (highest priority first):
    1. Database overrides (user-set via API/UI)
    2. Environment variables (``SYNTHORG_{NAMESPACE}_{KEY}`` or
       ``env_var_override``)
    3. Code defaults (from ``SettingDefinition.default``)

    For settings flagged ``compose_set=True`` the DB tier is bypassed
    and the chain collapses to env > default.

    The cache stores only non-sensitive DB values.  Sensitive values
    are decrypted on every read to avoid holding plaintext secrets
    in memory.

    Args:
        repository: Persistence repository for DB settings.
        registry: Setting metadata registry.
        encryptor: Optional encryptor for sensitive settings.
        message_bus: Optional message bus for change notifications.
    """

    def __init__(
        self,
        *,
        repository: SettingsRepository,
        registry: SettingsRegistry,
        encryptor: SettingsEncryptor | None = None,
        message_bus: MessageBus | None = None,
    ) -> None:
        self._repository = repository
        self._registry = registry
        self._encryptor = encryptor
        self._message_bus = message_bus
        self._cache: dict[tuple[str, str], SettingValue] = {}

    @property
    def registry(self) -> SettingsRegistry:
        """Read-only access to the registry for callers that need definitions."""
        return self._registry

    async def _emit_resolved(
        self,
        definition: SettingDefinition,
        source: str,
    ) -> None:
        """Log a setting resolution at DEBUG.

        A resolution always succeeds (it resolves to some source), so it stays
        at DEBUG rather than flooding startup with one INFO line per setting.
        Problems -- a feature that cannot activate, an unwired dependency --
        surface through their own INFO/WARNING events, not here.
        """
        logger.debug(
            SETTINGS_VALUE_RESOLVED,
            namespace=definition.namespace,
            key=definition.key,
            source=source,
        )

    async def _resolve_db(
        self,
        definition: SettingDefinition,
    ) -> SettingValue | None:
        """Fetch a setting from the DB and decrypt if sensitive.

        Shared pipeline used by both ``get()`` and ``get_versioned()``
        so the two APIs never drift on how sensitive values are
        decoded.  Returns ``None`` when the DB has no row for the
        key; raises ``SettingsEncryptionError`` when a sensitive
        setting cannot be decrypted.

        Returns:
            A ``SettingValue`` sourced from the DB (sensitive values
            already decrypted), or ``None`` when no DB row exists for
            the key.

        Raises:
            SettingsEncryptionError: If a DB row exists but its
                sensitive value cannot be decrypted (no encryptor
                configured, or decryption failed).
        """
        result = await self._repository.get(
            (NotBlankStr(definition.namespace), NotBlankStr(definition.key)),
        )
        if result is None:
            return None
        value = self._decrypt_if_sensitive(definition, result.value)
        return SettingValue(
            namespace=definition.namespace,
            key=definition.key,
            value=value,
            source=SettingSource.DATABASE,
            updated_at=result.updated_at,
        )

    def _decrypt_if_sensitive(
        self,
        definition: SettingDefinition,
        raw_value: str,
    ) -> str:
        """Decrypt ``raw_value`` for sensitive settings, else return as-is.

        Returns:
            The decrypted plaintext for sensitive settings, or
            ``raw_value`` unchanged for non-sensitive settings.

        Raises:
            SettingsEncryptionError: If the setting is sensitive but no
                encryptor is configured, or the encryptor's
                ``decrypt()`` call fails.
        """
        if not definition.sensitive:
            return raw_value
        if self._encryptor is None:
            logger.error(
                SETTINGS_ENCRYPTION_ERROR,
                namespace=definition.namespace,
                key=definition.key,
                reason="no_encryptor_on_read",
            )
            msg = (
                f"Cannot decrypt sensitive setting "
                f"{definition.namespace}/{definition.key}: no encryptor"
            )
            raise SettingsEncryptionError(msg)
        try:
            return self._encryptor.decrypt(raw_value)
        except SettingsEncryptionError as exc:
            # Settings encryption is a credential-bearing path;
            # ``logger.exception`` would attach a traceback that may
            # leak the encrypted payload or the encryptor's internal
            # state via the cryptography exception chain. Use
            # ``logger.warning`` with ``safe_error_description`` per
            # CLAUDE.md ``## Logging``.
            logger.warning(
                SETTINGS_ENCRYPTION_ERROR,
                namespace=definition.namespace,
                key=definition.key,
                reason="decrypt_failed",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise

    async def get(self, namespace: str, key: str) -> SettingValue:
        """Resolve a setting value through the priority chain.

        Args:
            namespace: Setting namespace.
            key: Setting key.

        Returns:
            Resolved setting value with source information.

        Raises:
            SettingNotFoundError: If the key is not in the registry.
        """
        definition = self._registry.get(namespace, key)
        if definition is None:
            logger.warning(SETTINGS_NOT_FOUND, namespace=namespace, key=key)
            msg = f"Unknown setting: {namespace}/{key}"
            raise SettingNotFoundError(msg)

        # Cache check (sensitive values are never cached)
        cache_key = (namespace, key)
        if not definition.sensitive:
            cached = self._cache.get(cache_key)
            if cached is not None:
                return cached

        # Compose-set entries come from the deployment; a DB row (left over
        # from an earlier schema or a stale ops mistake) MUST NOT be
        # consulted, otherwise the running process would surface a value it
        # is not actually using.
        if not definition.compose_set:
            setting_value = await self._resolve_db(definition)
            if setting_value is not None:
                # Direct dict item assignment, not a {**self._cache, k: v}
                # copy-on-write spread: the spread would read a stale
                # snapshot after the await above and clobber a concurrent
                # TaskGroup writer's entry (TOCTOU). Under asyncio's
                # cooperative concurrency, a single dict item assignment is
                # one opcode and safe without a lock.
                if not definition.sensitive:
                    self._cache[cache_key] = setting_value
                await self._emit_resolved(definition, source="db")
                return setting_value

        fallback = await self._resolve_fallback(definition)
        # Compose-set: cache the first-read snapshot so subsequent
        # ``/settings`` queries and ``SETTINGS_VALUE_RESOLVED`` events report
        # the *same* value the runtime captured at boot. Without it, a
        # mid-process mutation of ``os.environ`` or the in-memory config
        # object would let the resolved value drift away from what middleware
        # / NATS clients / worker pools locked in at startup, and
        # ``/settings`` would lie about reality. Sensitive compose-set values
        # are still bypassed (they should never survive in cache).
        if definition.compose_set and not definition.sensitive:
            self._cache[cache_key] = fallback
        return fallback

    async def get_entry(self, namespace: str, key: str) -> SettingEntry:
        """Resolve a setting and return it with its definition.

        Args:
            namespace: Setting namespace.
            key: Setting key.

        Returns:
            Combined entry with definition, value, and source.

        Raises:
            SettingNotFoundError: If the key is not in the registry.
        """
        # get() performs the registry check and raises SettingNotFoundError
        value = await self.get(namespace, key)
        definition = self._registry.get(namespace, key)
        assert definition is not None  # noqa: S101 -- get() guarantees
        display_value = SENSITIVE_MASK if definition.sensitive else value.value
        return SettingEntry(
            definition=definition,
            value=display_value,
            source=value.source,
            updated_at=value.updated_at,
        )

    async def get_namespace(self, namespace: str) -> tuple[SettingEntry, ...]:
        """Resolve all settings in a namespace.

        Uses the repository's batch method to avoid N+1 DB queries.

        Args:
            namespace: Setting namespace.

        Returns:
            All setting entries in the namespace, sorted by key.
        """
        definitions = self._registry.list_namespace(namespace)
        if not definitions:
            return ()

        # Batch-fetch all DB values for this namespace in one query.
        db_rows = await self._repository.get_namespace(
            NotBlankStr(namespace),
        )
        db_lookup: dict[str, tuple[str, str]] = {
            row.key: (row.value, row.updated_at) for row in db_rows
        }

        entries: list[SettingEntry] = []
        for defn in definitions:
            entry = await self._resolve_with_db_lookup(defn, db_lookup.get(defn.key))
            entries.append(entry)
        return tuple(entries)

    async def get_all(self) -> tuple[SettingEntry, ...]:
        """Resolve all settings across all namespaces.

        Uses the repository's batch method to avoid N+1 DB queries.

        Returns:
            All setting entries, sorted by namespace then key.
        """
        definitions = self._registry.list_all()
        if not definitions:
            return ()

        # Batch-fetch all DB values in one query.
        db_rows = await self._repository.list_items(limit=ALL_OVERRIDES_LIMIT)
        _warn_if_overrides_truncated(db_rows, source="get_all")
        db_lookup: dict[tuple[str, str], tuple[str, str]] = {
            (row.namespace, row.key): (row.value, row.updated_at) for row in db_rows
        }

        entries: list[SettingEntry] = []
        for defn in definitions:
            db_hit = db_lookup.get((defn.namespace, defn.key))
            entry = await self._resolve_with_db_lookup(defn, db_hit)
            entries.append(entry)
        return tuple(entries)

    async def get_page(
        self,
        *,
        after_key: str | None,
        limit: int,
    ) -> tuple[tuple[SettingEntry, ...], bool]:
        """Resolve a single keyset page of settings sorted by ``namespace:key``.

        Slices the in-memory definition registry before resolving DB
        overrides, so the controller only pays the resolve cost for
        the rows it actually returns.  Cursor pages are keyset-stable
        on ``f"{namespace}:{key}"``: a new override or definition
        added between requests does not duplicate or skip rows the
        client has already seen.

        Args:
            after_key: ``None`` for the first page; the previous
                page's last ``f"{namespace}:{key}"`` for follow-up
                pages.
            limit: Page size requested.

        Returns:
            ``(page, has_more)`` where ``page`` is at most ``limit``
            entries in ``(namespace, key)`` order and ``has_more`` is
            ``True`` when an additional definition was observed past
            the requested page.
        """
        sorted_defs = sorted(
            self._registry.list_all(),
            key=lambda d: (d.namespace, d.key),
        )
        if after_key is not None:
            sorted_defs = [
                d for d in sorted_defs if f"{d.namespace}:{d.key}" > after_key
            ]
        # Over-fetch by one to detect has_more without a separate count.
        page_defs = sorted_defs[: limit + 1]
        has_more = len(page_defs) > limit
        page_defs = page_defs[:limit]
        if not page_defs:
            return (), has_more

        # Single DB round-trip for the override values; bounded by the
        # number of overridden settings (typically << total definition
        # count) so we keep the existing batch shape.
        db_rows = await self._repository.list_items(limit=ALL_OVERRIDES_LIMIT)
        _warn_if_overrides_truncated(db_rows, source="get_page")
        db_lookup: dict[tuple[str, str], tuple[str, str]] = {
            (row.namespace, row.key): (row.value, row.updated_at) for row in db_rows
        }
        resolved: list[SettingEntry] = []
        for defn in page_defs:
            entry = await self._resolve_with_db_lookup(
                defn,
                db_lookup.get((defn.namespace, defn.key)),
            )
            resolved.append(entry)
        return tuple(resolved), has_more

    async def _resolve_fallback(
        self,
        definition: SettingDefinition,
    ) -> SettingValue:
        """Resolve via env > code default (no DB lookup).

        Returns:
            A ``SettingValue`` resolved from the environment variable or
            code default (never the DB), with ``source`` set to
            ``ENVIRONMENT`` or ``DEFAULT``.
        """
        ns = definition.namespace
        key = definition.key

        env_name = (
            definition.env_var_override
            if definition.env_var_override is not None
            else env_var_name(ns, key)
        )
        env_val = os.environ.get(env_name)
        if env_val is not None:
            await self._emit_resolved(definition, source="env")
            return SettingValue(
                namespace=ns,
                key=key,
                value=env_val,
                source=SettingSource.ENVIRONMENT,
            )

        # default=None means "optional, no built-in default". Return
        # empty string as a sentinel (callers like ConfigResolver.get_int
        # raise ValueError on empty, giving a clear error at the
        # consumer layer rather than here).
        default = definition.default if definition.default is not None else ""
        await self._emit_resolved(definition, source="default")
        return SettingValue(
            namespace=ns,
            key=key,
            value=default,
            source=SettingSource.DEFAULT,
        )

    async def _resolve_with_db_lookup(
        self,
        definition: SettingDefinition,
        db_hit: tuple[str, str] | None,
    ) -> SettingEntry:
        """Resolve a single setting entry using a pre-fetched DB value.

        This is a synchronous helper for batch operations.  It does
        not check the cache (batch callers skip the cache).

        Returns:
            A ``SettingEntry`` combining the definition with the
            resolved value (from the pre-fetched DB row, env, or
            default), with sensitive values masked.
        """
        ns = definition.namespace
        key = definition.key

        # Compose-set: ignore any DB row (mirrors the per-key ``get()``
        # short-circuit so batch reads do not surface a stale override).
        if definition.compose_set:
            db_hit = None

        if db_hit is not None:
            raw_value, updated_at = db_hit
            value = raw_value
            if definition.sensitive:
                if self._encryptor is None:
                    logger.error(
                        SETTINGS_ENCRYPTION_ERROR,
                        namespace=ns,
                        key=key,
                        reason="no_encryptor_on_read",
                    )
                    return SettingEntry(
                        definition=definition,
                        value=SENSITIVE_MASK,
                        source=SettingSource.DATABASE,
                        updated_at=updated_at,
                    )
                try:
                    value = self._encryptor.decrypt(raw_value)
                except SettingsEncryptionError:
                    logger.warning(
                        SETTINGS_ENCRYPTION_ERROR,
                        namespace=ns,
                        key=key,
                        reason="decrypt_failed_in_batch",
                    )
                    return SettingEntry(
                        definition=definition,
                        value=SENSITIVE_MASK,
                        source=SettingSource.DATABASE,
                        updated_at=updated_at,
                    )
            display = SENSITIVE_MASK if definition.sensitive else value
            await self._emit_resolved(definition, source="db")
            return SettingEntry(
                definition=definition,
                value=display,
                source=SettingSource.DATABASE,
                updated_at=updated_at,
            )

        # Fall back to env, then default
        fallback = await self._resolve_fallback(definition)
        display = SENSITIVE_MASK if definition.sensitive else fallback.value
        return SettingEntry(
            definition=definition,
            value=display,
            source=fallback.source,
            updated_at=fallback.updated_at,
        )

    def _invalidate_cache(self, namespace: str, key: str) -> None:
        """Remove a key from the settings cache."""
        self._cache.pop((namespace, key), None)
        logger.debug(SETTINGS_CACHE_INVALIDATED, namespace=namespace, key=key)

    def _invalidate_namespace_cache(self, namespace: str) -> None:
        """Drop every cache entry under *namespace*."""
        self._cache = {k: v for k, v in self._cache.items() if k[0] != namespace}
        logger.debug(SETTINGS_CACHE_INVALIDATED, namespace=namespace)

    async def get_versioned(
        self,
        namespace: str,
        key: str,
    ) -> tuple[str, str]:
        """Read a setting value and its ``updated_at`` for CAS.

        Shares the ``_resolve_db`` pipeline with ``get()`` so
        sensitive values come back decrypted.  Bypasses cache and
        fallback chain -- CAS callers only care about DB state.
        Returns ``("", "")`` when the setting has no DB override
        (first-write sentinel) or the key is not in the registry.

        Returns:
            A ``(value, updated_at)`` string pair from the DB row for
            CAS preflight; ``("", "")`` when no DB override exists, the
            key is unregistered, or the setting is ``compose_set``.
        """
        definition = self._registry.get(namespace, key)
        if definition is None:
            return "", ""
        # Compose-set entries are never written via the service, so CAS
        # callers must observe the same "no DB override" sentinel the read
        # path returns: a stale row here would let a CAS preflight succeed
        # against a value the runtime does not honour.
        if definition.compose_set:
            return "", ""
        setting_value = await self._resolve_db(definition)
        if setting_value is None:
            return "", ""
        return setting_value.value, setting_value.updated_at or ""

    async def set(
        self,
        namespace: str,
        key: str,
        value: str,
        *,
        expected_updated_at: str | None = None,
        import_source: SettingsImportSource = SettingsImportSource.DIRECT_SET,
        governance: SettingsWriteGovernance | None = None,
    ) -> SettingEntry:
        """Span-wrapped public entry point for a setting write.

        The ``settings.set`` span carries only namespace/key, never the
        value (which may be a secret); record_exception / set_status are
        off so exception frame-locals are not serialised.

        ``governance`` carries the deliberate confirm + reason + actor a
        security-weakening transition requires (see
        :mod:`synthorg.settings.write_governance`); the enable / tighten
        direction ignores it.

        Returns:
            The persisted ``SettingEntry`` from :meth:`_set`.
        """
        with _tracer.start_as_current_span(
            "settings.set",
            attributes={"settings.namespace": namespace, "settings.key": key},
            record_exception=False,
            set_status_on_exception=False,
        ):
            return await self._set(
                namespace,
                key,
                value,
                expected_updated_at=expected_updated_at,
                import_source=import_source,
                governance=governance,
            )

    async def _set(
        self,
        namespace: str,
        key: str,
        value: str,
        *,
        expected_updated_at: str | None = None,
        import_source: SettingsImportSource = SettingsImportSource.DIRECT_SET,
        governance: SettingsWriteGovernance | None = None,
    ) -> SettingEntry:
        """Validate, encrypt, and persist a setting value with optional CAS.

        Call only from :meth:`set`; every caller must route through the
        span-wrapped public method so the write stays traced.

        Pass ``expected_updated_at=""`` for first-write semantics.
        Raises ``VersionConflictError`` on CAS miss,
        ``SettingNotFoundError`` / ``SettingValidationError`` /
        ``SettingsEncryptionError`` on preflight failures.

        ``import_source`` distinguishes how this write entered the
        service so ``SETTINGS_VALIDATION_FAILED`` log records show
        whether a malformed value came from an API body, file
        upload, config merge, or direct set.  Defaults to
        ``DIRECT_SET`` for in-process callers.

        Returns:
            A ``SettingEntry`` reflecting the newly persisted value
            (sensitive values masked) with ``source=DATABASE``.

        Raises:
            VersionConflictError: If ``expected_updated_at`` was
                supplied but the DB row's current ``updated_at`` does
                not match (concurrent modification).
            SettingNotFoundError: If the namespace/key pair is not in
                the registry.
            SettingValidationError: If the value fails type or pattern
                validation.
            SettingsEncryptionError: If a sensitive value cannot be
                encrypted.
        """
        definition = self._registry.get(namespace, key)
        if definition is None:
            logger.warning(SETTINGS_NOT_FOUND, namespace=namespace, key=key)
            msg = f"Unknown setting: {namespace}/{key}"
            raise SettingNotFoundError(msg)

        reject_if_read_only(
            definition,
            action="set",
            import_source=import_source,
        )

        # Ahead of both guards: a cross-field rule parses the pending value to
        # compare it, so an unvalidated one reaches it as an unparseable
        # string the rule can only skip, and the malformed input is reported
        # by whichever check happens to run last rather than by the type
        # validator that actually owns it.
        try:
            validate_value(definition, value)
        except SettingValidationError as exc:
            logger.warning(
                SETTINGS_VALIDATION_FAILED,
                namespace=namespace,
                key=key,
                import_source=import_source.value,
                reason=safe_error_description(exc),
            )
            raise

        await guard_security_writes(
            [(namespace, key, value)],
            governance=governance,
            get_entry=self.get,
        )
        await self._guard_cross_field_rules([(namespace, key, value)])

        store_value = self._encrypt_if_sensitive(definition, value)
        updated_at = now_iso_utc()
        entity = SettingRow(
            namespace=NotBlankStr(namespace),
            key=NotBlankStr(key),
            value=store_value,
            updated_at=updated_at,
        )
        if expected_updated_at is not None:
            written = await self._repository.set_if_unchanged(
                entity,
                expected_updated_at=expected_updated_at,
            )
        else:
            await self._repository.save(entity)
            written = True
        if not written:
            from synthorg.core.domain_errors import (  # noqa: PLC0415
                VersionConflictError,
            )

            logger.warning(
                SETTINGS_VERSION_CONFLICT,
                namespace=namespace,
                key=key,
                reason="concurrent_modification",
                expected_updated_at=expected_updated_at,
            )
            msg = f"Concurrent modification on {namespace}/{key}"
            raise VersionConflictError(msg)

        self._invalidate_cache(namespace, key)
        logger.info(SETTINGS_VALUE_SET, namespace=namespace, key=key)
        record_settings_mutation(namespace=namespace)
        emit_security_setting_changed(namespace, key=key, action_type="set")
        await self._publish_change(namespace, key)

        display_value = SENSITIVE_MASK if definition.sensitive else value
        return SettingEntry(
            definition=definition,
            value=display_value,
            source=SettingSource.DATABASE,
            updated_at=updated_at,
        )

    async def set_many(
        self,
        items: Sequence[tuple[str, str, str]],
        *,
        expected_updated_at_map: Mapping[tuple[str, str], str],
        import_source: SettingsImportSource = SettingsImportSource.DIRECT_SET,
        governance: SettingsWriteGovernance | None = None,
    ) -> str:
        """Span-wrapped public entry point for a batch setting write.

        The ``settings.set_many`` span carries only the batch size, never
        namespaces / keys / values; record_exception / set_status are off
        so exception frame-locals are not serialised.

        ``governance`` authorises any security-weakening transition in the
        batch (see :mod:`synthorg.settings.write_governance`).

        Returns:
            The shared ``updated_at`` ISO string from :meth:`_set_many`.
        """
        with _tracer.start_as_current_span(
            "settings.set_many",
            attributes={"settings.batch_size": len(items)},
            record_exception=False,
            set_status_on_exception=False,
        ):
            return await self._set_many(
                items,
                expected_updated_at_map=expected_updated_at_map,
                import_source=import_source,
                governance=governance,
            )

    async def _guard_cross_field_rules(
        self, items: Sequence[tuple[str, str, str]]
    ) -> None:
        """Reject a write whose combined result breaks a cross-setting rule.

        Args:
            items: The triples about to be written.

        Raises:
            SettingValidationError: When the resulting combination is
                invalid. Raised before anything is persisted, so the caller
                sees the refusal rather than a 200 followed by a value the
                system never enforces.
        """
        await guard_cross_field_rules(
            items,
            get_entry=self.get,
            get_definition=self._registry.get,
        )

    async def _set_many(
        self,
        items: Sequence[tuple[str, str, str]],
        *,
        expected_updated_at_map: Mapping[tuple[str, str], str],
        import_source: SettingsImportSource = SettingsImportSource.DIRECT_SET,
        governance: SettingsWriteGovernance | None = None,
    ) -> str:
        """Atomically persist multiple setting values with per-key CAS.

        Call only from :meth:`set_many`; every caller must route through
        the span-wrapped public method so the batch write stays traced.

        Each element is ``(namespace, key, value)``.  The service
        validates and (if sensitive) encrypts every value, then
        routes the batch through ``SettingsRepository.set_many`` in
        one transaction with a shared ``updated_at`` timestamp.
        ``expected_updated_at_map`` supplies per-key CAS versions;
        pass ``""`` for first-write semantics.  Returns the shared
        ``updated_at`` ISO string.  Raises ``VersionConflictError``
        on CAS miss (whole transaction rolled back),
        ``SettingNotFoundError`` / ``SettingValidationError`` /
        ``SettingsEncryptionError`` on preflight failures.

        ``import_source`` is forwarded to validation-failure logs so
        bulk-import audit trails carry the same attribution as the
        per-key ``set`` path.

        Returns:
            The shared ISO 8601 ``updated_at`` timestamp applied to all
            persisted rows in the batch.

        Raises:
            ValueError: If the ``items`` sequence is empty.
            VersionConflictError: If any per-key CAS version in
                ``expected_updated_at_map`` does not match the current
                DB row (whole transaction rolled back).
            SettingNotFoundError: If a namespace/key pair is not in the
                registry.
            SettingValidationError: If a value fails type or pattern
                validation.
            SettingsEncryptionError: If a sensitive value cannot be
                encrypted.
        """
        if not items:
            msg = "set_many requires at least one item"
            logger.warning(
                SETTINGS_VALIDATION_FAILED,
                action="set_many",
                reason="empty_batch",
                import_source=import_source.value,
            )
            raise ValueError(msg)

        # Ahead of both guards for the same reason as the single-key path: a
        # cross-field rule parses the pending value, so an unvalidated one
        # reaches it as a string it can only skip. ``_prepare_set_many``
        # validates again on its way to building the rows; validation is pure,
        # so the repeat costs nothing and neither call site depends on the
        # other having run.
        self._validate_batch(items, import_source=import_source)

        await guard_security_writes(
            items,
            governance=governance,
            get_entry=self.get,
        )
        # Checked over the whole batch: a pair that is only valid together
        # (raising the floor and a tier in one write) has to be allowed.
        await self._guard_cross_field_rules(items)

        updated_at = now_iso_utc()
        prepared, written_pairs = self._prepare_set_many(
            items,
            updated_at,
            import_source=import_source,
        )

        written = await self._repository.set_many(
            prepared,
            expected_updated_at_map=expected_updated_at_map,
        )
        if not written:
            from synthorg.core.domain_errors import (  # noqa: PLC0415
                VersionConflictError,
            )

            logger.warning(
                SETTINGS_VERSION_CONFLICT,
                reason="concurrent_modification_batch",
                key_count=len(prepared),
            )
            keys = ", ".join(f"{ns}/{k}" for ns, k, _ in items)
            msg = f"Concurrent modification on batch: {keys}"
            raise VersionConflictError(msg)

        for namespace, key in written_pairs:
            self._invalidate_cache(namespace, key)
            logger.info(SETTINGS_VALUE_SET, namespace=namespace, key=key)
            record_settings_mutation(namespace=namespace)
            emit_security_setting_changed(
                namespace,
                key=key,
                action_type="set_many",
            )
            await self._publish_change(namespace, key)

        return updated_at

    def _validate_batch(
        self,
        items: Sequence[tuple[str, str, str]],
        *,
        import_source: SettingsImportSource,
    ) -> None:
        """Reject a batch carrying a value its own definition refuses.

        Args:
            items: The triples about to be written.
            import_source: Where the write came from, for the log.

        Raises:
            SettingNotFoundError: On an unregistered namespace/key pair.
            SettingValidationError: On a value failing type or pattern
                validation.
        """
        for namespace, key, value in items:
            definition = self._registry.get(namespace, key)
            if definition is None:
                logger.warning(SETTINGS_NOT_FOUND, namespace=namespace, key=key)
                msg = f"Unknown setting: {namespace}/{key}"
                raise SettingNotFoundError(msg)
            try:
                validate_value(definition, value)
            except SettingValidationError as exc:
                logger.warning(
                    SETTINGS_VALIDATION_FAILED,
                    namespace=namespace,
                    key=key,
                    import_source=import_source.value,
                    reason=safe_error_description(exc),
                )
                raise

    def _prepare_set_many(
        self,
        items: Sequence[tuple[str, str, str]],
        updated_at: str,
        *,
        import_source: SettingsImportSource = SettingsImportSource.DIRECT_SET,
    ) -> tuple[
        list[SettingRow],
        list[tuple[str, str]],
    ]:
        """Validate, encrypt, and shape items for a batch ``set_many`` write.

        Returns two parallel lists: the tuple format the repository
        protocol expects, and the per-item keys so the caller can
        invalidate cache + publish change events after the transactional
        write succeeds.

        Returns:
            Two parallel lists: the ``list[SettingRow]`` the repository
            protocol expects, and a ``list`` of ``(namespace, key)`` for
            post-write cache-invalidation and publish calls.

        Raises:
            SettingNotFoundError: If a namespace/key pair in the batch
                is not in the registry.
            SettingValidationError: If a value fails type or pattern
                validation, or a duplicate namespace/key pair appears
                in the batch.
        """
        prepared: list[SettingRow] = []
        written_pairs: list[tuple[str, str]] = []
        seen: set[tuple[str, str]] = set()
        for namespace, key, value in items:
            pair = (namespace, key)
            if pair in seen:
                msg = f"Duplicate setting in batch: {namespace}/{key}"
                logger.warning(
                    SETTINGS_VALIDATION_FAILED,
                    namespace=namespace,
                    key=key,
                    action="set_many",
                    reason="duplicate_setting_in_batch",
                    import_source=import_source.value,
                )
                raise SettingValidationError(msg)
            seen.add(pair)
            definition = self._registry.get(namespace, key)
            if definition is None:
                logger.warning(SETTINGS_NOT_FOUND, namespace=namespace, key=key)
                msg = f"Unknown setting: {namespace}/{key}"
                raise SettingNotFoundError(msg)

            reject_if_read_only(
                definition,
                action="set_many",
                import_source=import_source,
            )

            try:
                validate_value(definition, value)
            except SettingValidationError as exc:
                logger.warning(
                    SETTINGS_VALIDATION_FAILED,
                    namespace=namespace,
                    key=key,
                    import_source=import_source.value,
                    reason=safe_error_description(exc),
                )
                raise

            store_value = self._encrypt_if_sensitive(definition, value)
            prepared.append(
                SettingRow(
                    namespace=NotBlankStr(namespace),
                    key=NotBlankStr(key),
                    value=store_value,
                    updated_at=updated_at,
                )
            )
            written_pairs.append((namespace, key))
        return prepared, written_pairs

    def _encrypt_if_sensitive(
        self,
        definition: SettingDefinition,
        value: str,
    ) -> str:
        """Encrypt ``value`` via the configured encryptor when sensitive.

        Returns the plaintext unchanged for non-sensitive settings.
        Raises ``SettingsEncryptionError`` when a sensitive setting
        is configured without an encryptor.

        Returns:
            The encrypted ciphertext for sensitive settings, or the
            original plaintext unchanged for non-sensitive settings.

        Raises:
            SettingsEncryptionError: If the setting is sensitive but no
                encryptor is configured, or the encryptor's
                ``encrypt()`` call fails.
        """
        if not definition.sensitive:
            return value
        if self._encryptor is None:
            logger.error(
                SETTINGS_ENCRYPTION_ERROR,
                namespace=definition.namespace,
                key=definition.key,
                reason="no_encryptor",
            )
            msg = (
                f"Cannot store sensitive setting "
                f"{definition.namespace}/{definition.key} "
                f"without encryption key"
            )
            raise SettingsEncryptionError(msg)
        try:
            return self._encryptor.encrypt(value)
        except SettingsEncryptionError as exc:
            # Same rationale as ``_decrypt_if_sensitive``.
            logger.warning(
                SETTINGS_ENCRYPTION_ERROR,
                namespace=definition.namespace,
                key=definition.key,
                reason="encrypt_failed",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise

    async def delete(self, namespace: str, key: str) -> None:
        """Delete a DB override, reverting to the next source in chain.

        Args:
            namespace: Setting namespace.
            key: Setting key.

        Raises:
            SettingNotFoundError: If the key is not in the registry.
        """
        # The span carries only namespace/key (never the value, which may
        # be a secret). record_exception/set_status are off so an
        # exception's frame-locals are not serialised into the span.
        with _tracer.start_as_current_span(
            "settings.delete",
            attributes={"settings.namespace": namespace, "settings.key": key},
            record_exception=False,
            set_status_on_exception=False,
        ):
            definition = self._registry.get(namespace, key)
            if definition is None:
                logger.warning(SETTINGS_NOT_FOUND, namespace=namespace, key=key)
                msg = f"Unknown setting: {namespace}/{key}"
                raise SettingNotFoundError(msg)

            reject_if_read_only(definition, action="delete")

            await guard_security_delete(
                namespace,
                [definition],
                resolve_fallback=self._resolve_fallback,
                get_entry=self.get,
            )

            await self._repository.delete(
                (NotBlankStr(namespace), NotBlankStr(key)),
            )

            self._invalidate_cache(namespace, key)

            logger.info(
                SETTINGS_VALUE_DELETED,
                namespace=namespace,
                key=key,
            )
            record_settings_mutation(namespace=namespace)
            emit_security_setting_changed(namespace, key=key, action_type="delete")

            await self._publish_change(namespace, key)

    async def delete_namespace(self, namespace: str) -> int:
        """Span-wrapped public entry point for a whole-namespace delete.

        See :meth:`_delete_namespace` for the full contract (audit log,
        per-key republish semantics, raised exceptions). Wrapping here
        keeps the credentials-bearing namespace-delete path under a trace
        span like ``set`` / ``set_many`` / ``delete``, with
        ``record_exception=False`` / ``set_status_on_exception=False`` so a
        failure does not serialise in-scope secret values into the OTel
        error attributes.

        Returns:
            Number of override rows actually removed.
        """
        with _tracer.start_as_current_span(
            "settings.delete_namespace",
            attributes={"settings.namespace": namespace},
            record_exception=False,
            set_status_on_exception=False,
        ):
            return await self._delete_namespace(namespace)

    async def _delete_namespace(self, namespace: str) -> int:
        """Delete every DB override under *namespace*.

        Call only from :meth:`delete_namespace`; all callers must route
        through the span-wrapped public method.

        Reverts each affected key to the next source in its chain
        (env, default).  Emits a single
        :data:`SETTINGS_VALUE_DELETED` audit log carrying the namespace
        and the affected count, then publishes per-key change
        notifications for the **subset of registered keys whose DB
        override was actually removed** so downstream caches /
        listeners stay in sync.  Keys with no DB override (e.g.
        defaults, env-only) do NOT republish -- otherwise every
        registered key in the namespace would trigger phantom
        reload / restart work even when only a single override row
        was cleared.

        Args:
            namespace: Setting namespace to clear.

        Returns:
            Number of override rows actually removed.

        Raises:
            PersistenceError: If the persistence layer fails.
        """
        # Compose-set keys in this namespace are reported as a WARNING but
        # do not block the whole-namespace delete: the writable overrides
        # the operator wants to clear should not be held hostage by the
        # presence of a read-only entry. Reads already bypass the DB for
        # compose-set definitions (see ``_resolve_with_db_lookup``), so any
        # stale row that ``delete_namespace_returning_keys`` removes is a
        # no-op for the running process.
        readonly_definition_keys = {
            d.key for d in self._registry.list_namespace(namespace) if d.compose_set
        }

        # Atomic delete-and-return-keys: the repository removes every
        # override row under *namespace* in one transaction and returns
        # exactly the keys whose row was actually removed. A separate
        # ``get_namespace`` snapshot followed by ``delete_namespace`` would
        # have a TOCTOU race -- a concurrent ``set`` between the snapshot and
        # the delete would either drop a publish (key set after the snapshot,
        # then deleted) or fire a phantom one (key in the snapshot, then
        # unset before the delete). Returning the actually-removed keys keys
        # the change notifications to what truly changed.
        await guard_security_delete(
            namespace,
            self._registry.list_namespace(namespace),
            resolve_fallback=self._resolve_fallback,
            get_entry=self.get,
        )

        ns = NotBlankStr(namespace)
        try:
            removed_keys = await self._repository.delete_namespace_returning_keys(ns)
        except Exception as exc:
            reraise_critical(exc)
            logger.warning(
                SETTINGS_DELETE_FAILED,
                namespace=namespace,
                phase="delete_namespace_returning_keys",
                error_type=type(exc).__name__,
            )
            raise
        deleted = len(removed_keys)

        # Now that we know exactly which keys were actually removed,
        # report the read-only intersection so the audit log reflects
        # what the storage layer did (not what was registered).  An
        # earlier emit would have claimed sweep even for read-only
        # definitions whose rows never existed or whose deletion the
        # repository skipped; this version is precise.
        swept_readonly = sorted(set(removed_keys) & readonly_definition_keys)
        if swept_readonly:
            logger.warning(
                SETTINGS_VALIDATION_FAILED,
                namespace=namespace,
                action="delete_namespace",
                reason="compose_set_swept",
                read_only_keys=swept_readonly,
            )

        self._invalidate_namespace_cache(namespace)

        # No-op short-circuit: a delete_namespace that removed zero rows
        # must not fire the audit event or republish per-key change
        # notifications.  Otherwise downstream subscribers (cache reload
        # listeners, restart-required gates) react to a phantom change.
        if deleted == 0:
            return 0

        logger.info(
            SETTINGS_VALUE_DELETED,
            namespace=namespace,
            count=deleted,
        )
        # ``set_many`` increments the counter once per mutated key, so
        # ``delete_namespace`` matches that semantics: emit one
        # ``settings_mutations`` increment per actually-deleted key.
        for _ in range(deleted):
            record_settings_mutation(namespace=namespace)
        emit_security_setting_changed(
            namespace,
            action_type="delete_namespace",
            count=deleted,
        )

        removed_key_set = set(removed_keys)
        for definition in self._registry.list_namespace(namespace):
            # A compose-set key is fixed for the life of the process, so
            # sweeping its row changes nothing a subscriber could act on.
            # Publishing anyway would announce a change to a value that has
            # not moved and cannot.
            if definition.key in removed_key_set and not definition.compose_set:
                await self._publish_change(namespace, definition.key)

        return deleted

    def get_schema(self, namespace: str | None = None) -> tuple[SettingDefinition, ...]:
        """Return setting definitions for schema introspection.

        Args:
            namespace: Optional namespace filter. If ``None``,
                returns all definitions.

        Returns:
            Matching definitions sorted by namespace then key.
        """
        if namespace is not None:
            return self._registry.list_namespace(namespace)
        return self._registry.list_all()

    async def _publish_change(
        self,
        namespace: str,
        key: str,
    ) -> None:
        """Publish a change notification to the message bus.

        Every published change is one a subscriber can act on: a compose-set
        setting is rejected on the write side, so it never reaches here.
        """
        if self._message_bus is None:
            return

        if not self._message_bus.is_running:
            return

        try:
            message = Message(
                timestamp=datetime.now(UTC),
                sender="system",
                to="#settings",
                type=MessageType.ANNOUNCEMENT,
                channel="#settings",
                parts=(TextPart(text=f"Setting changed: {namespace}/{key}"),),
                metadata=MessageMetadata(
                    extra=(
                        ("namespace", namespace),
                        ("key", key),
                    ),
                ),
            )
            await self._message_bus.publish(message)
            logger.debug(
                SETTINGS_NOTIFICATION_PUBLISHED,
                namespace=namespace,
                key=key,
            )
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            # Notification failure should not break settings writes.
            # Settings is a credential-bearing path so use the
            # ``safe_error_description`` redactor and do NOT pass
            # exc_info=True -- the traceback could leak sensitive
            # payload through the cryptography exception chain.
            logger.warning(
                SETTINGS_NOTIFICATION_FAILED,
                namespace=namespace,
                key=key,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
