# module-kind: code
"""Rendering of the soft house-style prompt section.

Renders the scope-merged directives into the body of the ``## House Writing
Style`` section injected into an agent's system prompt. Directives are
org-authored (from the active pack), so they are rendered verbatim, not fenced
as untrusted content.
"""

from synthorg.engine.output_style.models import HouseStyleDirective


def build_house_style_section(directives: tuple[HouseStyleDirective, ...]) -> str:
    """Render the house-style directives into the section body.

    Args:
        directives: The in-scope directives for an agent (org + role + dept).

    Returns:
        A bullet list of directive texts, or an empty string when there are
        no directives.
    """
    if not directives:
        return ""
    return "\n".join(f"- {directive.text}" for directive in directives)


__all__ = ["build_house_style_section"]
