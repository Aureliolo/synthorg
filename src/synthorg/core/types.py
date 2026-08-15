"""Reusable Pydantic type annotations and validators.

``CurrencyCode`` intentionally lives in ``synthorg.budget.currency``
next to the ISO 4217 allowlist data.  Importing it here would force
``core`` to depend on ``budget``, introducing a circular import via
the many budget modules that already import from ``core.types``.
Consumers who need the validated currency type import it from
``synthorg.budget.currency``.
"""

from collections import Counter
from typing import Annotated, Final, Literal, get_args
from uuid import UUID, uuid5

from pydantic import AfterValidator, BeforeValidator, StringConstraints

CapabilityLevel = Literal["basic", "capable", "expert"]
"""What a model can be trusted with: ``expert`` (hardest work) >
``capable`` > ``basic``. Declared weakest-first, because that order is what
:data:`CAPABILITY_LADDER` reads as the rank.

A claim about capability, never about size or price. The two are only ever
correlated, and grading by the proxy is how a large older model came to
outrank a smaller newer one that benchmarked above it. Locality is a
separate axis with its own signal, so a small model an operator runs
themselves is ``basic`` *and* local, not a fourth rung.

Never order these with ``<`` / ``>``. That the three words happen to sort
alphabetically into rank order is a coincidence of this vocabulary, not a
property of it; go through :func:`capability_rank` or
:func:`capability_meets`."""

#: Weakest-first capability ladder. Index doubles as the rank
#: (``basic`` = 0 < ``capable`` = 1 < ``expert`` = 2). The single source of the
#: ordering, shared by the provider routing resolver and the engine
#: stakes-routing layer so neither re-derives it. Read off the alias rather
#: than restated, so a rung cannot exist in one and not the other.
CAPABILITY_LADDER: Final[tuple[CapabilityLevel, ...]] = get_args(CapabilityLevel)

_CAPABILITY_RANK: Final[dict[CapabilityLevel, int]] = {
    level: idx for idx, level in enumerate(CAPABILITY_LADDER)
}


def capability_rank(level: CapabilityLevel) -> int:
    """Return the weakest-first rank of *level* (basic=0, expert=2)."""
    return _CAPABILITY_RANK[level]


def capability_meets(candidate: CapabilityLevel, required: CapabilityLevel) -> bool:
    """Return whether *candidate* is at least as capable as *required*."""
    return _CAPABILITY_RANK[candidate] >= _CAPABILITY_RANK[required]


def capability_below(level: CapabilityLevel) -> CapabilityLevel | None:
    """Return the rung immediately below *level*.

    Returns:
        The next weaker rung, or ``None`` when *level* is the weakest and
        there is nothing cheaper to descend to.
    """
    idx = _CAPABILITY_RANK[level] - 1
    if idx < 0:
        return None
    return CAPABILITY_LADDER[idx]


AutonomyDetailLevel = Literal["full", "summary", "minimal"]
"""Level of autonomy instruction detail in prompt profiles."""

PersonalityMode = Literal["full", "condensed", "minimal"]
"""Personality section verbosity in prompt profiles."""


def _check_not_whitespace(value: str) -> str:
    """Reject whitespace-only strings.

    Returns:
        The unchanged *value* once it is confirmed non-blank.

    Raises:
        ValueError: If *value* is empty or whitespace-only.
    """
    if not value.strip():
        msg = "must not be whitespace-only"
        raise ValueError(msg)
    return value


NotBlankStr = Annotated[
    str,
    StringConstraints(min_length=1),
    AfterValidator(_check_not_whitespace),
]
"""A string that must be non-empty and not consist solely of whitespace."""


def require_not_blank(value: str, field: str) -> str:
    """Validate a plain string arg is non-blank at runtime.

    A ``NotBlankStr`` annotation only runs inside a Pydantic model, so a
    domain-exception ``__init__`` typed ``execution_id: NotBlankStr`` would
    still accept ``""``. Call this in such constructors to enforce the
    contract and name the offending field.

    Returns:
        The unchanged *value* once confirmed non-blank.

    Raises:
        ValueError: If *value* is empty or whitespace-only.
    """
    if not value.strip():
        msg = f"{field} must not be blank"
        raise ValueError(msg)
    return value


def flatten_label(value: str) -> str:
    """Flatten a semi-trusted label for safe interpolation into a prompt.

    Collapses all whitespace (newlines included) onto one line and drops
    angle brackets so a crafted value cannot forge an untrusted-content
    fence or inject a fresh instruction line into a SYSTEM prompt. Shared
    by the persona renderer, the chief-of-staff router, and the
    ``PersonaLabelStr`` field type so the sanitisation cannot drift
    between sink and source.

    Returns:
        The single-line, angle-bracket-free label.
    """
    collapsed = " ".join(value.split())
    return collapsed.replace("<", "").replace(">", "")


PersonaLabelStr = Annotated[
    str,
    BeforeValidator(flatten_label),
    StringConstraints(min_length=1),
    AfterValidator(_check_not_whitespace),
]
"""A persona-bound label (role / department / name): flattened to a
single line with angle brackets stripped at construction, then required
non-blank. Source-side defence-in-depth for prompt-injection (the render
sites also flatten); a value that is empty after flattening is rejected."""


_AGENT_ID_NAMESPACE: Final = UUID("0b3d2c1e-7a4f-4b8e-9c6d-1f2e3a4b5c6d")
"""Fixed namespace for deriving deterministic agent ids from agent names."""


def stable_agent_id(name: str) -> UUID:
    """Derive a deterministic agent id from an agent *name*.

    The config-sourced agent roster and the runtime registry both derive
    identity from the agent name without coordinating, so a config agent
    and its registered ``AgentIdentity`` resolve to the same id and the
    dashboard can address either by one stable UUID.

    Args:
        name: Agent display name (unique across the company config).

    Returns:
        The deterministic ``uuid5`` agent id for *name*.
    """
    return uuid5(_AGENT_ID_NAMESPACE, name)


def validate_unique_strings(
    values: tuple[str, ...],
    field_name: str,
) -> None:
    """Validate that every string in *values* is unique.

    Raises:
        ValueError: If duplicates are present.
    """
    if len(values) != len(set(values)):
        dupes = sorted(v for v, c in Counter(values).items() if c > 1)
        msg = f"Duplicate entries in {field_name}: {dupes}"
        raise ValueError(msg)
