# module-kind: code
"""Typed errors for the OpenHands execution loop."""

from typing import ClassVar

from synthorg.core.domain_errors import ServiceUnavailableError
from synthorg.engine.errors import EngineError


class OpenHandsLoopError(EngineError):
    """Base for OpenHands execution-loop failures (500).

    An engine-layer error: inherits :class:`EngineError` so it shares the
    ``ENGINE_ERROR`` code as an inheritance alias rather than claiming a
    duplicate mapping.
    """

    default_message: ClassVar[str] = "OpenHands execution loop failed"


class OpenHandsRuntimeError(OpenHandsLoopError):
    """Raised when the OpenHands runtime fails mid-run.

    Inherits :class:`OpenHandsLoopError` (an inheritance alias for the
    error-code-uniqueness gate); the adapter maps it onto a terminal
    ``ERROR`` ``ExecutionResult`` rather than propagating.
    """


class OpenHandsUnavailableError(ServiceUnavailableError):
    """Raised when the OpenHands SDK / runtime is not available (503).

    The ``openhands-sdk`` is bundled only in the agent sandbox image, never
    the main package venv, so a loop built without a runtime factory (or on a
    host without the image) fails loud here instead of silently degrading.
    """

    default_message: ClassVar[str] = "OpenHands runtime is not available"
