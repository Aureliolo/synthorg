# module-kind: tests
"""The transport artefact that a schema error cannot describe."""

import pytest

from synthorg.engine.decomposition._mangled_arguments import (
    mangled_serialisation_hint,
)

pytestmark = pytest.mark.unit


#: Verbatim from a live run: repeated array elements arrived as XML siblings
#: and were collapsed into nesting instead of into a list.
_OBSERVED: dict[str, object] = {
    "$text": "Plan for the CLI",
    "item": {
        "$text": "...</item>",
        "item": {"$text": "...</item>", "item": {"$text": ""}},
    },
}


@pytest.mark.parametrize(
    "arguments",
    [
        _OBSERVED,
        {"$text": ""},
        {"subtasks": [{"$text": "x"}]},
        {"subtasks": {"nested": {"deeper": {"$text": "x"}}}},
    ],
    ids=["observed", "top-level", "inside-a-list", "deeply-nested"],
)
def test_the_collapse_artefact_is_recognised_wherever_it_sits(
    arguments: dict[str, object],
) -> None:
    """Nothing in this codebase emits the key, so its presence is decisive."""
    hint = mangled_serialisation_hint(arguments)

    assert hint is not None
    assert "JSON array" in hint
    assert "serialisation fault" in hint


@pytest.mark.parametrize(
    "arguments",
    [
        {},
        {"subtasks": [{"id": "s1", "title": "t"}], "task_structure": "sequential"},
        {"subtasks": "not-a-list"},
        {"text": "a field merely named text"},
        {"description": "prose mentioning $text as a literal string"},
        {"subtasks": [{"description": "$text"}]},
        {"subtasks": [{"estimated_hours": 3, "blocking": True, "parent": None}]},
    ],
    ids=[
        "empty",
        "a-real-plan",
        "wrong-type",
        "similar-key",
        "in-prose",
        "as-a-value",
        "scalar-fields",
    ],
)
def test_an_intact_call_is_left_alone(arguments: dict[str, object]) -> None:
    """A KEY, never a value: the artefact is structural, and prose is not it.

    Reading the value position too would make any plan whose text happens to
    quote the token unparseable-looking, and send a model to rewrite a correct
    submission. A number, a boolean and a null are none of the three shapes the
    walk descends into, so it has to answer for them rather than reach the end
    of its own branches with nothing to say.
    """
    assert mangled_serialisation_hint(arguments) is None


def test_a_pathological_chain_is_bounded_rather_than_walked() -> None:
    """The check is cheap on every call, including a hostile one."""
    deep: dict[str, object] = {"$text": "bottom"}
    for _ in range(200):
        deep = {"item": deep}

    assert mangled_serialisation_hint(deep) is None


def test_a_chain_within_the_bound_is_still_caught() -> None:
    """The bound is a floor on cost, not a hole in the check."""
    deep: dict[str, object] = {"$text": "bottom"}
    for _ in range(8):
        deep = {"item": deep}

    assert mangled_serialisation_hint(deep) is not None
