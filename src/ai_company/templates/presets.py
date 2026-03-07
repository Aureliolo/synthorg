"""Minimal personality presets and auto-name generation for templates.

This module provides placeholder presets for M1.  A comprehensive
preset library is planned in a follow-up issue (see GH #80).
"""

import random
from typing import Any

from ai_company.observability import get_logger
from ai_company.observability.events.template import (
    TEMPLATE_PERSONALITY_PRESET_UNKNOWN,
)

logger = get_logger(__name__)

# Preset name -> dict compatible with PersonalityConfig constructor.
PERSONALITY_PRESETS: dict[str, dict[str, Any]] = {
    "visionary_leader": {
        "traits": ("strategic", "decisive", "inspiring"),
        "communication_style": "authoritative",
        "risk_tolerance": "high",
        "creativity": "high",
        "description": ("A visionary leader who sets direction and inspires the team."),
    },
    "pragmatic_builder": {
        "traits": ("practical", "reliable", "detail-oriented"),
        "communication_style": "concise",
        "risk_tolerance": "medium",
        "creativity": "medium",
        "description": ("A pragmatic builder focused on shipping quality code."),
    },
    "eager_learner": {
        "traits": ("curious", "enthusiastic", "adaptable"),
        "communication_style": "collaborative",
        "risk_tolerance": "low",
        "creativity": "medium",
        "description": ("An eager learner who grows quickly and asks good questions."),
    },
    "methodical_analyst": {
        "traits": ("thorough", "systematic", "objective"),
        "communication_style": "formal",
        "risk_tolerance": "low",
        "creativity": "low",
        "description": ("A methodical analyst who values precision and completeness."),
    },
}

# Role-aware auto-generated name pools (gender-neutral names).
_AUTO_NAMES: dict[str, tuple[str, ...]] = {
    "ceo": ("Alex Chen", "Jordan Park", "Morgan Lee", "Taylor Kim"),
    "cto": ("Quinn Zhang", "Sage Patel", "Avery Nakamura", "Reese Torres"),
    "cfo": ("Drew Collins", "Casey Rivera", "Blake Morrison", "Ellis Ward"),
    "coo": ("Rowan Blake", "Finley Cruz", "Emery Santos", "Harper Quinn"),
    "cpo": ("Phoenix Reed", "Kendall Brooks", "Harley Stone", "Lennox Hayes"),
    "full-stack developer": ("Riley Sharma", "Dakota Wei", "Skyler Okafor"),
    "backend developer": ("Cameron Ito", "Hayden Reyes", "Jamie Novak"),
    "frontend developer": ("Kai Jensen", "Noel Andersen", "Sage Hoffman"),
    "product manager": ("Emery Cho", "Phoenix Larsen", "Lennox Dunn"),
    "qa lead": ("Jordan Vega", "Taylor Marsh", "Morgan Frost"),
    "qa engineer": ("Riley Tran", "Avery Grant", "Blake Russell"),
    "devops/sre engineer": ("Quinn Mercer", "Drew Kemp", "Casey Mills"),
    "software architect": ("Sage Holloway", "Rowan Fischer", "Emery Drake"),
    "ux designer": ("Kai Sinclair", "Harper Lane", "Noel Ashford"),
    "ui designer": ("Finley Archer", "Lennox Byrne", "Phoenix Dale"),
    "data analyst": ("Drew Hartley", "Casey Lowe", "Blake Summers"),
    "data engineer": ("Reese Gallagher", "Jordan Holt", "Taylor Crane"),
    "security engineer": ("Quinn Steele", "Morgan Wolfe", "Avery Knox"),
    "content writer": ("Harper Ellis", "Kendall Frost", "Sage Monroe"),
    "_default": ("Agent Alpha", "Agent Beta", "Agent Gamma", "Agent Delta"),
}


def get_personality_preset(name: str) -> dict[str, Any]:
    """Look up a personality preset by name.

    Args:
        name: Preset name (case-insensitive, whitespace-stripped).

    Returns:
        A *copy* of the personality configuration dict.

    Raises:
        KeyError: If the preset name is not found.
    """
    key = name.strip().lower()
    if key not in PERSONALITY_PRESETS:
        available = sorted(PERSONALITY_PRESETS)
        msg = f"Unknown personality preset {name!r}. Available: {available}"
        logger.warning(
            TEMPLATE_PERSONALITY_PRESET_UNKNOWN,
            preset_name=name,
            available=available,
        )
        raise KeyError(msg)
    return dict(PERSONALITY_PRESETS[key])


def generate_auto_name(role: str, *, seed: int | None = None) -> str:
    """Generate a contextual agent name based on role.

    Uses a deterministic PRNG when *seed* is provided, ensuring
    reproducible name generation across runs.

    Args:
        role: The agent's role name.
        seed: Optional random seed for deterministic naming.

    Returns:
        A generated agent name string.
    """
    key = role.strip().lower()
    pool = _AUTO_NAMES.get(key, _AUTO_NAMES["_default"])
    rng = random.Random(seed)  # noqa: S311
    return rng.choice(pool)
