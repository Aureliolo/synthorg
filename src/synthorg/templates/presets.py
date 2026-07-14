"""Personality presets and auto-name generation for templates.

Provides comprehensive personality presets with Big Five dimensions
and behavioral enums, plus internationally diverse auto-name generation
backed by the Faker library.
"""

import functools
from collections.abc import Callable, Mapping
from types import MappingProxyType
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    # Faker stays import-free at runtime: the library is heavy and the
    # auto-name path imports it lazily inside the generator function.
    from faker import Faker

from pydantic import JsonValue, ValidationError

from synthorg.core.agent import PersonalityConfig
from synthorg.core.authority import role_depth
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.normalization import normalize_ascii_lowercase
from synthorg.hr.strategy_mode import StrategicOutputMode
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.template import (
    TEMPLATE_PERSONALITY_PRESET_INVALID,
    TEMPLATE_PERSONALITY_PRESET_UNKNOWN,
)
from synthorg.templates._preset_data import RAW_PRESETS, PresetValue
from synthorg.templates.schema import CompanyTemplate

logger = get_logger(__name__)

# Both the outer mapping and each inner mapping are read-only.  Each
# inner dict is copied (``dict(v)``) so the frozen view is independent of
# the mutable source ``RAW_PRESETS``; a later mutation of the source dict
# cannot leak through these proxies.
PERSONALITY_PRESETS: MappingProxyType[str, MappingProxyType[str, PresetValue]] = (
    MappingProxyType({k: MappingProxyType(dict(v)) for k, v in RAW_PRESETS.items()})
)


def get_personality_preset(
    name: str,
    custom_presets: Mapping[str, dict[str, JsonValue]] | None = None,
) -> dict[str, JsonValue]:
    """Look up a personality preset by name.

    Custom presets take precedence over builtins; the result is a fresh
    one-level copy with any tuple-valued field normalised to a JSON list.

    Args:
        name: Preset name (case-insensitive, whitespace-stripped).
        custom_presets: Optional mapping of custom preset names to
            personality config dicts.  Keys must be lowercased.

    Returns:
        A fresh JSON-shaped copy of the personality configuration dict.

    Raises:
        KeyError: If the preset name is not found in either source.
    """
    key = normalize_ascii_lowercase(name)
    source: Mapping[str, PresetValue | JsonValue] | None = None
    if custom_presets is not None and key in custom_presets:
        source = custom_presets[key]
    elif key in PERSONALITY_PRESETS:
        source = PERSONALITY_PRESETS[key]
    if source is not None:
        return {
            field: list(value) if isinstance(value, (list, tuple)) else value
            for field, value in source.items()
        }
    available = sorted(PERSONALITY_PRESETS)
    if custom_presets:
        available = sorted({*available, *custom_presets})
    msg = f"Unknown personality preset {name!r}. Available: {available}"
    logger.warning(
        TEMPLATE_PERSONALITY_PRESET_UNKNOWN,
        preset_name=name,
        available=available,
    )
    raise KeyError(msg)


# Validate all presets at import time to catch key typos immediately.
def _validate_presets() -> None:
    for name, preset in PERSONALITY_PRESETS.items():
        try:
            PersonalityConfig.model_validate(dict(preset))
        except (ValidationError, TypeError) as exc:
            logger.warning(
                TEMPLATE_PERSONALITY_PRESET_INVALID,
                preset_name=name,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            msg = f"Invalid personality preset {name!r}: {safe_error_description(exc)}"
            raise ValueError(msg) from exc


_validate_presets()
del _validate_presets


# ── Strategic output mode defaults by seniority ────────────────

# Executive tier by reporting depth (CEO at depth 0, the C-suite at
# depth 1) defaults to an advisory strategic-output posture. See
# docs/design/strategy.md "Strategic Output Modes".
_STRATEGIC_OUTPUT_MAX_DEPTH: Final[int] = 1


def get_strategic_output_default(
    role: str,
) -> StrategicOutputMode | None:
    """Return the default strategic output mode for a role.

    Args:
        role: Agent role name.

    Returns:
        ``ADVISOR`` for executive-tier roles (shallow reporting depth);
        ``None`` for roles with no strategic default (strategic output
        is not applicable).
    """
    if role_depth(role) <= _STRATEGIC_OUTPUT_MAX_DEPTH:
        return StrategicOutputMode.ADVISOR
    return None


def validate_preset_references(
    template: CompanyTemplate,
    custom_presets: Mapping[str, dict[str, JsonValue]] | None = None,
) -> tuple[str, ...]:
    """Check all agent personality_preset references against known presets.

    Returns a tuple of warning messages for unknown presets.  Does not
    raise -- purely advisory for pre-flight validation and template
    import/export scenarios.

    Args:
        template: Parsed template to validate.
        custom_presets: Optional custom preset mapping.  Keys must
            be lowercased.

    Returns:
        Tuple of warning strings (empty when all presets are known).
    """
    issues: list[str] = []
    for agent_cfg in template.agents:
        preset = agent_cfg.personality_preset
        if preset is None:
            continue
        key = normalize_ascii_lowercase(preset)
        if custom_presets is not None and key in custom_presets:
            continue
        if key in PERSONALITY_PRESETS:
            continue
        issues.append(
            f"Agent {agent_cfg.role!r} references unknown personality preset {preset!r}"
        )
    return tuple(issues)


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
            fake = _get_faker(tuple(locale_list))
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
