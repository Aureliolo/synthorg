"""Resolver side effects that answer every setting with its registered default.

A boot-path test hands the assembly a resolver double, and a double that
answers one number for every key is one refactor from feeding a validator a
value the real registry could never serve (a stagnation window of ``1``
against a floor of ``2`` stopped a whole test file). These read the same
registry the resolver itself reads, so the double answers exactly what an
untouched deployment would, and a key nothing registered fails loudly rather
than plausibly.
"""

import synthorg.settings.definitions  # noqa: F401 -- populates the registry
from synthorg.settings.errors import SettingsRegistryError
from synthorg.settings.registry import (
    get_registry,
    registered_default_bool,
    registered_default_float,
    registered_default_int,
)


async def default_int(namespace: str, key: str) -> int:
    """Answer ``get_int`` with the registered default.

    Returns:
        The default.
    """
    return registered_default_int(namespace, key)


async def default_float(namespace: str, key: str) -> float:
    """Answer ``get_float`` with the registered default.

    Returns:
        The default.
    """
    return registered_default_float(namespace, key)


async def default_bool(namespace: str, key: str) -> bool:
    """Answer ``get_bool`` with the registered default.

    Returns:
        The default.
    """
    return registered_default_bool(namespace, key)


async def default_str(namespace: str, key: str) -> str:
    """Answer ``get_str`` with the registered default.

    Returns:
        The default as the registry records it, or the empty string for a
        blank-default setting, which is what the resolver serves for one.

    Raises:
        SettingsRegistryError: Nothing registered the key.
    """
    definition = get_registry().get(namespace, key)
    if definition is None:
        msg = f"setting {namespace}.{key} is not registered"
        raise SettingsRegistryError(msg)
    return definition.default or ""


__all__ = ["default_bool", "default_float", "default_int", "default_str"]
