"""Rendering of the ask-policy prompt section."""

import pytest

from synthorg.engine.ask_policy.models import AskDirective
from synthorg.engine.ask_policy.section import build_ask_policy_section

_BASE = "Ask rather than guess."


@pytest.mark.unit
def test_base_only_renders_verbatim() -> None:
    assert build_ask_policy_section(_BASE, ()) == _BASE


@pytest.mark.unit
def test_extras_render_as_bullets_below_the_base() -> None:
    extras = (
        AskDirective(id="a", text="Ask before a schema change."),
        AskDirective(id="b", text="Ask before a public API break."),
    )
    body = build_ask_policy_section(_BASE, extras)
    assert body == (
        f"{_BASE}\n\n- Ask before a schema change.\n- Ask before a public API break."
    )
