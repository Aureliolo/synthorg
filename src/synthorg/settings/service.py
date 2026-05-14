"""Settings service: resolution, validation, caching, and notifications.

Provides the central service layer that merges setting values from
three sources in priority order: DB > env > code default. For settings
flagged ``read_only_post_init=True`` the DB tier is bypassed and the
chain collapses to env > default.
"""

import asyncio
import os
import re
from collections.abc import Mapping, Sequence  # noqa: TC003
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from synthorg.communication.enums import MessageType
from synthorg.communication.message import Message, MessageMetadata, TextPart
from synthorg.core.types import NotBlankStr
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.security import SECURITY_SETTINGS_CHANGED
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
from synthorg.settings.enums import SettingsImportSource, SettingSource
from synthorg.settings.errors import (
    SettingNotFoundError,
    SettingReadOnlyError,
    SettingsEncryptionError,
    SettingValidationError,
)
from synthorg.settings.models import SettingDefinition, SettingEntry, SettingValue
from synthorg.settings.type_validators import validate_by_type

if TYPE_CHECKING:
    from synthorg.communication.bus_protocol import MessageBus
    from synthorg.persistence.settings_protocol import SettingsRepository
    from synthorg.settings.encryption import SettingsEncryptor
    from synthorg.settings.registry import SettingsRegistry

logger = get_logger(__name__)

_SENSITIVE_MASK = "********"

# Namespaces whose changes always represent a security decision and must
# be appended to the cryptographic audit chain. Settings in these
# namespaces affect authentication, authorization, autonomy gating, or
# encryption -- a forensic investigator needs to be able to prove the
# change order is intact.
_AUDITED_SETTING_NAMESPACES: frozenset[str] = frozenset(
    {"auth", "security", "autonomy", "encryption", "rbac"},
)


def _emit_security_setting_changed(
    namespace: str,
    *,
    action_type: str,
    key: str | None = None,
    **extra: object,
) -> None:
    """Emit ``SECURITY_SETTINGS_CHANGED`` when *namespace* is audited.

    Centralises the gate so set / set_many / delete / delete_namespace
    use a single, consistent payload shape -- and so a future
    namespace addition only has to touch
    :data:`_AUDITED_SETTING_NAMESPACES`.

    ``key`` is optional because ``delete_namespace`` operates on the
    whole namespace and substitutes ``count`` via ``extra`` instead.
    """
    if namespace not in _AUDITED_SETTING_NAMESPACES:
        return
    payload: dict[str, object] = {
        "namespace": namespace,
        "action_type": action_type,
        **extra,
    }
    if key is not None:
        payload["key"] = key
    logger.info(SECURITY_SETTINGS_CHANGED, **payload)


def _now_iso() -> str:
    """Return current UTC time as ISO 8601 string."""
    return datetime.now(UTC).isoformat()


def _env_var_name(namespace: str, key: str) -> str:
    """Build env var name: ``SYNTHORG_{NAMESPACE}_{KEY}`` (uppercased)."""
    return f"SYNTHORG_{namespace.upper()}_{key.upper()}"


def _reject_if_read_only(
    definition: SettingDefinition,
    *,
    action: str,
    import_source: SettingsImportSource | None = None,
) -> None:
    """Raise ``SettingReadOnlyError`` for read-only-post-init settings.

    The registry entry exists for discoverability via the /settings
    API; the resolved value is sourced from environment variables or
    YAML at process startup.  Mutation surfaces (``set``, ``set_many``,
    ``delete``, ``delete_namespace``) must reject so an operator does
    not believe the override took effect when the running process keeps
    the boot-time value.

    ``import_source`` is included in the validation log when supplied
    so the every ``set()`` rejection path carries the same tag the
    happy path emits, keeping the log tagging contract consistent.
    """
    if not definition.read_only_post_init:
        return
    payload: dict[str, object] = {
        "namespace": definition.namespace,
        "key": definition.key,
        "reason": "read_only_post_init",
        "action": action,
    }
    if import_source is not None:
        payload["import_source"] = import_source.value
    logger.warning(SETTINGS_VALIDATION_FAILED, **payload)
    msg = (
        f"Setting {definition.namespace}/{definition.key} is sourced"
        f" from env / YAML at startup and cannot be modified at runtime"
        f" (action={action!r}). Update the env var or YAML and restart."
    )
    raise SettingReadOnlyError(msg)


def _validate_value(definition: SettingDefinition, value: str) -> None:
    """Validate a value against its definition.

    For sensitive settings, error messages mask the actual value
    to prevent secret leakage through validation errors.

    Raises:
        SettingValidationError: If validation fails.
    """
    validate_by_type(definition, value)

    if definition.validator_pattern is not None and not re.fullmatch(
        definition.validator_pattern, value
    ):
        display = _SENSITIVE_MASK if definition.sensitive else repr(value)
        msg = f"Value {display} does not match pattern {definition.validator_pattern!r}"
        raise SettingValidationError(msg)


class SettingsService:
    """Central settings service with resolution, cache, and notifications.

    Resolution order (highest priority first):
    1. Database overrides (user-set via API/UI)
    2. Environment variables (``SYNTHORG_{NAMESPACE}_{KEY}`` or
       ``env_var_override``)
    3. Code defaults (from ``SettingDefinition.default``)

    For settings flagged ``read_only_post_init=True`` the DB tier is
    bypassed and the chain collapses to env > default.

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
        # Source-of-resolution audit: every (namespace, key) emits one
        # INFO SETTINGS_VALUE_RESOLVED on the first cold read of the
        # process so operators can see which source won at startup.
        # Subsequent resolutions stay at DEBUG. ``_resolution_lock``
        # gates the membership-test + add window so concurrent
        # first-reads for the same key promote exactly one entry to
        # INFO; without it, two coroutines that miss the cache and
        # interleave around an ``await`` in their resolver chain can
        # both observe the key as not-yet-logged and emit duplicate
        # INFO lines.
        self._resolution_logged: set[tuple[str, str]] = set()
        self._resolution_lock: asyncio.Lock = asyncio.Lock()

    @property
    def registry(self) -> SettingsRegistry:
        """Read-only access to the registry for callers that need definitions."""
        return self._registry

    async def _emit_resolved(
        self,
        definition: SettingDefinition,
        source: str,
    ) -> None:
        """Log a setting resolution; promote to INFO on first cold read.

        Every ``(namespace, key)`` pair emits exactly one INFO
        ``SETTINGS_VALUE_RESOLVED`` event during the lifetime of the
        process so operators can audit which source supplied each
        configuration value at startup.  Subsequent resolutions for
        the same pair stay at DEBUG to avoid log spam.

        ``_resolution_lock`` gates the membership-test + add so
        concurrent first-reads for the same key produce a single
        INFO line; the actual log emission runs outside the lock to
        keep the critical section CPU-bound.
        """
        cache_key = (definition.namespace, definition.key)
        async with self._resolution_lock:
            first_read = cache_key not in self._resolution_logged
            if first_read:
                self._resolution_logged.add(cache_key)
        log = logger.info if first_read else logger.debug
        log(
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
        """
        result = await self._repository.get(
            NotBlankStr(definition.namespace),
            NotBlankStr(definition.key),
        )
        if result is None:
            return None
        raw_value, updated_at = result
        value = self._decrypt_if_sensitive(definition, raw_value)
        return SettingValue(
            namespace=definition.namespace,
            key=definition.key,
            value=value,
            source=SettingSource.DATABASE,
            updated_at=updated_at,
        )

    def _decrypt_if_sensitive(
        self,
        definition: SettingDefinition,
        raw_value: str,
    ) -> str:
        """Decrypt ``raw_value`` for sensitive settings, else return as-is."""
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

        # Read-only-post-init entries are sourced exclusively from env /
        # YAML / code default at process startup; the DB row (if any --
        # left over from a pre-rename schema or a stale ops mistake) MUST
        # NOT be consulted, otherwise the running process would surface
        # a value the operator believed was overridden out via
        # ``set()`` -- which we already reject on the write side.
        if not definition.read_only_post_init:
            setting_value = await self._resolve_db(definition)
            if setting_value is not None:
                # Direct dict mutation is intentional: the previous
                # copy-on-write pattern {**self._cache, k: v} had a
                # TOCTOU race under concurrent TaskGroup reads (the
                # spread sees a stale snapshot after an await).  In
                # asyncio's cooperative concurrency, dict item assignment
                # is a single-opcode operation and safe without locking.
                if not definition.sensitive:
                    self._cache[cache_key] = setting_value
                await self._emit_resolved(definition, source="db")
                return setting_value

        fallback = await self._resolve_fallback(definition)
        # Read-only-post-init: cache the first-read snapshot so
        # subsequent ``/settings`` queries and ``SETTINGS_VALUE_RESOLVED``
        # events report the *same* value the runtime captured at boot.
        # Without this snapshot, a mid-process mutation of ``os.environ``
        # or the in-memory config object would let the resolved value
        # drift away from what middleware / NATS clients / worker pools
        # locked in at startup, and ``/settings`` would lie about
        # reality.  Sensitive read-only-post-init values are still
        # bypassed (they should never survive in cache).
        if definition.read_only_post_init and not definition.sensitive:
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
        display_value = _SENSITIVE_MASK if definition.sensitive else value.value
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
        db_lookup: dict[str, tuple[str, str]] = {k: (v, ts) for k, v, ts in db_rows}

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
        db_rows = await self._repository.get_all()
        db_lookup: dict[tuple[str, str], tuple[str, str]] = {
            (ns, k): (v, ts) for ns, k, v, ts in db_rows
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
        db_rows = await self._repository.get_all()
        db_lookup: dict[tuple[str, str], tuple[str, str]] = {
            (ns, k): (v, ts) for ns, k, v, ts in db_rows
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
        """Resolve via env > code default (no DB lookup)."""
        ns = definition.namespace
        key = definition.key

        env_name = (
            definition.env_var_override
            if definition.env_var_override is not None
            else _env_var_name(ns, key)
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
        """
        ns = definition.namespace
        key = definition.key

        # Read-only-post-init: ignore any DB row (mirrors the per-key
        # ``get()`` short-circuit so batch reads do not surface a
        # stale override).
        if definition.read_only_post_init:
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
                        value=_SENSITIVE_MASK,
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
                        value=_SENSITIVE_MASK,
                        source=SettingSource.DATABASE,
                        updated_at=updated_at,
                    )
            display = _SENSITIVE_MASK if definition.sensitive else value
            await self._emit_resolved(definition, source="db")
            return SettingEntry(
                definition=definition,
                value=display,
                source=SettingSource.DATABASE,
                updated_at=updated_at,
            )

        # Fallback: env > default
        fallback = await self._resolve_fallback(definition)
        display = _SENSITIVE_MASK if definition.sensitive else fallback.value
        return SettingEntry(
            definition=definition,
            value=display,
            source=fallback.source,
            updated_at=fallback.updated_at,
        )

    def _invalidate_cache(self, namespace: str, key: str) -> None:
        """Remove a key from the settings cache."""
        cache_key = (namespace, key)
        self._cache = {k: v for k, v in self._cache.items() if k != cache_key}
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
        """
        definition = self._registry.get(namespace, key)
        if definition is None:
            return "", ""
        # Read-only-post-init entries are never written via the service,
        # so CAS callers must observe the same "no DB override" sentinel
        # the read path returns -- returning a stale row here would let
        # a CAS preflight succeed against a value the runtime no longer
        # honours.
        if definition.read_only_post_init:
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
    ) -> SettingEntry:
        """Validate, encrypt, and persist a setting value with optional CAS.

        Pass ``expected_updated_at=""`` for first-write semantics.
        Raises ``VersionConflictError`` on CAS miss,
        ``SettingNotFoundError`` / ``SettingValidationError`` /
        ``SettingsEncryptionError`` on preflight failures.

        ``import_source`` distinguishes how this write entered the
        service so ``SETTINGS_VALIDATION_FAILED`` log records show
        whether a malformed value came from an API body, file
        upload, config merge, or direct set.  Defaults to
        ``DIRECT_SET`` for in-process callers.
        """
        definition = self._registry.get(namespace, key)
        if definition is None:
            logger.warning(SETTINGS_NOT_FOUND, namespace=namespace, key=key)
            msg = f"Unknown setting: {namespace}/{key}"
            raise SettingNotFoundError(msg)

        _reject_if_read_only(
            definition,
            action="set",
            import_source=import_source,
        )

        try:
            _validate_value(definition, value)
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
        updated_at = _now_iso()
        written = await self._repository.set(
            NotBlankStr(namespace),
            NotBlankStr(key),
            store_value,
            updated_at,
            expected_updated_at=expected_updated_at,
        )
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
        _emit_security_setting_changed(namespace, key=key, action_type="set")
        await self._publish_change(namespace, key, definition)

        display_value = _SENSITIVE_MASK if definition.sensitive else value
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
    ) -> str:
        """Atomically persist multiple setting values with per-key CAS.

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

        updated_at = _now_iso()
        prepared, definitions = self._prepare_set_many(
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

        for namespace, key, definition in definitions:
            self._invalidate_cache(namespace, key)
            logger.info(SETTINGS_VALUE_SET, namespace=namespace, key=key)
            record_settings_mutation(namespace=namespace)
            _emit_security_setting_changed(
                namespace,
                key=key,
                action_type="set_many",
            )
            await self._publish_change(namespace, key, definition)

        return updated_at

    def _prepare_set_many(
        self,
        items: Sequence[tuple[str, str, str]],
        updated_at: str,
        *,
        import_source: SettingsImportSource = SettingsImportSource.DIRECT_SET,
    ) -> tuple[
        list[tuple[NotBlankStr, NotBlankStr, str, str]],
        list[tuple[str, str, SettingDefinition]],
    ]:
        """Validate, encrypt, and shape items for a batch ``set_many`` write.

        Returns two parallel lists: the tuple format the repository
        protocol expects, and the per-item definitions so the caller
        can invalidate cache + publish change events after the
        transactional write succeeds.
        """
        prepared: list[tuple[NotBlankStr, NotBlankStr, str, str]] = []
        definitions: list[tuple[str, str, SettingDefinition]] = []
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

            _reject_if_read_only(
                definition,
                action="set_many",
                import_source=import_source,
            )

            try:
                _validate_value(definition, value)
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
                (
                    NotBlankStr(namespace),
                    NotBlankStr(key),
                    store_value,
                    updated_at,
                )
            )
            definitions.append((namespace, key, definition))
        return prepared, definitions

    def _encrypt_if_sensitive(
        self,
        definition: SettingDefinition,
        value: str,
    ) -> str:
        """Encrypt ``value`` via the configured encryptor when sensitive.

        Returns the plaintext unchanged for non-sensitive settings.
        Raises ``SettingsEncryptionError`` when a sensitive setting
        is configured without an encryptor.
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
        definition = self._registry.get(namespace, key)
        if definition is None:
            logger.warning(SETTINGS_NOT_FOUND, namespace=namespace, key=key)
            msg = f"Unknown setting: {namespace}/{key}"
            raise SettingNotFoundError(msg)

        _reject_if_read_only(definition, action="delete")

        await self._repository.delete(NotBlankStr(namespace), NotBlankStr(key))

        self._invalidate_cache(namespace, key)

        logger.info(
            SETTINGS_VALUE_DELETED,
            namespace=namespace,
            key=key,
        )
        record_settings_mutation(namespace=namespace)
        _emit_security_setting_changed(namespace, key=key, action_type="delete")

        await self._publish_change(namespace, key, definition)

    async def delete_namespace(self, namespace: str) -> int:
        """Delete every DB override under *namespace*.

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
        # Read-only-post-init keys in this namespace are reported as a
        # WARNING but do not block the whole-namespace delete: the
        # writable overrides the operator wants to clear should not be
        # held hostage by the presence of a read-only entry.  Reads
        # already bypass the DB for read-only-post-init definitions
        # (see ``_resolve_with_db_lookup``), so any stale row that
        # ``delete_namespace_returning_keys`` removes is a no-op for
        # the running process.
        readonly_definition_keys = {
            d.key
            for d in self._registry.list_namespace(namespace)
            if d.read_only_post_init
        }

        # Atomic delete-and-return-keys: the repository removes every
        # override row under *namespace* in one transaction and returns
        # exactly the keys whose row was actually removed.  This avoids
        # the TOCTOU race the older ``get_namespace`` + ``delete_namespace``
        # pair had -- a concurrent ``set`` between the snapshot and the
        # delete would either drop a publish (key set after snapshot,
        # then deleted) or fire a phantom one (key visible in snapshot,
        # then unset before delete).
        ns = NotBlankStr(namespace)
        try:
            removed_keys = await self._repository.delete_namespace_returning_keys(ns)
        except Exception as exc:
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
                reason="read_only_post_init_swept",
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
        _emit_security_setting_changed(
            namespace,
            action_type="delete_namespace",
            count=deleted,
        )

        removed_key_set = set(removed_keys)
        for definition in self._registry.list_namespace(namespace):
            if definition.key in removed_key_set:
                await self._publish_change(namespace, definition.key, definition)

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
        definition: SettingDefinition,
    ) -> None:
        """Publish a change notification to the message bus."""
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
                        (
                            "restart_required",
                            str(definition.restart_required),
                        ),
                    ),
                ),
            )
            await self._message_bus.publish(message)
            logger.debug(
                SETTINGS_NOTIFICATION_PUBLISHED,
                namespace=namespace,
                key=key,
            )
        except MemoryError, RecursionError:
            raise
        except Exception as exc:
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
