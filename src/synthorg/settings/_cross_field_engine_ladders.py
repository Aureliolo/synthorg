# module-kind: code
"""The two ``engine`` ladders, each one scale spread over four settings.

The capability floors and the reasoning efforts are written a key at a time,
and every rung is a legal value on its own, so nothing but the four together
says whether the ladder still rises with stakes.

The policy refuses an inverted ladder when it reads it, which is after the
write has been accepted. From then on the dashboard reports a ladder the loop
is not enforcing, every later write to any of the eight re-fails the same
rebuild (so a genuine correction during an incident binds nothing either), and
the next boot cannot build the policy at all, which takes the runtime with it.
Refusing at the write is what puts the error in front of the operator who
caused it.
"""

from collections.abc import Awaitable, Callable, Mapping
from itertools import pairwise
from typing import Final

from synthorg.core.completion_enums import ReasoningEffort, reasoning_effort_rank
from synthorg.core.types import CAPABILITY_LADDER
from synthorg.settings._cross_field_shared import effective_raw, reject

ENGINE_NS: Final[str] = "engine"
_CAPABILITY_FLOOR_KEYS: Final[tuple[str, ...]] = (
    "capability_floor_low",
    "capability_floor_normal",
    "capability_floor_high",
    "capability_floor_critical",
)
_REASONING_EFFORT_KEYS: Final[tuple[str, ...]] = (
    "reasoning_effort_low",
    "reasoning_effort_normal",
    "reasoning_effort_high",
    "reasoning_effort_critical",
)
ENGINE_LADDER_KEYS: Final[frozenset[str]] = frozenset(
    _CAPABILITY_FLOOR_KEYS + _REASONING_EFFORT_KEYS
)
#: Reasoning effort left unset, which ranks below every configured effort.
_REASONING_UNSET: Final[str] = "none"


async def enforce_engine_ladders(
    written: Mapping[tuple[str, str], str],
    get_current: Callable[[str, str], Awaitable[str | None]],
    get_default: Callable[[str, str], str | None],
) -> None:
    """Reject a capability or reasoning ladder that stops rising.

    Args:
        written: The batch about to be written.
        get_current: Resolves the value in force.
        get_default: Resolves the registered default for an unset key.

    Raises:
        SettingValidationError: When either ladder inverts.
    """
    await _enforce_ladder_rises(
        written,
        get_current,
        get_default,
        keys=_CAPABILITY_FLOOR_KEYS,
        rank_of=_capability_ladder_rank,
        scale="capability floor",
        remedy=(
            "Move the floors in one write, or raise the higher-stakes floor to match."
        ),
    )
    await _enforce_ladder_rises(
        written,
        get_current,
        get_default,
        keys=_REASONING_EFFORT_KEYS,
        rank_of=_reasoning_ladder_rank,
        scale="reasoning effort",
        remedy=(
            "Move the efforts in one write, or raise the higher-stakes effort to match."
        ),
    )


async def _enforce_ladder_rises(
    written: Mapping[tuple[str, str], str],
    get_current: Callable[[str, str], Awaitable[str | None]],
    get_default: Callable[[str, str], str | None],
    *,
    keys: tuple[str, ...],
    rank_of: Callable[[str], int | None],
    scale: str,
    remedy: str,
) -> None:
    """Reject *keys* whose post-write ranks do not rise with stakes.

    Args:
        written: The batch about to be written.
        get_current: Resolves the value in force.
        get_default: Resolves the registered default for an unset key.
        keys: The ladder's keys, weakest stakes first.
        rank_of: Reads a raw value's rank, or ``None`` when it names none.
        scale: What the ladder measures, for the operator-facing message.
        remedy: What the operator can do instead.

    Raises:
        SettingValidationError: When a rung sits below the one before it.
    """
    ranked: list[tuple[str, str, int]] = []
    for key in keys:
        raw = await effective_raw(written, get_current, get_default, (ENGINE_NS, key))
        if raw is None:
            return
        rank = rank_of(raw)
        if rank is None:
            # A malformed value is rejected by the per-field type validator,
            # so it is not this rule's job to report it a second time.
            return
        ranked.append((key, raw, rank))

    for (lower_key, lower_raw, lower_rank), (key, raw, rank) in pairwise(ranked):
        if rank >= lower_rank:
            continue
        msg = (
            f"{ENGINE_NS}.{key} of {raw!r} sits below"
            f" {ENGINE_NS}.{lower_key} of {lower_raw!r}: the {scale} ladder"
            " must rise with stakes, or more consequential work would be held"
            " to a lower bar than less consequential work. The policy refuses"
            " an inverted ladder when it reads it, which is after this write"
            f" has been accepted. {remedy}"
        )
        reject(key, msg, reason=f"{scale} ladder inverts", namespace=ENGINE_NS)


def _capability_ladder_rank(value: str) -> int | None:
    """Return *value*'s capability rank, or ``None`` when it names no rung.

    Read off the shared ladder, whose index is the rank, so this cannot order
    the rungs differently from the selection that acts on them.

    Returns:
        The rank, or ``None``.
    """
    for rank, level in enumerate(CAPABILITY_LADDER):
        if level == value:
            return rank
    return None


def _reasoning_ladder_rank(value: str) -> int | None:
    """Return *value*'s reasoning rank, with unset ranking below every effort.

    Returns:
        The rank, or ``None`` when the value names no effort.
    """
    if value == _REASONING_UNSET:
        return -1
    try:
        return reasoning_effort_rank(ReasoningEffort(value))
    except ValueError:
        return None


__all__ = ["ENGINE_LADDER_KEYS", "ENGINE_NS", "enforce_engine_ladders"]
