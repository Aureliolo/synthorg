#!/usr/bin/env python3
"""Pre-push / CI gate: an initiative completes only through the verified path.

The general loop's forcing property is that "done" is never a claim anyone can
make directly. Three structures hold it up, and each is one careless edit away
from being undone, so each is checked here.

1. **The tail is unskippable.** ``PlanStatus.EXECUTING`` must not reach
   ``COMPLETED``, and ``ProjectStatus.ACTIVE`` must not either: delivery is
   reachable only from the evaluate stage. Re-adding either edge would restore
   the old behaviour where a pile of individually-verified pieces completed an
   initiative nobody had assembled or scored.

2. **Only the evaluate stage completes a plan.** A call writing
   ``PlanStatus.COMPLETED`` through the audited plan-status seam belongs to
   :mod:`synthorg.engine.initiative.evaluate` and nowhere else. Any other
   writer is a second delivery path that skips the verdict. The owner is
   excluded by filename, so this does not distinguish call sites *within* that
   module; the companion check is that ``derive_plan_status`` never returns
   ``COMPLETED``, so a status the rollup computed can never reach the seam
   either.

3. **Every work unit declares a deliverable.** ``PlanItem`` and
   ``DecompositionPlan`` must both call ``validate_expected_artifacts``: it is
   what arms the fail-loud zero-artifact guard on the dispatched task, so
   dropping it silently re-opens the "chat-only run looks finished" hole.

Sanctioned exceptions opt out with a per-line trailing comment::

    await writer.sync_status(
        plan, PlanStatus.COMPLETED
    )  # lint-allow: verified-completion -- <reason>

The justification after ``--`` is required. There is no baseline file: the rule
ships with zero offenders.

Usage::

    python scripts/check_verified_completion_paths.py
    python scripts/check_verified_completion_paths.py --repo-root PATH
"""

import argparse
import ast
import re
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Final

SUPPRESSION_MARKER: Final[str] = "lint-allow: verified-completion"

_SUPPRESSION_RE: Final[re.Pattern[str]] = re.compile(
    r"\blint-allow:\s*verified-completion\s*--\s*\S",
)

#: The only module allowed to write a plan's COMPLETED status. Delivery is its
#: verdict; every other writer would be a way around that verdict.
_PLAN_COMPLETION_OWNER: Final[str] = "src/synthorg/engine/initiative/evaluate.py"

#: Seams that persist a plan-status transition.
_PLAN_STATUS_SEAMS: Final[frozenset[str]] = frozenset({"sync_status", "_advance_plan"})

#: Files the invariant checks read, relative to the repo root.
_PLAN_TRANSITIONS: Final[str] = "src/synthorg/core/plan_transitions.py"
_PROJECT_TRANSITIONS: Final[str] = "src/synthorg/core/project_transitions.py"
_ARTIFACT_VALIDATORS: Final[tuple[str, ...]] = (
    "src/synthorg/core/plan.py",
    "src/synthorg/engine/decomposition/models.py",
)
_ARTIFACT_VALIDATOR_CALL: Final[str] = "validate_expected_artifacts"

#: The post-execution transition, and the two things it must still do with a
#: run that did not deliver. Declaring an artifact (above) only matters if
#: something checks the declaration; leaving an unfinished run untransitioned
#: only matters because the stall derivation reads IN_PROGRESS as progress.
_POST_EXECUTION_TRANSITIONS: Final[str] = "src/synthorg/engine/task_sync.py"
_POST_EXECUTION_ENTRY: Final[str] = "apply_post_execution_transitions"
_ARTIFACT_PROBE_CALL: Final[str] = "_absent_artifacts"
_UNFINISHED_REASON_TABLE: Final[str] = "_UNFINISHED_REASONS"

#: Test evidence is what the build/test oracle judges, so where it comes from
#: is an invariant and not a detail. It is minted from the executed command,
#: by one module. A tool that took a ``purpose`` argument would put the
#: decision back in the model's hands: an agent that produced no passing suite
#: could label a run as tests and arm the oracle with nothing behind it.
_TEST_EVIDENCE_OWNER: Final[str] = "src/synthorg/tools/_test_run_capture.py"
_TEST_PURPOSE_MEMBER: Final[str] = "TESTS"
_PURPOSE_PARAMETER: Final[str] = "purpose"
_MODEL_FACING_TOOLS: Final[tuple[str, ...]] = (
    "src/synthorg/tools/code_runner.py",
    "src/synthorg/tools/terminal/shell_command.py",
)
#: Every termination reason that stops a run without finishing it. Each must
#: reach a terminal status of its own; left out, a task sits at IN_PROGRESS
#: forever and its initiative can never be replanned or completed.
_UNFINISHED_REASONS_REQUIRED: Final[tuple[str, ...]] = (
    "MAX_TURNS",
    "BUDGET_EXHAUSTED",
    "STAGNATION",
)

#: The forbidden edges, as ``(source, forbidden target)`` per machine.
_FORBIDDEN_EDGES: Final[tuple[tuple[str, str, str], ...]] = (
    (_PLAN_TRANSITIONS, "EXECUTING", "COMPLETED"),
    (_PROJECT_TRANSITIONS, "ACTIVE", "COMPLETED"),
)

#: Where COMPLETED is legitimately reachable from, per machine.
_COMPLETION_SOURCE: Final[tuple[tuple[str, str], ...]] = (
    (_PLAN_TRANSITIONS, "EVALUATING"),
    (_PROJECT_TRANSITIONS, "EVALUATING"),
)


def _read(root: Path, rel: str) -> tuple[str, ast.Module] | None:
    """Read and parse a repo-relative source file.

    Returns:
        The source text and its parsed tree, or ``None`` when unreadable.
    """
    path = root / rel
    try:
        source = path.read_text(encoding="utf-8")
        return source, ast.parse(source)
    except OSError, SyntaxError:
        return None


def _transition_map(tree: ast.Module) -> dict[str, set[str]]:
    """Extract ``VALID_TRANSITIONS`` as ``{source: {target, ...}}``.

    Reads the literal dict statically, so the gate cannot be fooled by a
    machine assembled at runtime: an unreadable table yields an empty map and
    the reachability check below reports it.

    Returns:
        The declared edges, keyed by source-status member name.
    """
    edges: dict[str, set[str]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            named = any(
                isinstance(t, ast.Name) and t.id == "VALID_TRANSITIONS"
                for t in node.targets
            )
        elif isinstance(node, ast.AnnAssign):
            named = (
                isinstance(node.target, ast.Name)
                and node.target.id == "VALID_TRANSITIONS"
            )
        else:
            continue
        if not named or not isinstance(node.value, ast.Dict):
            continue
        for key, value in zip(node.value.keys, node.value.values, strict=True):
            if not isinstance(key, ast.Attribute):
                continue
            edges[key.attr] = {
                member.attr
                for member in ast.walk(value)
                if isinstance(member, ast.Attribute)
            }
    return edges


def _check_state_machines(root: Path) -> list[str]:
    """Check that delivery is reachable only from the evaluate stage.

    Returns:
        One message per violated invariant.
    """
    messages: list[str] = []
    for rel, source_status, forbidden in _FORBIDDEN_EDGES:
        parsed = _read(root, rel)
        if parsed is None:
            messages.append(f"{rel}: unreadable; the forcing invariant is unchecked")
            continue
        edges = _transition_map(parsed[1])
        if forbidden in edges.get(source_status, set()):
            messages.append(
                f"{rel}: {source_status} -> {forbidden} is back. The tail "
                "(integrate, then evaluate) is what makes delivery mean "
                "something; a direct edge lets a plan complete without being "
                "assembled or scored."
            )
    for rel, expected_source in _COMPLETION_SOURCE:
        parsed = _read(root, rel)
        if parsed is None:
            continue
        edges = _transition_map(parsed[1])
        sources = {src for src, targets in edges.items() if "COMPLETED" in targets}
        if sources != {expected_source}:
            messages.append(
                f"{rel}: COMPLETED is reachable from {sorted(sources)}, expected "
                f"only from {expected_source}. Delivery has exactly one "
                "predecessor by design."
            )
    return messages


def _check_derivation_never_completes(root: Path) -> list[str]:
    """Check that ``derive_plan_status`` cannot return COMPLETED.

    The writer check above matches a literal ``PlanStatus.COMPLETED`` argument,
    so it cannot see ``_advance_plan(plan, derived)``. That call is safe only
    because the derivation has no COMPLETED branch; making that explicit here
    keeps the two halves of the invariant from drifting apart.

    Returns:
        One message when the derivation gained a COMPLETED branch.
    """
    rel = "src/synthorg/engine/initiative/completion.py"
    parsed = _read(root, rel)
    if parsed is None:
        return [f"{rel}: unreadable; the derivation invariant is unchecked"]
    seen = False
    for node in ast.walk(parsed[1]):
        if (
            not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
            or node.name != "derive_plan_status"
        ):
            continue
        seen = True
        for inner in ast.walk(node):
            if (
                isinstance(inner, ast.Attribute)
                and inner.attr == "COMPLETED"
                and isinstance(inner.value, ast.Name)
                and inner.value.id == "PlanStatus"
            ):
                return [
                    (
                        f"{rel}:{inner.lineno}: derive_plan_status names "
                        "PlanStatus.COMPLETED. The rollup writes whatever this "
                        "derives, so a COMPLETED branch here is a second delivery "
                        "path that skips the evaluate stage's verdict."
                    )
                ]
    if not seen:
        return [
            (
                f"{rel}: derive_plan_status not found; the derivation invariant is "
                "unchecked. Point the gate at its new home rather than leaving it "
                "silently satisfied."
            )
        ]
    return []


def _check_plan_completion_writers(root: Path) -> list[str]:
    """Check that only the evaluate stage writes a plan's COMPLETED status.

    Returns:
        One message per unsanctioned writer.
    """
    messages: list[str] = []
    for path in sorted((root / "src" / "synthorg").rglob("*.py")):
        rel = path.relative_to(root).as_posix()
        if rel == _PLAN_COMPLETION_OWNER:
            continue
        parsed = _read(root, rel)
        if parsed is None:
            continue
        source, tree = parsed
        lines = source.splitlines()
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = (
                node.func.attr
                if isinstance(node.func, ast.Attribute)
                else node.func.id
                if isinstance(node.func, ast.Name)
                else ""
            )
            if name not in _PLAN_STATUS_SEAMS:
                continue
            arguments = [*node.args, *(kw.value for kw in node.keywords)]
            if not any(
                isinstance(arg, ast.Attribute)
                and arg.attr == "COMPLETED"
                and isinstance(arg.value, ast.Name)
                and arg.value.id == "PlanStatus"
                for arg in arguments
            ):
                continue
            end = node.end_lineno or node.lineno
            span = "\n".join(lines[node.lineno - 1 : min(end, len(lines))])
            if _SUPPRESSION_RE.search(span):
                continue
            messages.append(
                f"{rel}:{node.lineno}: writes PlanStatus.COMPLETED through "
                f"{name!r}. Delivery is the evaluate stage's verdict; another "
                "writer is a way around it. Move the write to "
                f"{_PLAN_COMPLETION_OWNER}, or add "
                f"'# {SUPPRESSION_MARKER} -- <reason>' on this line."
            )
    return messages


def _check_artifact_invariant(root: Path) -> list[str]:
    """Check that both plan-shaped models still enforce a declared deliverable.

    Returns:
        One message per model that stopped enforcing it.
    """
    messages: list[str] = []
    for rel in _ARTIFACT_VALIDATORS:
        parsed = _read(root, rel)
        if parsed is None:
            messages.append(
                f"{rel}: unreadable; the deliverable invariant is unchecked"
            )
            continue
        calls = {
            node.func.id
            for node in ast.walk(parsed[1])
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        if _ARTIFACT_VALIDATOR_CALL not in calls:
            messages.append(
                f"{rel}: no longer calls {_ARTIFACT_VALIDATOR_CALL}. A WORK unit "
                "with no declared deliverable disarms the fail-loud "
                "zero-artifact guard, so a run that produced nothing reads as "
                "finished."
            )
    return messages


def _functions_by_name(tree: ast.Module) -> dict[str, ast.AST]:
    """Index every top-level function in *tree* by name.

    Returns:
        Each ``def`` / ``async def`` at module scope, keyed by its name.
    """
    return {
        node.name: node
        for node in tree.body
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
    }


def _calls_in(node: ast.AST) -> set[str]:
    """Collect the bare names invoked as calls anywhere under *node*.

    Returns:
        The set of ``f(...)`` names, ignoring attribute calls.
    """
    return {
        sub.func.id
        for sub in ast.walk(node)
        if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Name)
    }


def _reaches(entry: str, target: str, functions: Mapping[str, ast.AST]) -> bool:
    """Whether *target* is called from *entry*, directly or through helpers.

    A whole-module name match would accept a module where the probe is
    called only from a helper nothing reaches, which is the shape a
    refactor produces by accident and a gate is supposed to catch. Walking
    the call graph accepts the honest refactor -- the guard moved into a
    helper the entry point calls -- and rejects the stranded one.

    Returns:
        ``True`` when a path of same-module calls leads from *entry* to
        *target*.
    """
    seen: set[str] = set()
    frontier = [entry]
    while frontier:
        current = frontier.pop()
        if current in seen or current not in functions:
            continue
        seen.add(current)
        called = _calls_in(functions[current])
        if target in called:
            return True
        frontier.extend(called)
    return False


def _table_bindings(tree: ast.Module, table: str) -> list[ast.expr | None]:
    """Collect every module-level assignment to *table*, in source order.

    Returns:
        One entry per binding: the assigned value, or ``None`` for a bare
        annotation that binds nothing.
    """
    bindings: list[ast.expr | None] = []
    for node in tree.body:
        if isinstance(node, ast.Assign):
            targets: list[ast.expr] = list(node.targets)
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
        else:
            continue
        if any(
            isinstance(target, ast.Name) and target.id == table for target in targets
        ):
            bindings.append(node.value)
    return bindings


def _table_reasons(value: ast.expr | None) -> set[str]:
    """Read the ``TerminationReason`` members keyed in a table's *value*.

    Only the mapping's keys count. A reason appearing on the value side is
    the failure message, not an entry, so a table keyed by something else
    that merely mentions the reasons terminalises none of them. The literal
    is found inside whatever wraps it (``MappingProxyType`` today), and a
    binding carrying no mapping at all reads as no keys, which fails
    closed.

    Returns:
        The member names used as keys of the table's mapping.
    """
    if value is None:
        return set()
    mapping = next(
        (sub for sub in ast.walk(value) if isinstance(sub, ast.Dict)),
        None,
    )
    if mapping is None:
        return set()
    return {
        key.attr
        for key in mapping.keys
        if isinstance(key, ast.Attribute)
        and isinstance(key.value, ast.Name)
        and key.value.id == "TerminationReason"
    }


def _check_test_evidence_provenance(root: Path) -> list[str]:
    """Check test evidence is still minted from the command, by one module.

    Two ways the provenance breaks, both leaving the oracle judging a claim
    rather than a run: a model-facing tool regaining a ``purpose`` argument,
    so the agent labels its own run; and a second module stamping
    ``CodeExecutionPurpose.TESTS``, so command recognition stops being the
    only door.

    Returns:
        One message per break.
    """
    messages: list[str] = []
    for rel in _MODEL_FACING_TOOLS:
        parsed = _read(root, rel)
        if parsed is None:
            messages.append(f"{rel}: unreadable; test-evidence provenance unchecked")
            continue
        _source, tree = parsed
        if _stamps_test_purpose(tree):
            messages.append(
                f"{rel}: stamps CodeExecutionPurpose.{_TEST_PURPOSE_MEMBER} itself. "
                f"Evidence is minted in {_TEST_EVIDENCE_OWNER} from the executed "
                "command; a second source is a second thing to keep honest."
            )
        if _declares_purpose(tree):
            messages.append(
                f"{rel}: names a `purpose` parameter again. A model-supplied "
                "purpose lets an agent that ran no suite arm the build/test "
                "oracle with a label."
            )
    owner = _read(root, _TEST_EVIDENCE_OWNER)
    if owner is None:
        return [
            *messages,
            f"{_TEST_EVIDENCE_OWNER}: unreadable; nothing mints test evidence",
        ]
    if not _stamps_test_purpose(owner[1]):
        messages.append(
            f"{_TEST_EVIDENCE_OWNER}: no longer stamps CodeExecutionPurpose."
            f"{_TEST_PURPOSE_MEMBER}, so no run produces test evidence and the "
            "build/test oracle abstains on every task."
        )
    return messages


def _declares_purpose(tree: ast.AST) -> bool:
    """Whether *tree* declares a ``purpose`` the caller can set.

    A declaration is what hands the decision back to the model: a
    parameter on a signature, or a field on the tool's args model. A
    ``purpose=`` keyword the module passes on to something else is the
    opposite -- the module deciding -- so it is not matched here, and the
    one that matters is caught by the ``TESTS`` check instead.

    Returns:
        ``True`` when a parameter or attribute named ``purpose`` is
        declared anywhere in the module.
    """
    for node in ast.walk(tree):
        if isinstance(node, ast.arg) and node.arg == _PURPOSE_PARAMETER:
            return True
        if isinstance(node, ast.AnnAssign):
            target: ast.expr = node.target
        elif isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
        else:
            continue
        if isinstance(target, ast.Name) and target.id == _PURPOSE_PARAMETER:
            return True
    return False


def _stamps_test_purpose(tree: ast.AST) -> bool:
    """Whether *tree* assigns ``CodeExecutionPurpose.TESTS`` anywhere.

    Returns:
        ``True`` when the member is referenced as an attribute.
    """
    return any(
        isinstance(node, ast.Attribute)
        and node.attr == _TEST_PURPOSE_MEMBER
        and isinstance(node.value, ast.Name)
        and node.value.id == "CodeExecutionPurpose"
        for node in ast.walk(tree)
    )


def _check_post_execution_guards(root: Path) -> list[str]:
    """Check the post-execution transition still guards both failure shapes.

    Two guards, both silently disarmable by deleting one call:

    - the artifact probe, without which the only empty-run signal is the
      zero-tool-call proxy, which an agent that read a file and wrote
      nothing walks straight past;
    - the unfinished-reason table, without which a run that hit its turn
      cap, exhausted its budget or stagnated stays at IN_PROGRESS, where
      the stall derivation reads it as still moving.

    Both are checked structurally rather than by searching the module
    text. A name match passes on a module where the probe sits in a
    helper the entry point never calls, and on one whose table is empty
    while the reason names appear in a comment or an unrelated branch:
    exactly the two states this gate exists to distinguish from a working
    guard.

    Returns:
        One message per missing guard.
    """
    rel = _POST_EXECUTION_TRANSITIONS
    parsed = _read(root, rel)
    if parsed is None:
        return [f"{rel}: unreadable; the post-execution guards are unchecked"]
    _source, tree = parsed
    messages: list[str] = []
    functions = _functions_by_name(tree)
    if _POST_EXECUTION_ENTRY not in functions:
        return [
            (
                f"{rel}: {_POST_EXECUTION_ENTRY} is gone, so nothing applies "
                "the post-execution guards at all."
            )
        ]
    if not _reaches(_POST_EXECUTION_ENTRY, _ARTIFACT_PROBE_CALL, functions):
        messages.append(
            f"{rel}: {_POST_EXECUTION_ENTRY} no longer reaches "
            f"{_ARTIFACT_PROBE_CALL}. Without it the only empty-run signal is "
            "the zero-tool-call proxy, so a run that read files and wrote "
            "nothing reaches review as delivered."
        )
    bindings = _table_bindings(tree, _UNFINISHED_REASON_TABLE)
    if not bindings:
        messages.append(
            f"{rel}: {_UNFINISHED_REASON_TABLE} is gone. A run that stopped "
            "without finishing would stay IN_PROGRESS, which the stall "
            "derivation reads as still moving, so its initiative could never "
            "be replanned or completed."
        )
        return messages
    if len(bindings) != 1:
        # Whichever binding this gate read, the runtime reads the last one.
        # A table checked here and a different table in force is the shape
        # that lets an emptied replacement ship behind a passing gate.
        messages.append(
            f"{rel}: {_UNFINISHED_REASON_TABLE} is bound "
            f"{len(bindings)} times at module level. One name, one table: "
            "reduce it to a single binding so what is checked is what runs."
        )
        return messages
    reasons = _table_reasons(bindings[0])
    messages.extend(
        f"{rel}: {_UNFINISHED_REASON_TABLE} no longer terminalises {reason}. "
        "That run would sit at IN_PROGRESS forever."
        for reason in _UNFINISHED_REASONS_REQUIRED
        if reason not in reasons
    )
    return messages


def main(argv: list[str] | None = None) -> int:
    """CLI entry point.

    Returns:
        ``0`` when every invariant holds, ``1`` when one is violated, ``2`` on
        a bad ``--repo-root``.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=None,
        help="Project root to anchor path resolution against.",
    )
    args = parser.parse_args(argv)

    root = args.repo_root or Path(__file__).resolve().parent.parent
    if not root.is_dir():
        print(f"--repo-root must be a directory: {root}", file=sys.stderr)
        return 2

    messages = [
        *_check_state_machines(root),
        *_check_derivation_never_completes(root),
        *_check_plan_completion_writers(root),
        *_check_artifact_invariant(root),
        *_check_post_execution_guards(root),
        *_check_test_evidence_provenance(root),
    ]
    if messages:
        for message in messages:
            print(message)
        print(
            f"\n{len(messages)} verified-completion violation(s) found. "
            "See docs/design/initiative-tail.md.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
