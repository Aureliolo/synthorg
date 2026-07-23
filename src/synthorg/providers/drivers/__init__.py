"""Driver implementations for LLM provider backends.

Each driver subclasses ``BaseCompletionProvider`` and wraps a specific
backend SDK (e.g. LiteLLM).
"""

import threading
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from synthorg.providers.drivers.litellm_driver import LiteLLMDriver

# ``LiteLLMDriver`` transitively imports ``litellm`` (~5s cold). Re-exporting it
# eagerly here meant importing any light sibling (``litellm_model_catalog`` /
# ``litellm_model_info`` / ``scripted``) ran this package init and paid that
# cost, defeating the registry's deliberately function-local driver import.
# Resolving it lazily (PEP 562) keeps ``from synthorg.providers.drivers import
# LiteLLMDriver`` working while confining the ``litellm`` import to the moment a
# LiteLLM driver is actually constructed.
_LAZY_EXPORTS: Final[dict[str, tuple[str, str]]] = {
    "LiteLLMDriver": (
        "synthorg.providers.drivers.litellm_driver",
        "LiteLLMDriver",
    ),
}

_LAZY_EXPORT_LOCK: Final[threading.Lock] = threading.Lock()


def __getattr__(name: str) -> object:
    """Resolve and cache a lazily-exported symbol on first access (PEP 562).

    Returns:
        The resolved (and now cached) export object for ``name``.

    Raises:
        AttributeError: When ``name`` is not a known lazy export.
    """
    if name not in _LAZY_EXPORTS:
        msg = f"module {__name__!r} has no attribute {name!r}"
        raise AttributeError(msg)
    import importlib  # noqa: PLC0415

    if name in globals():
        return globals()[name]
    module_path, attr = _LAZY_EXPORTS[name]
    # Resolve the import OUTSIDE the lock: importing the target runs arbitrary
    # module-level code that can re-enter this hub (the import cycles this lazy
    # machinery exists to break), so holding a non-reentrant lock across the
    # import would risk a same-thread self-deadlock or a cross-hub lock-order
    # inversion. Python's per-module import lock already dedups the work, so a
    # racing first access at worst resolves the idempotent value twice;
    # ``setdefault`` keeps a single cached object.
    value = getattr(importlib.import_module(module_path), attr)
    with _LAZY_EXPORT_LOCK:
        return globals().setdefault(name, value)


def __dir__() -> list[str]:
    """Include the lazily-exported names in ``dir()`` / autocomplete.

    Returns:
        The sorted list of public export names.
    """
    return sorted(__all__)


__all__ = ["LiteLLMDriver"]
