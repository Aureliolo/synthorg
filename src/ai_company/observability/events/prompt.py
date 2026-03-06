"""Prompt construction event constants."""

from typing import Final

PROMPT_BUILD_START: Final[str] = "prompt.build.start"
PROMPT_BUILD_SUCCESS: Final[str] = "prompt.build.success"
PROMPT_BUILD_TOKEN_TRIMMED: Final[str] = "prompt.build.token_trimmed"  # noqa: S105 — event name, not a credential
PROMPT_BUILD_ERROR: Final[str] = "prompt.build.error"
PROMPT_BUILD_BUDGET_EXCEEDED: Final[str] = "prompt.build.budget_exceeded"
PROMPT_CUSTOM_TEMPLATE_LOADED: Final[str] = "prompt.custom_template.loaded"
PROMPT_CUSTOM_TEMPLATE_FAILED: Final[str] = "prompt.custom_template.failed"
