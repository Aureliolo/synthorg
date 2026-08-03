"""The standing "ask rather than guess" directive injected into agent prompts.

Tells every agent, at every autonomy level, to put a material and hard-to-reverse
choice to a human instead of picking one. See ``docs/design/org-questions.md``.
"""

from synthorg.engine.ask_policy.adapter import (
    inject_ask_policy_context,
    should_inject_ask_policy,
)
from synthorg.engine.ask_policy.directives import (
    ASK_DIRECTIVE_LOOKUP,
    ASK_DIRECTIVES,
    ASK_DIRECTIVES_MINIMAL,
    ASK_DIRECTIVES_SUMMARY,
    base_directive,
)
from synthorg.engine.ask_policy.models import AskDirective, AskPolicyConfig
from synthorg.engine.ask_policy.provider import (
    AskPolicyProvider,
    SnapshotAskPolicyProvider,
    current_ask_policy_provider,
    set_ask_policy_provider,
)
from synthorg.engine.ask_policy.section import build_ask_policy_section

__all__ = [
    "ASK_DIRECTIVES",
    "ASK_DIRECTIVES_MINIMAL",
    "ASK_DIRECTIVES_SUMMARY",
    "ASK_DIRECTIVE_LOOKUP",
    "AskDirective",
    "AskPolicyConfig",
    "AskPolicyProvider",
    "SnapshotAskPolicyProvider",
    "base_directive",
    "build_ask_policy_section",
    "current_ask_policy_provider",
    "inject_ask_policy_context",
    "set_ask_policy_provider",
    "should_inject_ask_policy",
]
