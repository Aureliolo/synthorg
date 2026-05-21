"""Vision verifier subsystem: the UI cousin of the red-team gate.

A pluggable :class:`VisionVerifier` inspects screenshots of a running
GUI deliverable against its brief and produces a structured report; the
:class:`VisionVerifierGateService` maps the report's findings to a
verdict that gates the deliverable's completion.
"""

from synthorg.security.visionverify.builder import build_vision_verifier_gate
from synthorg.security.visionverify.config import (
    VisionVerifierKind,
    VisionVerifyConfig,
)
from synthorg.security.visionverify.errors import (
    VisionDomainError,
    VisionModelUnsupportedError,
    VisionScreenshotError,
    VisionVerifyConfigError,
)
from synthorg.security.visionverify.factory import build_vision_verifier
from synthorg.security.visionverify.gate import VisionVerifierGateService
from synthorg.security.visionverify.models import (
    VisionFinding,
    VisionFindingCategory,
    VisionGateResult,
    VisionReviewInput,
    VisionScreenshotRef,
    VisionSeverity,
    VisionVerdict,
    VisionVerificationReport,
    VisualExpectation,
    VisualExpectationKind,
)
from synthorg.security.visionverify.protocol import VisionVerifier, VisionVerifierGate
from synthorg.security.visionverify.routing import compute_vision_verdict

__all__ = (
    "VisionDomainError",
    "VisionFinding",
    "VisionFindingCategory",
    "VisionGateResult",
    "VisionModelUnsupportedError",
    "VisionReviewInput",
    "VisionScreenshotError",
    "VisionScreenshotRef",
    "VisionSeverity",
    "VisionVerdict",
    "VisionVerificationReport",
    "VisionVerifier",
    "VisionVerifierGate",
    "VisionVerifierGateService",
    "VisionVerifierKind",
    "VisionVerifyConfig",
    "VisionVerifyConfigError",
    "VisualExpectation",
    "VisualExpectationKind",
    "build_vision_verifier",
    "build_vision_verifier_gate",
    "compute_vision_verdict",
)
