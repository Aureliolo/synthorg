# module-kind: code
"""Rendering of the ask-policy prompt section body.

The standing directive is prose, so it renders verbatim; operator additions
render as bullets below it, which keeps the org-specific rules visibly separate
from the standing one. Org-authored text is not fenced: it is organisation
policy the agent is meant to follow, not untrusted data.
"""

from synthorg.engine.ask_policy.models import AskDirective


def build_ask_policy_section(base: str, extra: tuple[AskDirective, ...]) -> str:
    """Render the section body from the standing directive plus any extras.

    Args:
        base: The standing directive for this agent's autonomy level and tier.
        extra: In-scope operator-authored directives, in provider order.

    Returns:
        The standing directive alone, or it followed by one bullet per extra.
    """
    if not extra:
        return base
    bullets = "\n".join(f"- {directive.text}" for directive in extra)
    return f"{base}\n\n{bullets}"


__all__ = ["build_ask_policy_section"]
