# module-kind: code
"""Deterministic probes for the pin-validation benchmark.

Turns a :class:`ModelPinMetadata` (built by
:func:`synthorg.llm.model_pins.pin_for`, the single pin source) into the probe
prompt and completion config the benchmark runs against the pinned tier, plus
the drift fingerprint over the probe output. The benchmark's case builder, its
grader, and the probe runner all read these helpers so they share one definition.
"""

from collections.abc import Mapping
from typing import Final

from pydantic import BaseModel, ConfigDict

from synthorg.core.boundary import parse_typed
from synthorg.hr.evaluation.pin_fingerprint import pin_fingerprint
from synthorg.llm.metadata import ModelPinMetadata
from synthorg.llm.prompt_purpose import PromptPurposeId
from synthorg.providers.enums import MessageRole
from synthorg.providers.models import ChatMessage, CompletionConfig

#: Metadata key under which an ``EvalTestCase`` carries its pin payload.
#: Kept in sync with the ``pin`` field of :class:`_PinCaseMetadata`.
PIN_META_KEY: Final[str] = "pin"


class _PinCaseMetadata(BaseModel):
    """Typed envelope for an ``EvalTestCase``'s pin metadata mapping.

    The serialisation boundary between ``load_test_cases`` (which writes
    ``metadata``) and ``grade`` / the probe runner (which read it). Parsing
    the whole mapping through this model (``extra="forbid"``) rejects a
    malformed or padded envelope, not only a malformed inner pin.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    pin: ModelPinMetadata


def probe_input_data(prompt_class_id: str | PromptPurposeId) -> str:
    """Return the canonical probe prompt for a prompt class.

    The id is embedded so a deterministic provider yields a distinct,
    reproducible output per class.

    Returns:
        The probe prompt string.
    """
    return (
        f"Pin-validation probe for prompt class {prompt_class_id}. "
        f"Reply with the single token OK."
    )


def probe_messages(input_data: str) -> list[ChatMessage]:
    """Build the chat messages for a probe completion.

    Returns:
        A single-element user-message list.
    """
    return [ChatMessage(role=MessageRole.USER, content=input_data)]


def probe_config(pin: ModelPinMetadata) -> CompletionConfig:
    """Build the completion config from a pin's sampling parameters.

    Returns:
        A :class:`CompletionConfig` carrying the pinned temperature,
        top-p, and max-tokens.
    """
    return CompletionConfig(
        temperature=pin.temperature,
        top_p=pin.top_p,
        max_tokens=pin.max_tokens,
    )


def pin_metadata_payload(pin: ModelPinMetadata) -> dict[str, object]:
    """Serialise a pin to the JSON-able payload carried on a test case.

    Returns:
        The pin as a JSON-mode dict, suitable for ``EvalTestCase.metadata``.
    """
    return pin.model_dump(mode="json")


def pin_from_case_metadata(metadata: Mapping[str, object]) -> ModelPinMetadata:
    """Reconstruct the pin from a test case's metadata payload.

    Parses the full metadata envelope through :func:`parse_typed`, so a
    case missing its pin or carrying unexpected keys is rejected at this
    boundary rather than only the inner pin being validated.

    Returns:
        The :class:`ModelPinMetadata` the case carries.

    Raises:
        ValidationError: If the metadata is not a well-formed pin
            envelope (no ``pin`` payload, or unexpected keys).
    """
    return parse_typed("eval.pin_case", metadata, _PinCaseMetadata).pin


def fingerprint_for(pin: ModelPinMetadata, output: str) -> str:
    """Compute the drift fingerprint for a pin and a probe output.

    Returns:
        The hex SHA-256 fingerprint of the pin contract plus *output*.
    """
    return pin_fingerprint(
        model_id=str(pin.model),
        temperature=pin.temperature,
        top_p=pin.top_p,
        max_tokens=pin.max_tokens,
        output=output,
    )


__all__ = [
    "PIN_META_KEY",
    "fingerprint_for",
    "pin_from_case_metadata",
    "pin_metadata_payload",
    "probe_config",
    "probe_input_data",
    "probe_messages",
]
