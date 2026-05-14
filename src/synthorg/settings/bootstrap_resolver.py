"""Pre-``SettingsService`` resolver for Category-2 boot-time reads.

Some registered settings are consumed at app construction time, before
``SettingsService`` has been wired (rate-limiter middleware, log
directory, console log level). Reading ``os.environ`` directly at
these sites is drift: the registry already owns the env-var name and
the default. This module provides the sanctioned pre-init resolver
that honours the env > default chain uniformly.

For post-init reads, use ``ConfigResolver.get_*()`` instead.
"""

import os
from collections.abc import Callable, Mapping  # noqa: TC003
from typing import Generic, TypeVar

from pydantic import BaseModel, ConfigDict

from synthorg.settings.enums import SettingNamespace, SettingSource
from synthorg.settings.errors import SettingNotFoundError
from synthorg.settings.registry import get_registry


def _env_var_name(namespace: str, key: str) -> str:
    """Auto-derived env-var name for a (namespace, key) pair."""
    return f"SYNTHORG_{namespace.upper()}_{key.upper()}"


T = TypeVar("T")


class BootstrapResolvedValue(BaseModel, Generic[T]):  # noqa: UP046
    """Result of a pre-init setting resolution.

    Attributes:
        value: The resolved value, either the raw env/default string or
            the parsed value returned by an optional ``parse`` callback.
        source: Origin of the value (ENVIRONMENT or DEFAULT). Never
            DATABASE since the bootstrap resolver runs before the
            persistence layer is wired.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    value: T
    source: SettingSource


def resolve_init_value(  # noqa: UP047
    namespace: SettingNamespace,
    key: str,
    *,
    env: Mapping[str, str] = os.environ,
    parse: Callable[[str], T | None] | None = None,
) -> BootstrapResolvedValue[T] | BootstrapResolvedValue[str]:
    """Resolve a setting value at app construction time.

    Reads the ``SettingDefinition`` from the registry to obtain the
    env-var name (``env_var_override`` or auto-derived
    ``SYNTHORG_<NAMESPACE>_<KEY>``) and the typed default, then applies
    env > default.  When ``parse`` is supplied, the raw string is
    passed through it; if parse returns ``None`` the env value is
    treated as invalid and the registered default is used instead.

    Args:
        namespace: Setting namespace from the registry.
        key: Setting key within the namespace.
        env: Mapping to consult for environment variables. Defaults to
            ``os.environ``; tests pass a custom dict.
        parse: Optional callback that converts the env string to the
            consumer's type. Return ``None`` to signal invalid input
            (falls back to default).

    Returns:
        A ``BootstrapResolvedValue`` with the resolved value (parsed
        if ``parse`` was supplied) and its source.

    Raises:
        SettingNotFoundError: If ``(namespace, key)`` is not registered.
    """
    registry = get_registry()
    definition = registry.get(str(namespace), key)
    if definition is None:
        msg = f"Unknown setting: {namespace}/{key}"
        raise SettingNotFoundError(msg)

    env_name = (
        definition.env_var_override
        if definition.env_var_override is not None
        else _env_var_name(str(namespace), key)
    )
    env_raw = env.get(env_name, "").strip()

    if env_raw:
        if parse is not None:
            parsed = parse(env_raw)
            if parsed is not None:
                return BootstrapResolvedValue(
                    value=parsed,
                    source=SettingSource.ENVIRONMENT,
                )
        else:
            return BootstrapResolvedValue(
                value=env_raw,
                source=SettingSource.ENVIRONMENT,
            )

    default = definition.default if definition.default is not None else ""
    if parse is not None:
        parsed_default = parse(default)
        if parsed_default is not None:
            return BootstrapResolvedValue(
                value=parsed_default,
                source=SettingSource.DEFAULT,
            )

    return BootstrapResolvedValue(value=default, source=SettingSource.DEFAULT)
