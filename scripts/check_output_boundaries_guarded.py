#!/usr/bin/env python3
"""Reachability lock: every agent-output boundary routes through the guard.

The output-style policy only enforces if each agent-output boundary calls the
deterministic guard before the output escapes. This gate parses each known
boundary module and asserts it still CALLS one of the guard entry points that
satisfy it (an AST call check, not a substring match, so a stray comment or
dead import cannot satisfy the lock). A refactor that silently drops a guard at
a boundary fails CI rather than shipping an unenforced path. Complements the
anti-ghost manifest, which locks that the service is bound at boot.

A boundary declares its KIND as well as its guards, because "calls the policy"
stopped being one property the day one boundary became an observation:

* ``ENFORCING`` boundaries refuse. Every tool an agent writes or sends through
  is one, and the refusal is its own tool result, so the agent reworks on its
  next turn inside the same session.
* ``OBSERVING`` boundaries report and decide nothing. There is exactly one, the
  post-session completion backstop: the session has ended by then, so its only
  correction would be a whole re-dispatch, and a style violation must never
  destroy work whose substance a peer reviewer approved. It is additionally
  checked for the ABSENCE of ``enforce_output_policy``, the raising door,
  because regaining it is precisely the shipped defect this shape replaced and
  nothing else would notice.

A boundary's guard set is ANY-OF. ``enforce_output_policy`` and
``evaluate_output_policy`` are two doors onto one policy, not two obligations,
so requiring both would fail every boundary in the tree; see the note on
``_BOUNDARIES``.

Exit codes:

* 0: every boundary still calls its guard entry point
* 1: a boundary lost its guard, or an observing one gained the raising door
* 2: a boundary file is missing, unreadable, or unparseable (fail-closed)

See CLAUDE.md "Output-Style Policy (MANDATORY)".
"""

import ast
import sys
from pathlib import Path
from typing import Final, NamedTuple

_REPO_ROOT: Final[Path] = Path(__file__).resolve().parent.parent

_ENFORCING: Final[str] = "enforcing"
_OBSERVING: Final[str] = "observing"

#: The raising door. An observing boundary calling this has become a deciding
#: one, which is the regression the kind split exists to catch.
_RAISING_GUARD: Final[str] = "enforce_output_policy"


class _Boundary(NamedTuple):
    """One agent-output boundary: what it is, what it does, what satisfies it.

    Attributes:
        label: Human-readable name, printed on a failure.
        kind: ``_ENFORCING`` or ``_OBSERVING``.
        accepted: Guard entry points, ANY-OF.
    """

    label: str
    kind: str
    accepted: frozenset[str]


# Each agent-output boundary, its kind, and the guard entry points that satisfy
# it. The set is ANY-OF, not all-of: a boundary is guarded by calling one of
# them, because they are alternative doors onto the same policy rather than
# separate obligations. ``enforce_output_policy`` rejects or rewrites in place
# and suits a call site that can abort; ``evaluate_output_policy`` hands back a
# verdict and suits one that has to turn a rejection into its own result type.
# The shared helpers (message, plan-prose, question, file-write, living-doc)
# are listed beside their callers, because the caller is satisfied by reaching
# the helper and the helper is what actually calls the policy.
_BOUNDARIES: Final[dict[str, _Boundary]] = {
    "src/synthorg/communication/_output_guard.py": _Boundary(
        "shared message guard",
        _ENFORCING,
        frozenset({"enforce_output_policy"}),
    ),
    "src/synthorg/communication/messenger.py": _Boundary(
        "inter-agent message send",
        _ENFORCING,
        frozenset({"guard_message_output"}),
    ),
    "src/synthorg/communication/messages/service.py": _Boundary(
        "MCP message send",
        _ENFORCING,
        frozenset({"guard_message_output"}),
    ),
    "src/synthorg/tools/git_tools.py": _Boundary(
        "agent commit message",
        _ENFORCING,
        frozenset({"enforce_output_policy", "evaluate_output_policy"}),
    ),
    "src/synthorg/meta/appliers/code_applier.py": _Boundary(
        "agent PR title / body",
        _ENFORCING,
        frozenset({"enforce_output_policy", "evaluate_output_policy"}),
    ),
    "src/synthorg/engine/_review_oracle_gates.py": _Boundary(
        "completing deliverable (shadow backstop)",
        _OBSERVING,
        frozenset({"evaluate_output_policy"}),
    ),
    "src/synthorg/engine/initiative/evaluate_session.py": _Boundary(
        "initiative evaluation verdict",
        _ENFORCING,
        frozenset({"enforce_output_policy", "evaluate_output_policy"}),
    ),
    "src/synthorg/engine/decomposition/_plan_output_guard.py": _Boundary(
        "shared plan-prose guard",
        _ENFORCING,
        frozenset({"enforce_output_policy"}),
    ),
    "src/synthorg/engine/decomposition/llm_parse.py": _Boundary(
        "plan item titles, descriptions, criteria, assumptions, questions",
        _ENFORCING,
        frozenset({"guard_plan_text", "guard_plan_texts"}),
    ),
    "src/synthorg/tools/file_system/_output_policy_guard.py": _Boundary(
        "shared file-write guard",
        _ENFORCING,
        frozenset({"evaluate_output_policy"}),
    ),
    "src/synthorg/tools/file_system/write_file.py": _Boundary(
        "agent code-file write",
        _ENFORCING,
        frozenset({"guard_written_content"}),
    ),
    "src/synthorg/tools/file_system/edit_file.py": _Boundary(
        "agent code-file edit",
        _ENFORCING,
        frozenset({"guard_written_content"}),
    ),
    "src/synthorg/tools/docs/_doc_output_guard.py": _Boundary(
        "shared living-doc guard",
        _ENFORCING,
        frozenset({"evaluate_output_policy"}),
    ),
    "src/synthorg/tools/docs/write_living_doc.py": _Boundary(
        "agent living-document publish",
        _ENFORCING,
        frozenset({"guard_doc_output"}),
    ),
    "src/synthorg/tools/chat/chat_tools.py": _Boundary(
        "agent outbound chat message",
        _ENFORCING,
        frozenset({"evaluate_output_policy"}),
    ),
    "src/synthorg/tools/communication/email_sender.py": _Boundary(
        "agent outbound email subject / body",
        _ENFORCING,
        frozenset({"evaluate_output_policy"}),
    ),
    "src/synthorg/tools/forge/forge_tools.py": _Boundary(
        "agent issue / PR body",
        _ENFORCING,
        frozenset({"enforce_output_policy", "evaluate_output_policy"}),
    ),
    "src/synthorg/tools/_question_output_guard.py": _Boundary(
        "shared parked-question guard",
        _ENFORCING,
        frozenset({"evaluate_output_policy"}),
    ),
    "src/synthorg/tools/clarification_tool.py": _Boundary(
        "agent clarification question",
        _ENFORCING,
        frozenset({"guard_question_text"}),
    ),
    "src/synthorg/tools/decision_tool.py": _Boundary(
        "agent decision question / options",
        _ENFORCING,
        frozenset({"guard_question_text"}),
    ),
}


def _called_names(tree: ast.AST) -> set[str]:
    """Collect the simple names invoked as calls anywhere in a module.

    Returns:
        The set of names appearing in ``f(...)`` and ``x.f(...)`` call
        positions, so a required guard must be genuinely called, not merely
        imported or mentioned in a comment/string.
    """
    called: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name):
            called.add(func.id)
        elif isinstance(func, ast.Attribute):
            called.add(func.attr)
    return called


class _Findings(NamedTuple):
    """What one run of the check found.

    Attributes:
        unguarded: Boundaries that no longer call their guard.
        deciding: Observing boundaries that regained the raising door.
        read_errors: Boundary files that could not be read or parsed.
    """

    unguarded: list[str]
    deciding: list[str]
    read_errors: list[str]


def _check(repo_root: Path) -> _Findings:
    """Inspect every declared boundary under *repo_root*.

    Args:
        repo_root: Root the relative boundary paths are resolved against.

    Returns:
        The three failure lists, empty when every boundary holds.
    """
    unguarded: list[str] = []
    deciding: list[str] = []
    read_errors: list[str] = []
    for relative, boundary in _BOUNDARIES.items():
        path = repo_root / relative
        try:
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source)
        except (OSError, UnicodeDecodeError, SyntaxError) as exc:
            read_errors.append(f"{relative}: {exc}")
            continue
        called = _called_names(tree)
        # Any-of: the entries are alternative doors onto the same policy, so
        # one call guards the boundary. See the note on ``_BOUNDARIES``.
        if not (boundary.accepted & called):
            wanted = " or ".join(sorted(boundary.accepted))
            unguarded.append(
                f"{relative} ({boundary.label}); expected a call to {wanted}"
            )
        if boundary.kind == _OBSERVING and _RAISING_GUARD in called:
            deciding.append(f"{relative} ({boundary.label})")
    return _Findings(unguarded, deciding, read_errors)


def main() -> int:
    """Assert each boundary calls the guard its kind requires.

    Returns:
        ``0`` when every boundary is guarded, ``1`` on a dropped guard or an
        observing boundary that regained the raising door, ``2`` when a
        boundary file cannot be read or parsed (fail-closed).
    """
    unguarded, deciding, read_errors = _check(_REPO_ROOT)

    if read_errors:
        print("ERROR: output-boundary gate could not read:", file=sys.stderr)
        for err in read_errors:
            print(f"  {err}", file=sys.stderr)
        return 2

    if unguarded:
        print(
            "Output-style guard missing at these agent-output boundaries "
            "(each must call its guard entry point before the output escapes):"
        )
        for entry in unguarded:
            print(f"  {entry}")

    if deciding:
        print(
            f"These boundaries observe and must not decide, but call "
            f"{_RAISING_GUARD} (style is enforced in-session at the tool that "
            f"wrote the output; a post-session refusal can only re-dispatch, "
            f"and must never fail work whose substance passed review):"
        )
        for entry in deciding:
            print(f"  {entry}")

    return 1 if unguarded or deciding else 0


if __name__ == "__main__":
    sys.exit(main())
