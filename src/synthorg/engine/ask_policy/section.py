# module-kind: code
"""Rendering of the ask-policy prompt section body.

The standing directive is shipped prose, so it renders verbatim. Operator
additions render as fenced bullets below it, which keeps the org-specific rules
visibly separate from the standing one.

Operator additions are fenced with ``TAG_CONFIG_VALUE``, exactly as the sibling
``org_policies`` block is. Fencing does not stop an agent applying a policy as a
policy (that block is proof), but it does stop a directive smuggling
instructions of its own, and "operator-authored" is an assumption the code
cannot enforce: the settings key is writable through the admin MCP surface, so
text that entered as fenced untrusted data could otherwise be laundered into
unfenced system-prompt text for every agent in the organisation.
"""

from synthorg.engine.ask_policy.models import AskDirective
from synthorg.engine.prompt_safety import TAG_CONFIG_VALUE, wrap_untrusted


def build_ask_policy_section(base: str, extra: tuple[AskDirective, ...]) -> str:
    """Render the section body from the standing directive plus any extras.

    Args:
        base: The standing directive for this agent's autonomy level and tier.
        extra: In-scope operator-authored directives, in provider order.

    Returns:
        The standing directive alone, or it followed by one fenced bullet per
        extra.
    """
    if not extra:
        return base
    bullets = "\n".join(
        f"- {wrap_untrusted(TAG_CONFIG_VALUE, directive.text)}" for directive in extra
    )
    return f"{base}\n\n{bullets}"


__all__ = ["build_ask_policy_section"]
