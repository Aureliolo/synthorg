"""Settings metadata registry -- single source of truth for setting definitions."""

from types import MappingProxyType

from synthorg.observability import get_logger
from synthorg.observability.events.settings import SETTINGS_REGISTRY_DUPLICATE
from synthorg.settings.models import SettingDefinition  # noqa: TC001

logger = get_logger(__name__)


class SettingsRegistry:
    """Catalogue of all known setting definitions.

    Setting definitions are registered at import time by the
    ``definitions/`` sub-package.  Once populated, the registry
    is treated as read-only by the rest of the system.

    The registry is the single source of truth for what settings
    exist and drives validation, schema introspection, and dynamic
    UI generation.
    """

    def __init__(self) -> None:
        self._definitions: dict[tuple[str, str], SettingDefinition] = {}
        self._read_view: MappingProxyType[tuple[str, str], SettingDefinition] = (
            MappingProxyType(self._definitions)
        )

    def register(self, definition: SettingDefinition) -> None:
        """Register a setting definition.

        Args:
            definition: The setting definition to register.

        Raises:
            ValueError: If a setting with the same namespace and key
                is already registered.
        """
        composite_key = (definition.namespace, definition.key)
        if composite_key in self._definitions:
            logger.warning(
                SETTINGS_REGISTRY_DUPLICATE,
                namespace=definition.namespace,
                key=definition.key,
            )
            msg = f"Duplicate setting: {definition.namespace}/{definition.key}"
            raise ValueError(msg)
        self._definitions = {**self._definitions, composite_key: definition}
        self._read_view = MappingProxyType(self._definitions)

    def get(self, namespace: str, key: str) -> SettingDefinition | None:
        """Look up a setting definition by namespace and key.

        Args:
            namespace: Setting namespace.
            key: Setting key within the namespace.

        Returns:
            The definition, or ``None`` if not registered.
        """
        return self._read_view.get((namespace, key))

    def list_namespace(self, namespace: str) -> tuple[SettingDefinition, ...]:
        """Return all definitions in a namespace, sorted by key.

        Args:
            namespace: Setting namespace to filter by.

        Returns:
            Definitions sorted alphabetically by key.
        """
        return tuple(
            sorted(
                (d for d in self._read_view.values() if d.namespace == namespace),
                key=lambda d: d.key,
            )
        )

    def list_all(self) -> tuple[SettingDefinition, ...]:
        """Return all definitions, sorted by namespace then key.

        Returns:
            All registered definitions.
        """
        return tuple(
            sorted(
                self._read_view.values(),
                key=lambda d: (d.namespace, d.key),
            )
        )

    def namespaces(self) -> tuple[str, ...]:
        """Return sorted unique namespaces with at least one definition.

        Returns:
            Namespace strings in alphabetical order.
        """
        return tuple(sorted({d.namespace for d in self._read_view.values()}))

    @property
    def size(self) -> int:
        """Total number of registered definitions."""
        return len(self._read_view)


# Module-level singleton -- populated by definitions/ sub-package imports.
_registry = SettingsRegistry()


def get_registry() -> SettingsRegistry:
    """Return the global settings registry singleton."""
    return _registry


def _registered_default_str(namespace: str, key: str) -> str:
    """Return the registered default for ``(namespace, key)`` as a string.

    Raises if the setting is unregistered or has no default.  Callers
    that need a typed value pass the result through the matching
    ``registered_default_*`` helper.
    """
    definition = _registry.get(namespace, key)
    if definition is None:
        msg = f"setting {namespace}.{key} is not registered"
        raise LookupError(msg)
    if definition.default is None:
        msg = f"setting {namespace}.{key} has no registered default"
        raise LookupError(msg)
    return definition.default


def registered_default_int(namespace: str, key: str) -> int:
    """Return the registered default for ``(namespace, key)`` coerced to ``int``.

    Reads the canonical default from the registry rather than
    duplicating the literal in caller code -- the registry is the
    single source of truth, so consumer-side fallbacks (e.g. resolver
    outage paths) read the same value the resolver would have served
    on the happy path.
    """
    return int(_registered_default_str(namespace, key))


def registered_default_float(namespace: str, key: str) -> float:
    """Return the registered default for ``(namespace, key)`` coerced to ``float``."""
    return float(_registered_default_str(namespace, key))


def registered_default_bool(namespace: str, key: str) -> bool:
    """Return the registered default for ``(namespace, key)`` coerced to ``bool``.

    Accepts the canonical string representations recorded in the
    registry: ``"true"`` / ``"false"`` (case-insensitive).
    """
    raw = _registered_default_str(namespace, key).strip().lower()
    if raw == "true":
        return True
    if raw == "false":
        return False
    msg = f"setting {namespace}.{key} default {raw!r} is not a boolean string"
    raise ValueError(msg)
