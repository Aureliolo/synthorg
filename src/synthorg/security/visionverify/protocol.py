"""Vision verifier service protocols.

Two seams:

- :class:`VisionVerifier` is the pluggable strategy that inspects the
  running UI's screenshots against the brief and produces a structured
  report. Variants: ``noop`` (disabled default), ``heuristic``
  (deterministic colour / rule checks), ``llm_vision`` (multimodal model).
- :class:`VisionVerifierGate` is the gate's outward surface; the
  ReviewGateService consumes it as an injected dependency.
"""

from typing import Protocol, runtime_checkable

from synthorg.security.visionverify.models import (
    VisionGateResult,
    VisionReviewInput,
    VisionVerificationReport,
)


@runtime_checkable
class VisionVerifier(Protocol):
    """Inspect a running UI deliverable against its brief.

    Implementations MUST be deterministic enough for the simulation
    harness when their kind is ``heuristic``; the ``llm_vision`` variant
    pins temperature so cassette replay is stable.
    """

    @property
    def kind(self) -> str:
        """Strategy name for logging and config discrimination."""
        ...

    async def verify(
        self,
        review_input: VisionReviewInput,
    ) -> VisionVerificationReport:
        """Return a structured report for ``review_input``.

        Implementations MUST NOT raise on adversarial deliverable
        content; untrusted brief / model text is wrapped at the prompt
        boundary, so a mismatch surfaces as a finding, not an exception.
        """
        ...


@runtime_checkable
class VisionVerifierGate(Protocol):
    """Outward gate surface consumed by the ReviewGateService."""

    async def evaluate(
        self,
        review_input: VisionReviewInput,
    ) -> VisionGateResult:
        """Evaluate ``review_input`` and return the gate's verdict.

        Concrete implementations apply a fail-OPEN policy: a verifier
        failure is converted to a synthetic INFO finding, never
        propagated. Only :class:`asyncio.CancelledError` (and unexpected
        programming errors) propagate.
        """
        ...
