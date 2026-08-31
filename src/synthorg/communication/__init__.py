# module-kind: declarative
"""Communication subsystem for the SynthOrg framework."""

import threading
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from synthorg.communication.bus.memory import InMemoryMessageBus
    from synthorg.communication.bus_protocol import MessageBus
    from synthorg.communication.channel import Channel
    from synthorg.communication.config import (
        CommunicationConfig,
        HierarchyConfig,
        MessageBusConfig,
        MessageRetentionConfig,
    )
    from synthorg.communication.dispatcher import (
        DispatchResult,
        MessageDispatcher,
    )
    from synthorg.communication.enums import (
        ChannelType,
        CommunicationPattern,
        MessageBusBackend,
        MessagePriority,
        MessageType,
    )
    from synthorg.communication.errors import (
        ChannelAlreadyExistsError,
        ChannelNotFoundError,
        CommunicationError,
        HierarchyResolutionError,
        MessageBusAlreadyRunningError,
        MessageBusNotRunningError,
        NotSubscribedError,
    )
    from synthorg.communication.handler import (
        FunctionHandler,
        HandlerRegistration,
        MessageHandler,
        MessageHandlerFunc,
    )
    from synthorg.communication.hierarchy import HierarchyResolver
    from synthorg.communication.message import (
        DataPart,
        FilePart,
        Message,
        MessageMetadata,
        Part,
        TextPart,
        UriPart,
    )
    from synthorg.communication.subscription import (
        DeliveryEnvelope,
        Subscription,
    )

# name -> (module path, attribute) for PEP 562 lazy resolution. The eager
# re-exports pulled the heavy engine / communication graph in at package
# import, so importing a light leaf under this package dragged the whole
# subgraph and could close a cross-package import cycle (ADR-0012). Resolving
# on first access keeps ``from synthorg.communication import Name`` working
# unchanged.
_LAZY_EXPORTS: Final[dict[str, tuple[str, str]]] = {
    "InMemoryMessageBus": (
        "synthorg.communication.bus.memory",
        "InMemoryMessageBus",
    ),
    "MessageBus": ("synthorg.communication.bus_protocol", "MessageBus"),
    "Channel": ("synthorg.communication.channel", "Channel"),
    "CommunicationConfig": (
        "synthorg.communication.config",
        "CommunicationConfig",
    ),
    "HierarchyConfig": ("synthorg.communication.config", "HierarchyConfig"),
    "MessageBusConfig": ("synthorg.communication.config", "MessageBusConfig"),
    "MessageRetentionConfig": (
        "synthorg.communication.config",
        "MessageRetentionConfig",
    ),
    "DispatchResult": ("synthorg.communication.dispatcher", "DispatchResult"),
    "MessageDispatcher": (
        "synthorg.communication.dispatcher",
        "MessageDispatcher",
    ),
    "ChannelType": ("synthorg.communication.enums", "ChannelType"),
    "CommunicationPattern": (
        "synthorg.communication.enums",
        "CommunicationPattern",
    ),
    "MessageBusBackend": (
        "synthorg.communication.enums",
        "MessageBusBackend",
    ),
    "MessagePriority": ("synthorg.communication.enums", "MessagePriority"),
    "MessageType": ("synthorg.communication.enums", "MessageType"),
    "ChannelAlreadyExistsError": (
        "synthorg.communication.errors",
        "ChannelAlreadyExistsError",
    ),
    "ChannelNotFoundError": (
        "synthorg.communication.errors",
        "ChannelNotFoundError",
    ),
    "CommunicationError": (
        "synthorg.communication.errors",
        "CommunicationError",
    ),
    "HierarchyResolutionError": (
        "synthorg.communication.errors",
        "HierarchyResolutionError",
    ),
    "MessageBusAlreadyRunningError": (
        "synthorg.communication.errors",
        "MessageBusAlreadyRunningError",
    ),
    "MessageBusNotRunningError": (
        "synthorg.communication.errors",
        "MessageBusNotRunningError",
    ),
    "NotSubscribedError": (
        "synthorg.communication.errors",
        "NotSubscribedError",
    ),
    "FunctionHandler": ("synthorg.communication.handler", "FunctionHandler"),
    "HandlerRegistration": (
        "synthorg.communication.handler",
        "HandlerRegistration",
    ),
    "MessageHandler": ("synthorg.communication.handler", "MessageHandler"),
    "MessageHandlerFunc": (
        "synthorg.communication.handler",
        "MessageHandlerFunc",
    ),
    "HierarchyResolver": (
        "synthorg.communication.hierarchy",
        "HierarchyResolver",
    ),
    "DataPart": ("synthorg.communication.message", "DataPart"),
    "FilePart": ("synthorg.communication.message", "FilePart"),
    "Message": ("synthorg.communication.message", "Message"),
    "MessageMetadata": (
        "synthorg.communication.message",
        "MessageMetadata",
    ),
    "Part": ("synthorg.communication.message", "Part"),
    "TextPart": ("synthorg.communication.message", "TextPart"),
    "UriPart": ("synthorg.communication.message", "UriPart"),
    "DeliveryEnvelope": (
        "synthorg.communication.subscription",
        "DeliveryEnvelope",
    ),
    "Subscription": ("synthorg.communication.subscription", "Subscription"),
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


__all__ = [
    "Channel",
    "ChannelAlreadyExistsError",
    "ChannelNotFoundError",
    "ChannelType",
    "CommunicationConfig",
    "CommunicationError",
    "CommunicationPattern",
    "DataPart",
    "DeliveryEnvelope",
    "DispatchResult",
    "FilePart",
    "FunctionHandler",
    "HandlerRegistration",
    "HierarchyConfig",
    "HierarchyResolutionError",
    "HierarchyResolver",
    "InMemoryMessageBus",
    "Message",
    "MessageBus",
    "MessageBusAlreadyRunningError",
    "MessageBusBackend",
    "MessageBusConfig",
    "MessageBusNotRunningError",
    "MessageDispatcher",
    "MessageHandler",
    "MessageHandlerFunc",
    "MessageMetadata",
    "MessagePriority",
    "MessageRetentionConfig",
    "MessageType",
    "NotSubscribedError",
    "Part",
    "Subscription",
    "TextPart",
    "UriPart",
]
