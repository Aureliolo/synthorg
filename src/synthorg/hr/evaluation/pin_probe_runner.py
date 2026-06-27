# module-kind: code
"""Agent runner that probes a prompt class's pinned tier.

:class:`PinProbeRunner` is the :class:`AgentRunner` the pin-validation
benchmark drives through :class:`ExternalBenchmarkRegistry`. For each
case it reconstructs the pin the case carries, runs the canonical probe
against the pinned tier model through a real
:meth:`CompletionProvider.complete` call with the pinned sampling
parameters, and returns the provider's raw output for the grader to
fingerprint. The benchmark injects a deterministic provider (typically a
:class:`~synthorg.providers.drivers.scripted.ScriptedDriver`), so the
probe is reproducible with no network or spend.
"""

from synthorg.hr.evaluation.external_benchmark_models import EvalTestCase
from synthorg.hr.evaluation.pin_probe import (
    pin_from_case_metadata,
    probe_config,
    probe_messages,
)
from synthorg.providers.protocol import CompletionProvider


class PinProbeRunner:
    """Runs the pin probe for a test case against its pinned tier.

    Args:
        provider: The deterministic completion provider the probe runs
            against.
    """

    def __init__(self, *, provider: CompletionProvider) -> None:
        self._provider = provider

    async def run_case(self, case: EvalTestCase) -> str:
        """Run the canonical probe for *case* against its pinned tier.

        Args:
            case: The pin-validation test case, carrying its pin payload
                in ``metadata`` and the probe prompt in ``input_data``.

        Returns:
            The provider's raw output string (empty when the provider
            returns no content).

        Raises:
            KeyError: If *case* carries no pin payload (``PIN_META_KEY``
                absent from its ``metadata``) -- i.e. it is not a
                pin-validation case.
        """
        pin = pin_from_case_metadata(case.metadata)
        response = await self._provider.complete(
            probe_messages(case.input_data),
            str(pin.model),
            config=probe_config(pin),
        )
        return response.content or ""


__all__ = ["PinProbeRunner"]
