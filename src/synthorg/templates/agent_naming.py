"""Auto-name generation for template agents.

Internationally diverse names backed by the Faker library, so a rendered
company reads as people rather than as ``Agent 1`` ... ``Agent N``.
"""

import functools
from collections.abc import Callable
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    # Faker stays import-free at runtime: the library is heavy and the
    # auto-name path imports it lazily inside the generator function.
    from faker import Faker

from synthorg.core.critical_errors import reraise_critical
from synthorg.observability import get_logger, safe_error_description

logger = get_logger(__name__)


# Agents display as a clean "First Last"; cap each part so a long compound
# locale surname (e.g. "O Huaillearan-O Maoilin") cannot overflow the UI.
_MAX_NAME_PART_LEN: Final[int] = 14
_NAME_PART_ATTEMPTS: Final[int] = 6


def _short_name_part(generate: Callable[[], str]) -> str:
    """Pick a single-token name part within the length cap.

    ``Faker.first_name`` / ``last_name`` can return compound, hyphenated, or
    overlong values for some locales. Retry a few times for a clean single
    token within ``_MAX_NAME_PART_LEN``, then fall back to the leading token
    truncated so a name is always produced.

    Args:
        generate: A Faker part generator (``first_name`` / ``last_name``).

    Returns:
        A single-token name part, capped in length.
    """
    fallback = ""
    for _ in range(_NAME_PART_ATTEMPTS):
        tokens = generate().strip().split()
        token = tokens[0] if tokens else ""
        if token and "-" not in token and len(token) <= _MAX_NAME_PART_LEN:
            return token
        if not fallback and token:
            fallback = token[:_MAX_NAME_PART_LEN]
    return fallback or "Agent"


def _two_part_name(first: Callable[[], str], last: Callable[[], str]) -> str:
    """Compose a clean ``First Last`` name from Faker part generators.

    Returns:
        A two-token ``First Last`` string, each part capped in length.
    """
    return f"{_short_name_part(first)} {_short_name_part(last)}"


def generate_auto_name(
    role: str,  # noqa: ARG001
    *,
    seed: int | None = None,
    locales: list[str] | None = None,
) -> str:
    """Generate an internationally diverse agent name using Faker.

    With *seed*, a fresh single-locale Faker instance is used so the
    shared cached instance is never mutated.  *role* is accepted for
    positional-caller compatibility but does not influence the name;
    *locales* defaults to all Latin-script locales when None or empty.

    Returns:
        A generated full name string.
    """
    import random  # noqa: PLC0415

    from faker import Faker  # noqa: PLC0415

    from synthorg.templates.locales import ALL_LATIN_LOCALES  # noqa: PLC0415

    locale_list = locales or list(ALL_LATIN_LOCALES)
    try:
        if seed is not None:
            rng = random.Random(seed)  # noqa: S311
            chosen_locale = rng.choice(locale_list)
            # Fresh instance -- never mutate the shared cached one.
            fake = Faker([chosen_locale])
            fake.seed_instance(seed)
        else:
            # Draw one locale rather than constructing a Faker over all of
            # them: a multi-locale Faker eagerly loads every provider for
            # every locale (seconds for the full Latin set) only to serve
            # one name, and the per-locale instance is cached so the next
            # call for the same locale is free. Diversity is preserved --
            # the locale itself is sampled from the full set per call.
            chosen_locale = random.choice(locale_list)  # noqa: S311
            fake = _get_faker((chosen_locale,))
        return _two_part_name(fake.first_name, fake.last_name)
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        reraise_critical(exc)
        from synthorg.observability.events.template import (  # noqa: PLC0415
            TEMPLATE_NAME_GEN_FAKER_ERROR,
        )

        logger.warning(
            TEMPLATE_NAME_GEN_FAKER_ERROR,
            locales=locale_list[:5],
            seed=seed,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        # Fall back to a known-safe locale.
        fallback = Faker(["en_US"])
        if seed is not None:
            fallback.seed_instance(seed)
        return _two_part_name(fallback.first_name, fallback.last_name)


@functools.lru_cache(maxsize=128)
def _get_faker(locale_tuple: tuple[str, ...]) -> Faker:
    """Return a cached Faker instance for the given locale tuple.

    Caching avoids re-initialising locale providers on every call.
    The cache is keyed by locale tuple (immutable, hashable).

    Only used for the **unseeded** path; seeded callers must create
    a fresh instance to avoid mutating shared state.
    """
    from faker import Faker  # noqa: PLC0415

    return Faker(list(locale_tuple))
