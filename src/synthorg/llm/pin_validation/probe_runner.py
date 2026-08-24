# module-kind: code
"""Runner that probes a prompt class's pinned capability.

For each case it reconstructs the pin the case carries, runs the canonical
probe against the pinned capability model through a
:meth:`CompletionProvider.complete` call with the pinned sampling
parameters, and returns the provider's raw output for the grader to
fingerprint. Callers inject a deterministic provider (a
:class:`~synthorg.providers.drivers.scripted.ScriptedDriver`), so the probe is
reproducible with no network or spend.
"""

from synthorg.llm.pin_validation.case_models import PinTestCase
from synthorg.llm.pin_validation.probe import (
    pin_from_case_metadata,
    probe_config,
    probe_messages,
)
from synthorg.providers.protocol import CompletionProvider


class PinProbeRunner:
    """Runs the pin probe for a test case against its pinned capability.

    Args:
        provider: The deterministic completion provider the probe runs
            against.
    """

    def __init__(self, *, provider: CompletionProvider) -> None:
        self._provider = provider

    async def run_case(self, case: PinTestCase) -> str:
        """Run the canonical probe for *case* against its pinned capability.

        Args:
            case: The pin-validation test case, carrying its pin payload
                in ``metadata`` and the probe prompt in ``input_data``.

        Returns:
            The provider's raw output string (empty when the provider
            returns no content).

        Raises:
            ValidationError: If *case* carries no well-formed pin payload
                in its ``metadata``, i.e. it is not a pin-validation case.
        """
        pin = pin_from_case_metadata(case.metadata)
        response = await self._provider.complete(
            probe_messages(case.input_data),
            str(pin.model),
            config=probe_config(pin),
        )
        return response.content or ""


__all__ = ["PinProbeRunner"]
