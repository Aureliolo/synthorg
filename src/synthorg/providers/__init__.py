"""Unified provider interface for LLM completion.

Exports protocols, base classes, domain models, enums, errors,
driver implementations, and the provider registry.
"""

import threading
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from synthorg.providers.base import BaseCompletionProvider
    from synthorg.providers.capabilities import ModelCapabilities
    from synthorg.providers.cassette import (
        CASSETTE_FORMAT_VERSION,
        CassetteCompletionProvider,
        CassetteConfig,
        CassetteError,
        CassetteFormatError,
        CassetteMode,
        CassetteRedactor,
        CassetteReplayExhaustedError,
        CassetteReplayMissError,
        CassetteSession,
        NullRedactor,
        PatternRedactor,
    )
    from synthorg.providers.cost_recording import (
        CostRecordingContext,
        cost_recording_scope,
        current_cost_context,
        resolve_currency,
    )
    from synthorg.providers.drivers import LiteLLMDriver
    from synthorg.providers.enums import (
        MessageRole,
        StreamEventType,
    )
    from synthorg.providers.errors import (
        AuthenticationError,
        ContentFilterError,
        DriverAlreadyRegisteredError,
        DriverFactoryNotFoundError,
        DriverNotRegisteredError,
        InvalidRequestError,
        ModelNotFoundError,
        ProviderConnectionError,
        ProviderError,
        ProviderImageGenerationUnsupportedError,
        ProviderInternalError,
        ProviderTimeoutError,
        RateLimitError,
    )
    from synthorg.providers.image_models import (
        GeneratedImage,
        ImageGenerationConfig,
        ImageGenerationResponse,
    )
    from synthorg.providers.models import (
        ZERO_TOKEN_USAGE,
        ChatMessage,
        CompletionConfig,
        CompletionResponse,
        StreamChunk,
        TokenUsage,
        ToolCall,
        ToolDefinition,
        ToolResult,
        add_token_usage,
    )
    from synthorg.providers.protocol import CompletionProvider
    from synthorg.providers.registry import ProviderRegistry
    from synthorg.providers.resilience import (
        RateLimiter,
        RetryExhaustedError,
        RetryHandler,
    )
    from synthorg.providers.routing import (
        STRATEGY_MAP,
        STRATEGY_NAME_COST_AWARE,
        STRATEGY_NAME_FASTEST,
        STRATEGY_NAME_MANUAL,
        STRATEGY_NAME_SMART,
        CostAwareStrategy,
        FastestStrategy,
        ManualStrategy,
        ModelResolutionError,
        ModelResolver,
        ModelRouter,
        NoAvailableModelError,
        ResolvedModel,
        RoutingDecision,
        RoutingError,
        RoutingRequest,
        RoutingStrategy,
        SmartStrategy,
        UnknownRoutingStrategyError,
    )

# name -> (module path, attribute) for PEP 562 lazy resolution. The eager
# re-exports pulled the driver + routing subgraph (and its litellm closure) in
# at package import, so importing a light ``providers.*`` leaf such as
# ``providers.enums`` dragged the whole thing in and helped close a cross-package
# import cycle (ADR-0012). Resolving on first access keeps
# ``from synthorg.providers import ProviderRegistry`` working unchanged.
_LAZY_EXPORTS: Final[dict[str, tuple[str, str]]] = {
    "BaseCompletionProvider": ("synthorg.providers.base", "BaseCompletionProvider"),
    "ModelCapabilities": ("synthorg.providers.capabilities", "ModelCapabilities"),
    "CASSETTE_FORMAT_VERSION": (
        "synthorg.providers.cassette",
        "CASSETTE_FORMAT_VERSION",
    ),
    "CassetteCompletionProvider": (
        "synthorg.providers.cassette",
        "CassetteCompletionProvider",
    ),
    "CassetteConfig": ("synthorg.providers.cassette", "CassetteConfig"),
    "CassetteError": ("synthorg.providers.cassette", "CassetteError"),
    "CassetteFormatError": ("synthorg.providers.cassette", "CassetteFormatError"),
    "CassetteMode": ("synthorg.providers.cassette", "CassetteMode"),
    "CassetteRedactor": ("synthorg.providers.cassette", "CassetteRedactor"),
    "CassetteReplayExhaustedError": (
        "synthorg.providers.cassette",
        "CassetteReplayExhaustedError",
    ),
    "CassetteReplayMissError": (
        "synthorg.providers.cassette",
        "CassetteReplayMissError",
    ),
    "CassetteSession": ("synthorg.providers.cassette", "CassetteSession"),
    "NullRedactor": ("synthorg.providers.cassette", "NullRedactor"),
    "PatternRedactor": ("synthorg.providers.cassette", "PatternRedactor"),
    "CostRecordingContext": (
        "synthorg.providers.cost_recording",
        "CostRecordingContext",
    ),
    "cost_recording_scope": (
        "synthorg.providers.cost_recording",
        "cost_recording_scope",
    ),
    "current_cost_context": (
        "synthorg.providers.cost_recording",
        "current_cost_context",
    ),
    "resolve_currency": ("synthorg.providers.cost_recording", "resolve_currency"),
    "LiteLLMDriver": ("synthorg.providers.drivers", "LiteLLMDriver"),
    "MessageRole": ("synthorg.providers.enums", "MessageRole"),
    "StreamEventType": ("synthorg.providers.enums", "StreamEventType"),
    "AuthenticationError": ("synthorg.providers.errors", "AuthenticationError"),
    "ContentFilterError": ("synthorg.providers.errors", "ContentFilterError"),
    "DriverAlreadyRegisteredError": (
        "synthorg.providers.errors",
        "DriverAlreadyRegisteredError",
    ),
    "DriverFactoryNotFoundError": (
        "synthorg.providers.errors",
        "DriverFactoryNotFoundError",
    ),
    "DriverNotRegisteredError": (
        "synthorg.providers.errors",
        "DriverNotRegisteredError",
    ),
    "InvalidRequestError": ("synthorg.providers.errors", "InvalidRequestError"),
    "ModelNotFoundError": ("synthorg.providers.errors", "ModelNotFoundError"),
    "ProviderConnectionError": (
        "synthorg.providers.errors",
        "ProviderConnectionError",
    ),
    "ProviderError": ("synthorg.providers.errors", "ProviderError"),
    "ProviderImageGenerationUnsupportedError": (
        "synthorg.providers.errors",
        "ProviderImageGenerationUnsupportedError",
    ),
    "ProviderInternalError": ("synthorg.providers.errors", "ProviderInternalError"),
    "ProviderTimeoutError": ("synthorg.providers.errors", "ProviderTimeoutError"),
    "RateLimitError": ("synthorg.providers.errors", "RateLimitError"),
    "GeneratedImage": ("synthorg.providers.image_models", "GeneratedImage"),
    "ImageGenerationConfig": (
        "synthorg.providers.image_models",
        "ImageGenerationConfig",
    ),
    "ImageGenerationResponse": (
        "synthorg.providers.image_models",
        "ImageGenerationResponse",
    ),
    "ZERO_TOKEN_USAGE": ("synthorg.providers.models", "ZERO_TOKEN_USAGE"),
    "ChatMessage": ("synthorg.providers.models", "ChatMessage"),
    "CompletionConfig": ("synthorg.providers.models", "CompletionConfig"),
    "CompletionResponse": ("synthorg.providers.models", "CompletionResponse"),
    "StreamChunk": ("synthorg.providers.models", "StreamChunk"),
    "TokenUsage": ("synthorg.providers.models", "TokenUsage"),
    "ToolCall": ("synthorg.providers.models", "ToolCall"),
    "ToolDefinition": ("synthorg.providers.models", "ToolDefinition"),
    "ToolResult": ("synthorg.providers.models", "ToolResult"),
    "add_token_usage": ("synthorg.providers.models", "add_token_usage"),
    "CompletionProvider": ("synthorg.providers.protocol", "CompletionProvider"),
    "ProviderRegistry": ("synthorg.providers.registry", "ProviderRegistry"),
    "RateLimiter": ("synthorg.providers.resilience", "RateLimiter"),
    "RetryExhaustedError": ("synthorg.providers.resilience", "RetryExhaustedError"),
    "RetryHandler": ("synthorg.providers.resilience", "RetryHandler"),
    "STRATEGY_MAP": ("synthorg.providers.routing", "STRATEGY_MAP"),
    "STRATEGY_NAME_COST_AWARE": (
        "synthorg.providers.routing",
        "STRATEGY_NAME_COST_AWARE",
    ),
    "STRATEGY_NAME_FASTEST": ("synthorg.providers.routing", "STRATEGY_NAME_FASTEST"),
    "STRATEGY_NAME_MANUAL": ("synthorg.providers.routing", "STRATEGY_NAME_MANUAL"),
    "STRATEGY_NAME_SMART": ("synthorg.providers.routing", "STRATEGY_NAME_SMART"),
    "CostAwareStrategy": ("synthorg.providers.routing", "CostAwareStrategy"),
    "FastestStrategy": ("synthorg.providers.routing", "FastestStrategy"),
    "ManualStrategy": ("synthorg.providers.routing", "ManualStrategy"),
    "ModelResolutionError": ("synthorg.providers.routing", "ModelResolutionError"),
    "ModelResolver": ("synthorg.providers.routing", "ModelResolver"),
    "ModelRouter": ("synthorg.providers.routing", "ModelRouter"),
    "NoAvailableModelError": ("synthorg.providers.routing", "NoAvailableModelError"),
    "ResolvedModel": ("synthorg.providers.routing", "ResolvedModel"),
    "RoutingDecision": ("synthorg.providers.routing", "RoutingDecision"),
    "RoutingError": ("synthorg.providers.routing", "RoutingError"),
    "RoutingRequest": ("synthorg.providers.routing", "RoutingRequest"),
    "RoutingStrategy": ("synthorg.providers.routing", "RoutingStrategy"),
    "SmartStrategy": ("synthorg.providers.routing", "SmartStrategy"),
    "UnknownRoutingStrategyError": (
        "synthorg.providers.routing",
        "UnknownRoutingStrategyError",
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


__all__ = [
    "CASSETTE_FORMAT_VERSION",
    "STRATEGY_MAP",
    "STRATEGY_NAME_COST_AWARE",
    "STRATEGY_NAME_FASTEST",
    "STRATEGY_NAME_MANUAL",
    "STRATEGY_NAME_SMART",
    "ZERO_TOKEN_USAGE",
    "AuthenticationError",
    "BaseCompletionProvider",
    "CassetteCompletionProvider",
    "CassetteConfig",
    "CassetteError",
    "CassetteFormatError",
    "CassetteMode",
    "CassetteRedactor",
    "CassetteReplayExhaustedError",
    "CassetteReplayMissError",
    "CassetteSession",
    "ChatMessage",
    "CompletionConfig",
    "CompletionProvider",
    "CompletionResponse",
    "ContentFilterError",
    "CostAwareStrategy",
    "CostRecordingContext",
    "DriverAlreadyRegisteredError",
    "DriverFactoryNotFoundError",
    "DriverNotRegisteredError",
    "FastestStrategy",
    "GeneratedImage",
    "ImageGenerationConfig",
    "ImageGenerationResponse",
    "InvalidRequestError",
    "LiteLLMDriver",
    "ManualStrategy",
    "MessageRole",
    "ModelCapabilities",
    "ModelNotFoundError",
    "ModelResolutionError",
    "ModelResolver",
    "ModelRouter",
    "NoAvailableModelError",
    "NullRedactor",
    "PatternRedactor",
    "ProviderConnectionError",
    "ProviderError",
    "ProviderImageGenerationUnsupportedError",
    "ProviderInternalError",
    "ProviderRegistry",
    "ProviderTimeoutError",
    "RateLimitError",
    "RateLimiter",
    "ResolvedModel",
    "RetryExhaustedError",
    "RetryHandler",
    "RoutingDecision",
    "RoutingError",
    "RoutingRequest",
    "RoutingStrategy",
    "SmartStrategy",
    "StreamChunk",
    "StreamEventType",
    "TokenUsage",
    "ToolCall",
    "ToolDefinition",
    "ToolResult",
    "UnknownRoutingStrategyError",
    "add_token_usage",
    "cost_recording_scope",
    "current_cost_context",
    "resolve_currency",
]
