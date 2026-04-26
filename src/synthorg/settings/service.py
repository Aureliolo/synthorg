"""Settings service -- resolution, validation, caching, and notifications.

Provides the central service layer that merges setting values from
four sources in priority order: DB > env > YAML > code defaults.
"""

import json
import os
import re
from collections.abc import Mapping, Sequence  # noqa: TC003
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from synthorg.communication.enums import MessageType
from synthorg.communication.message import Message, MessageMetadata, TextPart
from synthorg.core.types import NotBlankStr
from synthorg.observability import get_logger
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
from synthorg.settings.config_bridge import extract_from_config
from synthorg.settings.enums import SettingSource, SettingType
from synthorg.settings.errors import (
    SettingNotFoundError,
    SettingsEncryptionError,
    SettingValidationError,
)
from synthorg.settings.models import SettingDefinition, SettingEntry, SettingValue

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


def _now_iso() -> str:
    """Return current UTC time as ISO 8601 string."""
    return datetime.now(UTC).isoformat()


def _env_var_name(namespace: str, key: str) -> str:
    """Build env var name: ``SYNTHORG_{NAMESPACE}_{KEY}`` (uppercased)."""
    return f"SYNTHORG_{namespace.upper()}_{key.upper()}"


def _validate_value(definition: SettingDefinition, value: str) -> None:
    """Validate a value against its definition.

    For sensitive settings, error messages mask the actual value
    to prevent secret leakage through validation errors.

    Raises:
        SettingValidationError: If validation fails.
    """
    _validate_by_type(definition, value)

    if definition.validator_pattern is not None and not re.fullmatch(
        definition.validator_pattern, value
    ):
        display = _SENSITIVE_MASK if definition.sensitive else repr(value)
        msg = f"Value {display} does not match pattern {definition.validator_pattern!r}"
        raise SettingValidationError(msg)


def _validate_by_type(definition: SettingDefinition, value: str) -> None:
    """Type-specific validation dispatch."""
    setting_type = definition.type

    if setting_type == SettingType.INTEGER:
        _validate_integer(definition, value)
    elif setting_type == SettingType.FLOAT:
        _validate_float(definition, value)
    elif setting_type == SettingType.BOOLEAN:
        _validate_boolean(definition, value)
    elif setting_type == SettingType.ENUM:
        _validate_enum(definition, value)
    elif setting_type == SettingType.JSON:
        _validate_json(definition, value)


def _validate_integer(definition: SettingDefinition, value: str) -> None:
    try:
        int_val = int(value)
    except ValueError as exc:
        display = _SENSITIVE_MASK if definition.sensitive else repr(value)
        msg = f"Expected integer, got {display}"
        raise SettingValidationError(msg) from exc
    _check_range(definition, float(int_val))


def _validate_float(definition: SettingDefinition, value: str) -> None:
    try:
        float_val = float(value)
    except ValueError as exc:
        display = _SENSITIVE_MASK if definition.sensitive else repr(value)
        msg = f"Expected float, got {display}"
        raise SettingValidationError(msg) from exc
    _check_range(definition, float_val)


def _validate_boolean(definition: SettingDefinition, value: str) -> None:
    if value.lower() not in ("true", "false", "1", "0"):
        display = _SENSITIVE_MASK if definition.sensitive else repr(value)
        msg = f"Expected boolean, got {display}"
        raise SettingValidationError(msg)


def _validate_enum(definition: SettingDefinition, value: str) -> None:
    if value not in definition.enum_values:
        display = _SENSITIVE_MASK if definition.sensitive else repr(value)
        msg = f"Invalid enum value {display}. Allowed: {definition.enum_values}"
        raise SettingValidationError(msg)


def _validate_json(definition: SettingDefinition, value: str) -> None:
    try:
        json.loads(value)
    except json.JSONDecodeError as exc:
        if definition.sensitive:
            msg = (
                f"Invalid JSON for sensitive setting"
                f" {definition.namespace}/{definition.key}"
            )
        else:
            msg = f"Invalid JSON: {exc}"
        raise SettingValidationError(msg) from exc


def _check_range(definition: SettingDefinition, value: float) -> None:
    """Check numeric range constraints."""
    display = _SENSITIVE_MASK if definition.sensitive else str(value)
    if definition.min_value is not None and value < definition.min_value:
        msg = f"Value {display} below minimum {definition.min_value}"
        raise SettingValidationError(msg)
    if definition.max_value is not None and value > definition.max_value:
        msg = f"Value {display} above maximum {definition.max_value}"
        raise SettingValidationError(msg)


class SettingsService:
    """Central settings service with resolution, cache, and notifications.

    Resolution order (highest priority first):
    1. Database overrides (user-set via API/UI)
    2. Environment variables (``SYNTHORG_{NAMESPACE}_{KEY}``)
    3. YAML defaults (from ``RootConfig``)
    4. Code defaults (from ``SettingDefinition.default``)

    The cache stores only non-sensitive DB values.  Sensitive values
    are decrypted on every read to avoid holding plaintext secrets
    in memory.

    Args:
        repository: Persistence repository for DB settings.
        registry: Setting metadata registry.
        config: Root company configuration for YAML resolution.
        encryptor: Optional encryptor for sensitive settings.
        message_bus: Optional message bus for change notifications.
    """

    def __init__(
        self,
        *,
        repository: SettingsRepository,
        registry: SettingsRegistry,
        config: object,
        encryptor: SettingsEncryptor | None = None,
        message_bus: MessageBus | None = None,
    ) -> None:
        self._repository = repository
        self._registry = registry
        self._config = config
        self._encryptor = encryptor
        self._message_bus = message_bus
        self._cache: dict[tuple[str, str], SettingValue] = {}

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
        except SettingsEncryptionError:
            logger.exception(
                SETTINGS_ENCRYPTION_ERROR,
                namespace=definition.namespace,
                key=definition.key,
                reason="decrypt_failed",
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
            logger.debug(
                SETTINGS_VALUE_RESOLVED,
                namespace=namespace,
                key=key,
                source="db",
            )
            return setting_value

        return self._resolve_fallback(definition)

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
            entry = self._resolve_with_db_lookup(defn, db_lookup.get(defn.key))
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
            entry = self._resolve_with_db_lookup(defn, db_hit)
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
        entries = tuple(
            self._resolve_with_db_lookup(
                defn,
                db_lookup.get((defn.namespace, defn.key)),
            )
            for defn in page_defs
        )
        return entries, has_more

    def _resolve_fallback(
        self,
        definition: SettingDefinition,
    ) -> SettingValue:
        """Resolve via env > YAML > code default (no DB lookup)."""
        ns = definition.namespace
        key = definition.key

        env_name = _env_var_name(ns, key)
        env_val = os.environ.get(env_name)
        if env_val is not None:
            logger.debug(SETTINGS_VALUE_RESOLVED, namespace=ns, key=key, source="env")
            return SettingValue(
                namespace=ns,
                key=key,
                value=env_val,
                source=SettingSource.ENVIRONMENT,
            )

        if definition.yaml_path is not None:
            yaml_val = extract_from_config(self._config, definition.yaml_path)
            if yaml_val is not None:
                logger.debug(
                    SETTINGS_VALUE_RESOLVED, namespace=ns, key=key, source="yaml"
                )
                return SettingValue(
                    namespace=ns,
                    key=key,
                    value=yaml_val,
                    source=SettingSource.YAML,
                )

        # default=None means "optional -- no built-in default".  Return
        # empty string as a sentinel (callers like ConfigResolver.get_int
        # will raise ValueError on empty, giving a clear error at the
        # consumer layer rather than here).
        default = definition.default if definition.default is not None else ""
        logger.debug(SETTINGS_VALUE_RESOLVED, namespace=ns, key=key, source="default")
        return SettingValue(
            namespace=ns,
            key=key,
            value=default,
            source=SettingSource.DEFAULT,
        )

    def _resolve_with_db_lookup(
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
            return SettingEntry(
                definition=definition,
                value=display,
                source=SettingSource.DATABASE,
                updated_at=updated_at,
            )

        # Fallback: env > YAML > default
        fallback = self._resolve_fallback(definition)
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
    ) -> SettingEntry:
        """Validate, encrypt, and persist a setting value with optional CAS.

        Pass ``expected_updated_at=""`` for first-write semantics.
        Raises ``VersionConflictError`` on CAS miss,
        ``SettingNotFoundError`` / ``SettingValidationError`` /
        ``SettingsEncryptionError`` on preflight failures.
        """
        definition = self._registry.get(namespace, key)
        if definition is None:
            logger.warning(SETTINGS_NOT_FOUND, namespace=namespace, key=key)
            msg = f"Unknown setting: {namespace}/{key}"
            raise SettingNotFoundError(msg)

        try:
            _validate_value(definition, value)
        except SettingValidationError:
            logger.warning(SETTINGS_VALIDATION_FAILED, namespace=namespace, key=key)
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
            from synthorg.api.errors import (  # noqa: PLC0415
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
        if namespace in _AUDITED_SETTING_NAMESPACES:
            logger.info(
                SECURITY_SETTINGS_CHANGED,
                namespace=namespace,
                key=key,
                action_type="set",
            )
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
        """
        if not items:
            msg = "set_many requires at least one item"
            raise ValueError(msg)

        updated_at = _now_iso()
        prepared, definitions = self._prepare_set_many(items, updated_at)

        written = await self._repository.set_many(
            prepared,
            expected_updated_at_map=expected_updated_at_map,
        )
        if not written:
            from synthorg.api.errors import (  # noqa: PLC0415
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
            if namespace in _AUDITED_SETTING_NAMESPACES:
                logger.info(
                    SECURITY_SETTINGS_CHANGED,
                    namespace=namespace,
                    key=key,
                    action_type="set_many",
                )
            await self._publish_change(namespace, key, definition)

        return updated_at

    def _prepare_set_many(
        self,
        items: Sequence[tuple[str, str, str]],
        updated_at: str,
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
                raise SettingValidationError(msg)
            seen.add(pair)
            definition = self._registry.get(namespace, key)
            if definition is None:
                logger.warning(SETTINGS_NOT_FOUND, namespace=namespace, key=key)
                msg = f"Unknown setting: {namespace}/{key}"
                raise SettingNotFoundError(msg)

            try:
                _validate_value(definition, value)
            except SettingValidationError:
                logger.warning(SETTINGS_VALIDATION_FAILED, namespace=namespace, key=key)
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
        except SettingsEncryptionError:
            logger.exception(
                SETTINGS_ENCRYPTION_ERROR,
                namespace=definition.namespace,
                key=definition.key,
                reason="encrypt_failed",
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

        await self._repository.delete(NotBlankStr(namespace), NotBlankStr(key))

        self._invalidate_cache(namespace, key)

        logger.info(
            SETTINGS_VALUE_DELETED,
            namespace=namespace,
            key=key,
        )
        if namespace in _AUDITED_SETTING_NAMESPACES:
            logger.info(
                SECURITY_SETTINGS_CHANGED,
                namespace=namespace,
                key=key,
                action_type="delete",
            )

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
        if namespace in _AUDITED_SETTING_NAMESPACES:
            logger.info(
                SECURITY_SETTINGS_CHANGED,
                namespace=namespace,
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
            logger.warning(
                SETTINGS_NOTIFICATION_FAILED,
                namespace=namespace,
                key=key,
                error=str(exc),
                error_type=type(exc).__name__,
                exc_info=True,
            )
