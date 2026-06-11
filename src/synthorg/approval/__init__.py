"""Shared approval types and protocols.

A neutral subsystem module so ``engine`` and ``tools`` can both depend on
approval event models (``EscalationInfo``, ``ResumePayload``) and on the
``ApprovalStoreProtocol`` contract without either module importing the
other.  This avoids the former cycle that was dodged by ``TYPE_CHECKING``
imports and deferred runtime imports inside function bodies.

``ApprovalStoreProtocol`` is exported lazily (PEP 562) so importing the
lightweight ``approval.enums`` / ``approval.models`` leaves does not pull
``approval.protocol`` -> ``core.approval`` at package-import time. That
eager edge closes a cycle: ``core.approval`` imports ``approval.enums``,
which runs this package init, which would otherwise import
``approval.protocol``, which imports back ``core.approval.ApprovalItem``
while it is still partially initialised.
``from synthorg.approval import ApprovalStoreProtocol`` still works,
resolved and cached on first access.

The concrete ``ApprovalStore`` implementation lives in
``synthorg.api.approval_store``; concrete ``ApprovalRepository``
implementations live under ``synthorg.persistence.{sqlite,postgres}``.
Callers depending on the protocol types here remain backend-agnostic.
"""

import threading
from typing import TYPE_CHECKING, Final

from synthorg.approval.models import EscalationInfo, ResumePayload

if TYPE_CHECKING:
    from synthorg.approval.protocol import ApprovalStoreProtocol

# name -> (module path, attribute) for PEP 562 lazy resolution.
_LAZY_EXPORTS: dict[str, tuple[str, str]] = {
    "ApprovalStoreProtocol": (
        "synthorg.approval.protocol",
        "ApprovalStoreProtocol",
    ),
}

_LAZY_EXPORT_LOCK: Final[threading.Lock] = threading.Lock()


def __getattr__(name: str) -> object:
    """Resolve and cache a lazily-exported symbol on first access (PEP 562).

    The cache write into ``globals()`` is guarded so concurrent
    first-access from multiple threads cannot double-import the submodule
    or overwrite the cached object mid-write (mirrors
    :mod:`synthorg.ontology`).

    Returns:
        The resolved (and now cached) export object for ``name``.

    Raises:
        AttributeError: When ``name`` is not a known lazy export.
    """
    if name not in _LAZY_EXPORTS:
        msg = f"module {__name__!r} has no attribute {name!r}"
        raise AttributeError(msg)
    import importlib  # noqa: PLC0415

    with _LAZY_EXPORT_LOCK:
        if name in globals():
            return globals()[name]
        module_path, attr = _LAZY_EXPORTS[name]
        value = getattr(importlib.import_module(module_path), attr)
        globals()[name] = value
        return value


def __dir__() -> list[str]:
    """Include the lazily-exported names in ``dir()`` / autocomplete.

    Returns:
        The sorted list of public export names.
    """
    return sorted(__all__)


__all__ = [
    "ApprovalStoreProtocol",
    "EscalationInfo",
    "ResumePayload",
]
