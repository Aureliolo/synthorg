"""Vision verifier configuration re-export.

``VisionVerifyConfig`` and ``VisionVerifierKind`` live in
:mod:`synthorg.security.config` alongside ``RedTeamConfig`` so the
top-level ``SecurityConfig`` can reference them without importing the
verifier package (which would pull engine / provider modules into the
security-config import and create a cycle). This module re-exports them
so the verifier subsystem keeps a local, discoverable import path.
"""

from synthorg.security.config import (
    VISION_DEFAULT_COLOUR_TOLERANCE,
    VISION_TIMEOUT_DEFAULT_SECONDS,
    VISION_TIMEOUT_MAX_SECONDS,
    VisionVerifierKind,
    VisionVerifyConfig,
)

__all__ = (
    "VISION_DEFAULT_COLOUR_TOLERANCE",
    "VISION_TIMEOUT_DEFAULT_SECONDS",
    "VISION_TIMEOUT_MAX_SECONDS",
    "VisionVerifierKind",
    "VisionVerifyConfig",
)
