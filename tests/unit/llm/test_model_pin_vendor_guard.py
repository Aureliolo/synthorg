"""Guard: model pins record a design tier, never a vendor model name.

The provider-agnostic design requires every pin's ``model`` to be an
``example-{tier}-001`` archetype id, not a real vendor model. ``test_model_pins``
checks that ``pin_for`` returns a valid :class:`ModelPinMetadata`; this guard adds
the explicit negative: no pin's model matches a vendor token, and every model is a
design-tier archetype. It ratchets against a vendor name leaking into a pin.
"""

import re
from typing import Final

import pytest

from synthorg.llm.model_pins import pin_for
from synthorg.llm.prompt_purpose import PromptPurposeId

pytestmark = pytest.mark.unit

# A pin model id is the design tier the purpose maps to, per
# ``model_tier_policy.model_id_for_purpose`` -> ``example-{tier}-001``.
_ARCHETYPE_RE: Final[re.Pattern[str]] = re.compile(
    r"^example-(basic|capable|expert)-001$"
)

# Substrings that betray a real vendor model name. Lower-cased before the
# membership test. Not exhaustive, but covers the major families a careless
# pin edit would reach for.
_VENDOR_TOKENS: Final[tuple[str, ...]] = (
    "gpt",
    "openai",
    "claude",
    "anthropic",
    "sonnet",
    "opus",
    "haiku",
    "gemini",
    "llama",
    "mistral",
    "mixtral",
    "cohere",
    "command",
    "grok",
    "deepseek",
    "qwen",
)


@pytest.mark.parametrize("purpose", list(PromptPurposeId))
def test_pin_model_is_design_tier_archetype(purpose: PromptPurposeId) -> None:
    model = pin_for(purpose).model
    assert _ARCHETYPE_RE.match(model), (
        f"{purpose.value}: pin model {model!r} is not a design-tier archetype"
    )


@pytest.mark.parametrize("purpose", list(PromptPurposeId))
def test_pin_model_has_no_vendor_token(purpose: PromptPurposeId) -> None:
    model = pin_for(purpose).model.lower()
    for token in _VENDOR_TOKENS:
        assert token not in model, (
            f"{purpose.value}: pin model {model!r} contains vendor token {token!r}"
        )
