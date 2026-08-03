"""Ask-policy event constants."""

from typing import Final

ASK_POLICY_CONFIG_VALIDATED: Final[str] = "ask_policy.config.validated"
# One malformed operator-authored directive in the JSON setting. The entry is
# dropped and the rest are kept, so the count in the rebound event is the
# honest one.
ASK_POLICY_DIRECTIVES_INVALID: Final[str] = "ask_policy.directives.invalid"
# The settings read itself failed (backend unreachable, timeout). Distinct from
# a malformed payload: this one says nothing about what the operator wrote, and
# the recovery is to keep whatever is already bound rather than to re-derive.
ASK_POLICY_SETTINGS_READ_FAILED: Final[str] = "ask_policy.settings.read_failed"
ASK_POLICY_PROVIDER_REBOUND: Final[str] = "ask_policy.provider.rebound"
ASK_POLICY_PROVIDER_RETAINED: Final[str] = "ask_policy.provider.retained"
ASK_POLICY_PROMPT_INJECTED: Final[str] = "ask_policy.prompt.injected"
