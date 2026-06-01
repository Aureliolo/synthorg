# module-kind: code
# ruff: noqa: EM101
"""Shared helpers for the infrastructure MCP facades.

The ``EM101`` suppression is intentional: capability-gap messages are
string literals passed straight to :class:`CapabilityNotSupportedError`.
"""

from typing import Any, Final

from synthorg.communication.mcp_errors import CapabilityNotSupportedError

_DEFAULT_LIMIT: Final[int] = 100


def _require_callable(
    target: Any,
    method_name: str,
    capability: str,
    detail: str,
) -> Any:
    """Return a callable attribute or raise ``CapabilityNotSupportedError``.

    Raises:
        CapabilityNotSupportedError: When ``method_name`` is absent or not
            callable on ``target``.
    """
    fn = getattr(target, method_name, None)
    if not callable(fn):
        raise CapabilityNotSupportedError(capability, detail)
    return fn


def _split_setting_key(key: str) -> tuple[str, str]:
    """Split a compound ``namespace.key`` wire identifier into its parts.

    The MCP wire-level ``synthorg_settings_*`` tools accept a single
    ``key`` argument; :class:`SettingsService` takes ``namespace`` and
    ``key`` as distinct values.  This helper enforces the boundary and
    surfaces a typed capability error when the caller omits the
    namespace (no dot present).

    Returns:
        The ``(namespace, key)`` pair parsed from the compound identifier.

    Raises:
        CapabilityNotSupportedError: When ``key`` has no dot, or an empty
            namespace or leaf.
    """
    namespace, dot, leaf = key.partition(".")
    if not dot or not namespace or not leaf:
        raise CapabilityNotSupportedError(
            "settings_key_format",
            f"setting key must be 'namespace.key' (got {key!r})",
        )
    return namespace, leaf
