# module-kind: code
"""Canonical pins + probes for the pin-validation benchmark.

This module builds, for each prompt purpose, the :class:`ModelPinMetadata`
the pin-validation benchmark exercises and the deterministic probe it runs
against the pinned tier. It is the single place that turns a
:class:`PromptPurposeId` into a concrete pin (tier model id plus canonical
sampling parameters) and the probe prompt, so the benchmark's case builder,
its grader, and the probe runner all read one definition.

The canonical pin uses provisional deterministic sampling defaults
(``temperature=0.0`` / ``top_p=1.0`` and a per-tier output ceiling). They
are provisional because the per-class ``ModelPinMetadata`` rollout will
later supply the real per-class parameters; the golden regenerates when
it does.
"""

from collections.abc import Mapping
from types import MappingProxyType
from typing import Final

from synthorg.budget.model_tier import TIERS, TierName
from synthorg.core.iso_datetime import parse_iso_utc
from synthorg.core.types import NotBlankStr
from synthorg.hr.evaluation.pin_fingerprint import pin_fingerprint
from synthorg.llm.metadata import ModelPinMetadata
from synthorg.llm.model_tier_policy import model_id_for_purpose, tier_for_purpose
from synthorg.llm.prompt_purpose import PromptPurposeId
from synthorg.providers.enums import MessageRole
from synthorg.providers.models import ChatMessage, CompletionConfig

#: Metadata key under which an ``EvalTestCase`` carries its pin payload.
PIN_META_KEY: Final[str] = "pin"

#: Sentinel "never validated by an eval refresh yet" timestamp carried as
#: ``model_version_pinned_at`` by every canonical pin. It is excluded from
#: the drift fingerprint, so the live "last validated" record the
#: validator persists (``ModelPinValidationRow.validated_at``) can advance
#: without changing any fingerprint.
_UNVALIDATED_AT: Final = parse_iso_utc("1970-01-01T00:00:00Z")

#: Canonical deterministic sampling parameters for a system prompt class.
_CANONICAL_TEMPERATURE: Final[float] = 0.0
_CANONICAL_TOP_P: Final[float] = 1.0

#: Per-tier output-token ceiling the canonical pin asserts (powers of two).
_TIER_MAX_TOKENS: Final[Mapping[TierName, int]] = MappingProxyType(
    {
        "small": 1024,
        "medium": 2048,
        "large": 4096,
        "local-small": 1024,
    },
)

# Fail at import (mirroring the policy's own drift guard) if a new canonical
# tier is added to ``TierName`` without a ceiling here, rather than surfacing
# a KeyError on the first ``canonical_pin_for`` call for that tier.
_missing_tier_ceilings = TIERS - set(_TIER_MAX_TOKENS)
if _missing_tier_ceilings:
    msg = f"Tiers missing a max-tokens ceiling: {sorted(_missing_tier_ceilings)}"
    raise ValueError(msg)


def canonical_pin_for(purpose_id: str | PromptPurposeId) -> ModelPinMetadata:
    """Build the canonical pin the benchmark validates for a prompt class.

    Returns:
        A :class:`ModelPinMetadata` pinning the purpose's policy tier and
        the canonical deterministic sampling parameters.
    """
    pid = PromptPurposeId(str(purpose_id))
    tier: TierName = tier_for_purpose(pid)
    return ModelPinMetadata(
        prompt_class_id=pid,
        model=NotBlankStr(model_id_for_purpose(pid)),
        model_version_pinned_at=_UNVALIDATED_AT,
        temperature=_CANONICAL_TEMPERATURE,
        top_p=_CANONICAL_TOP_P,
        max_tokens=_TIER_MAX_TOKENS[tier],
    )


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

    Returns:
        The :class:`ModelPinMetadata` the case carries.

    Raises:
        KeyError: If the metadata carries no pin payload.
    """
    return ModelPinMetadata.model_validate(metadata[PIN_META_KEY])


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
    "canonical_pin_for",
    "fingerprint_for",
    "pin_from_case_metadata",
    "pin_metadata_payload",
    "probe_config",
    "probe_input_data",
    "probe_messages",
]
