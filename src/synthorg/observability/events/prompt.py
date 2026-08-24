"""Prompt event constants."""

from typing import Final

PROMPT_BUILD_START: Final[str] = "prompt.build.start"
PROMPT_BUILD_SUCCESS: Final[str] = "prompt.build.success"
PROMPT_BUILD_TOKEN_TRIMMED: Final[str] = "prompt.build.token_trimmed"  # noqa: S105 -- event name, not a credential
PROMPT_BUILD_ERROR: Final[str] = "prompt.build.error"
PROMPT_BUILD_BUDGET_EXCEEDED: Final[str] = "prompt.build.budget_exceeded"
PROMPT_CUSTOM_TEMPLATE_LOADED: Final[str] = "prompt.custom_template.loaded"
PROMPT_CUSTOM_TEMPLATE_FAILED: Final[str] = "prompt.custom_template.failed"
PROMPT_POLICY_VALIDATION_START: Final[str] = "prompt.policy.validation_start"
PROMPT_POLICY_QUALITY_ISSUE: Final[str] = "prompt.policy.quality_issue"
PROMPT_POLICY_VALIDATION_FAILED: Final[str] = "prompt.policy.validation_failed"
PROMPT_TOKEN_RATIO_HIGH: Final[str] = "prompt.token_ratio.high"  # noqa: S105 -- event name, not a credential
PROMPT_PROFILE_SELECTED: Final[str] = "prompt.profile.selected"
PROMPT_PROFILE_DEFAULT: Final[str] = "prompt.profile.default"
