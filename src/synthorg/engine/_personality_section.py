"""Single source for the system-prompt personality section.

The personality block is needed in two places: the Jinja2 system-prompt
template renders it for the agent, and the token estimator measures it to
decide progressive trimming. Keeping both on this one builder means the
rendered section and the estimated section cannot drift, so a trim
decision always reflects what the agent actually sees.

The builder returns the body lines only (no ``## Personality`` heading);
the template keeps the heading literal and injects the joined body, while
the estimator prepends the heading before measuring.
"""

from typing import cast

from synthorg.core.types import PersonalityMode


def build_personality_section(
    ctx: dict[str, object],
    personality_mode: PersonalityMode,
) -> list[str]:
    """Build the personality section body lines for *personality_mode*.

    ``"full"`` emits the description plus every behavioural field;
    ``"condensed"`` emits the description, communication style, and
    traits; ``"minimal"`` emits the communication style alone.

    Args:
        ctx: Template context dict with personality fields populated.
        personality_mode: Which rendering mode to build for.

    Returns:
        The markdown body lines, excluding the ``## Personality`` heading.
    """
    desc = cast("str", ctx.get("personality_description", ""))
    style = ctx.get("communication_style", "")
    lines: list[str] = []

    if personality_mode == "full":
        if desc:
            lines.append(desc)
        lines.append(f"- **Communication style**: {style}")
        lines.append(f"- **Verbosity**: {ctx.get('verbosity', '')}")
        lines.append(f"- **Risk tolerance**: {ctx.get('risk_tolerance', '')}")
        lines.append(f"- **Creativity**: {ctx.get('creativity', '')}")
        lines.append(f"- **Decision-making**: {ctx.get('decision_making', '')}")
        lines.append(f"- **Collaboration preference**: {ctx.get('collaboration', '')}")
        lines.append(f"- **Conflict approach**: {ctx.get('conflict_approach', '')}")
        traits = cast("tuple[str, ...]", ctx.get("personality_traits", ()))
        if traits:
            lines.append(f"- **Traits**: {', '.join(traits)}")
    elif personality_mode == "condensed":
        if desc:
            lines.append(desc)
        lines.append(f"- **Style**: {style}")
        traits = cast("tuple[str, ...]", ctx.get("personality_traits", ()))
        if traits:
            lines.append(f"- **Traits**: {', '.join(traits)}")
    else:
        lines.append(f"- **Style**: {style}")

    return lines
