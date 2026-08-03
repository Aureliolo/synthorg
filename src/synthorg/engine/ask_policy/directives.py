# module-kind: declarative
"""The standing "ask rather than guess" directive, per autonomy level and tier.

One text per :class:`AutonomyLevel`, at each of the three verbosity tiers a
prompt profile selects, mirroring how ``AUTONOMY_INSTRUCTIONS`` is tiered so the
two sections of the prompt always speak at the same level of detail.

The matrix is declared in code rather than loaded from a pack file because it
must be **total**: a missing cell is an autonomy level at which the organisation
silently stops asking. The import-time guards below make that a startup failure
rather than a quiet gap discovered in production. Operator-authored additions
ride on top through ``engine.ask_policy_extra_directives``.

The text never names a tool. Tool definitions reach the model through the
provider's ``tools`` parameter (the template's non-inferable principle), and
naming a tool an operator has gated off would teach the model to hallucinate a
call.
"""

from types import MappingProxyType
from typing import Final, get_args

from synthorg.core.autonomy_enums import AutonomyLevel
from synthorg.core.types import AutonomyDetailLevel

# ── Full tier ────────────────────────────────────────────────────

ASK_DIRECTIVES: Final[MappingProxyType[AutonomyLevel, str]] = MappingProxyType(
    {
        AutonomyLevel.FULL: (
            "Autonomy is not a licence to guess. Before a choice that is "
            "material and hard to reverse, stop and ask a human rather than "
            "picking for them. Material means it moves cost, scope, public "
            "behaviour, data, or someone else's work. Hard to reverse means "
            "undoing it costs real rework, not a quick edit. Everything else "
            "stays yours: decide it, state the assumption you made, and keep "
            "going."
        ),
        AutonomyLevel.SEMI: (
            "Work independently, and ask rather than guess when a choice is "
            "material and hard to reverse. A fork with real alternatives goes "
            "to a human with the options and their tradeoffs laid out; an "
            "ambiguous requirement goes back as a question. Routine, easily "
            "reversible calls are yours: make them, record the assumption, "
            "and keep going."
        ),
        AutonomyLevel.SUPERVISED: (
            "You propose and await approval, so the standing rule is close to "
            "your default already: ask rather than guess whenever a choice is "
            "material and hard to reverse. Fold the question into the plan you "
            "put up when you can, and raise it the moment a fork appears "
            "mid-step rather than picking one and reporting it afterwards."
        ),
        AutonomyLevel.LOCKED: (
            "You take no autonomous action, so a material, hard-to-reverse "
            "choice is never yours to settle quietly: ask rather than guess, "
            "every time. Surface the fork with its alternatives while it is "
            "still open, rather than noting it and waiting to be asked."
        ),
    }
)

_missing_full = set(AutonomyLevel) - set(ASK_DIRECTIVES)
if _missing_full:
    _names = sorted(level.value for level in _missing_full)
    _msg = f"Missing ask directive for: {_names}"
    raise ValueError(_msg)

# ── Summary tier (one sentence per level) ────────────────────────

ASK_DIRECTIVES_SUMMARY: Final[MappingProxyType[AutonomyLevel, str]] = MappingProxyType(
    {
        AutonomyLevel.FULL: (
            "Decide freely, but ask rather than guess when a choice is "
            "material and hard to reverse."
        ),
        AutonomyLevel.SEMI: (
            "Ask rather than guess when a choice is material and hard to "
            "reverse; decide the rest yourself."
        ),
        AutonomyLevel.SUPERVISED: (
            "Ask rather than guess on any material, hard-to-reverse choice; "
            "raise it in the plan or mid-step."
        ),
        AutonomyLevel.LOCKED: (
            "Always ask a human about a material, hard-to-reverse choice; "
            "never guess one."
        ),
    }
)

_missing_summary = set(AutonomyLevel) - set(ASK_DIRECTIVES_SUMMARY)
if _missing_summary:
    _names_s = sorted(level.value for level in _missing_summary)
    _msg_s = f"Missing ask summary for: {_names_s}"
    raise ValueError(_msg_s)

# ── Minimal tier (single phrase per level) ───────────────────────

ASK_DIRECTIVES_MINIMAL: Final[MappingProxyType[AutonomyLevel, str]] = MappingProxyType(
    {
        AutonomyLevel.FULL: "Ask, do not guess, on material irreversible choices.",
        AutonomyLevel.SEMI: "Ask on material irreversible choices; decide the rest.",
        AutonomyLevel.SUPERVISED: "Ask, never guess, a material irreversible choice.",
        AutonomyLevel.LOCKED: "Always ask on a material irreversible choice.",
    }
)

_missing_minimal = set(AutonomyLevel) - set(ASK_DIRECTIVES_MINIMAL)
if _missing_minimal:
    _names_m = sorted(level.value for level in _missing_minimal)
    _msg_m = f"Missing ask minimal for: {_names_m}"
    raise ValueError(_msg_m)

# ── Tier lookup ──────────────────────────────────────────────────

ASK_DIRECTIVE_LOOKUP: Final[
    MappingProxyType[AutonomyDetailLevel, MappingProxyType[AutonomyLevel, str]]
] = MappingProxyType(
    {
        "full": ASK_DIRECTIVES,
        "summary": ASK_DIRECTIVES_SUMMARY,
        "minimal": ASK_DIRECTIVES_MINIMAL,
    },
)

_missing_detail = set(get_args(AutonomyDetailLevel)) - set(ASK_DIRECTIVE_LOOKUP)
if _missing_detail:
    _msg_d = f"Missing ask directives for detail levels: {sorted(_missing_detail)}"
    raise ValueError(_msg_d)


def base_directive(*, autonomy: AutonomyLevel, detail: AutonomyDetailLevel) -> str:
    """Return the standing directive for an autonomy level at a verbosity tier.

    Total by construction: an unmapped pair raises ``KeyError`` at the call site
    rather than yielding a silently absent directive.

    Args:
        autonomy: The autonomy level the run resolved to.
        detail: The verbosity tier the prompt profile selected.

    Returns:
        The directive text.
    """
    return ASK_DIRECTIVE_LOOKUP[detail][autonomy]


__all__ = [
    "ASK_DIRECTIVES",
    "ASK_DIRECTIVES_MINIMAL",
    "ASK_DIRECTIVES_SUMMARY",
    "ASK_DIRECTIVE_LOOKUP",
    "base_directive",
]
