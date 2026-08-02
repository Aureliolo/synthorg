# module-kind: code
"""Bind the ambient prompt layers a harness-run engine would otherwise miss.

A benchmark run constructs :class:`AgentEngine` directly, with no API lifespan
and no settings service, so nothing calls the boot hooks that bind the
process-global prompt providers. For most layers that costs nothing: an unbound
house-style or active-principle provider renders exactly what the shipped
default renders, which is no section.

The ask policy is the exception. Its shipped default is ON and carries the
standing "ask rather than guess" directive, so leaving it unbound scores a
prompt the product never sends: a benchmark measuring agents that were never
told to ask cannot say anything about agents that were.
"""

from synthorg.engine.ask_policy.models import AskPolicyConfig
from synthorg.engine.ask_policy.wiring import bind_ask_policy_config


def bind_default_prompt_layers() -> None:
    """Bind the shipped defaults for every ambient layer that has one.

    Idempotent, and safe to call more than once per process: each bind
    replaces the previous provider with an equivalent snapshot.
    """
    bind_ask_policy_config(AskPolicyConfig())


__all__ = ["bind_default_prompt_layers"]
