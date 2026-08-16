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
from collections.abc import Iterator
from pathlib import Path
from typing import Final

SUPPRESSION_MARKER: Final[str] = "lint-allow: verified-completion"

#: What bounds a scope for the call walk: a body that runs only when
#: something invokes it, rather than where it is written.
_NESTED_SCOPES: Final[tuple[type[ast.AST], ...]] = (
    ast.FunctionDef,
    ast.AsyncFunctionDef,
    ast.Lambda,
)

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
    """Index every function in *tree* by name, at any nesting depth.

    Methods are included: the module-size budget makes moving a guard onto a
    class as ordinary as moving it into a sibling module, and a walker that
    only saw module scope would call either one a missing guard. Two
    same-named functions in one module collapse to the last indexed, which
    widens the walk rather than narrowing it; that direction is stated on
    :func:`_reaches`.

    Returns:
        Each ``def`` / ``async def``, keyed by its name.
    """
    return {
        node.name: node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
    }


def _dotted(node: ast.expr) -> str:
    """Render an attribute chain as dotted text, or empty when it is not one.

    Returns:
        ``"a.b"`` for ``a.b``, ``""`` for anything rooted in a call or
        subscript rather than a plain name.
    """
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = _dotted(node.value)
        return f"{prefix}.{node.attr}" if prefix else ""
    return ""


def _own_scope(node: ast.AST) -> Iterator[ast.AST]:
    """Walk *node* down to, but not into, the scopes nested inside it.

    A nested ``def`` or ``lambda`` is yielded so it can be recorded, and its
    body is left alone: statements there run only if something invokes it.

    Yields:
        Every node executing in *node*'s own scope, plus the nested
        definitions bounding it.
    """
    stack = list(ast.iter_child_nodes(node))
    while stack:
        current = stack.pop()
        yield current
        if isinstance(current, _NESTED_SCOPES):
            continue
        stack.extend(ast.iter_child_nodes(current))


def _bound_lambda(node: ast.AST) -> tuple[str, ast.Lambda] | None:
    """Return the name a statement binds a lambda to, when it does.

    Returns:
        The ``(name, lambda)`` pair, or ``None`` when *node* is not a plain
        assignment of a lambda to a single name.
    """
    if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
        return (
            (node.target.id, node.value) if isinstance(node.value, ast.Lambda) else None
        )
    if not (isinstance(node, ast.Assign) and isinstance(node.value, ast.Lambda)):
        return None
    targets = [t for t in node.targets if isinstance(t, ast.Name)]
    return (targets[0].id, node.value) if len(targets) == 1 else None


def _calls_in(node: ast.AST) -> set[tuple[str, str]]:
    """Collect every call reachable from *node* as ``(qualifier, name)``.

    The qualifier is the dotted text left of the final name, empty for a
    bare ``f(...)``. It is what lets ``module.guard()`` be resolved to the
    module and ``self._guard()`` fall back to this one.

    A nested scope bound to a name contributes only once that name is
    referenced, which is the one narrowing this walk makes deliberately: a
    helper defined and never mentioned again is the stranded shape the gate
    exists to reject, and counting its body would let it certify the caller.
    Referenced is the test rather than called, because handing a local
    function to a dispatcher (``build_for_backend(sqlite=_sqlite)``) reaches
    its body without ever naming it in call position. A lambda bound to
    nothing is reached where it appears: an argument, a return value or an
    element is already being handed onward at that point.

    Returns:
        The set of calls, attribute and bare alike.
    """
    found: set[tuple[str, str]] = set()
    referenced: set[str] = set()
    nested: dict[str, ast.AST] = {}
    deferred: set[int] = set()
    expanded: set[str] = set()
    pending: list[ast.AST] = [node]
    while pending:
        for sub in _own_scope(pending.pop()):
            if (binding := _bound_lambda(sub)) is not None:
                nested.setdefault(binding[0], binding[1])
                deferred.add(id(binding[1]))
            elif isinstance(sub, ast.FunctionDef | ast.AsyncFunctionDef):
                nested.setdefault(sub.name, sub)
            elif isinstance(sub, ast.Lambda):
                if id(sub) not in deferred:
                    pending.append(sub)
            elif isinstance(sub, ast.Name) and isinstance(sub.ctx, ast.Load):
                # Load only: the target of ``_helper = lambda: ...`` is a Name
                # too, and counting it would make every binding its own
                # reference, which is exactly the stranding being tested for.
                referenced.add(sub.id)
            if not isinstance(sub, ast.Call):
                continue
            if isinstance(sub.func, ast.Name):
                found.add(("", sub.func.id))
            elif isinstance(sub.func, ast.Attribute):
                found.add((_dotted(sub.func.value), sub.func.attr))
        if not pending:
            newly = sorted((referenced & nested.keys()) - expanded)
            expanded.update(newly)
            pending.extend(nested[name] for name in newly)
    return found


def _relative_module(rel: str, node: ast.ImportFrom) -> str | None:
    """Resolve a relative ``from . import`` against the importing module.

    Returns:
        The absolute dotted module name, or ``None`` when the level walks
        above the source root.
    """
    parts = rel.removeprefix("src/").removesuffix(".py").split("/")
    package = parts[:-1]
    if node.level > len(package):
        return None
    base = package[: len(package) - node.level + 1]
    return ".".join([*base, *(node.module.split(".") if node.module else [])])


def _first_party_import_sources(tree: ast.Module, rel: str) -> dict[str, list[str]]:
    """Map each name bound by a first-party import to its candidate modules.

    ``from synthorg.pkg import name`` is ambiguous in the AST alone: ``name``
    is either something ``pkg`` defines or the submodule ``pkg.name``. Both
    candidates are kept and the walk follows whichever it can read, because
    guessing one costs a real edge in the call graph.

    Args:
        tree: The parsed importing module.
        rel: Its repo-relative path, which relative imports resolve against.

    Returns:
        Local binding name to the repo-relative paths it may name.
    """
    sources: dict[str, list[str]] = {}

    def add(name: str, *modules: str) -> None:
        paths = [f"src/{m.replace('.', '/')}.py" for m in modules]
        sources.setdefault(name, []).extend(paths)

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("synthorg."):
                    add(alias.asname or alias.name, alias.name)
            continue
        if not isinstance(node, ast.ImportFrom):
            continue
        module = _relative_module(rel, node) if node.level else (node.module or None)
        if module is None or not module.startswith("synthorg"):
            continue
        for alias in node.names:
            add(alias.asname or alias.name, module, f"{module}.{alias.name}")
    return sources


def _qualifier_modules(qualifier: str, imports: dict[str, list[str]]) -> list[str]:
    """Resolve a call's dotted qualifier to the modules it may name.

    ``import synthorg.a.b`` binds the whole dotted path, so the longest
    prefix that is bound wins and the segments past it extend the path.
    Without that walk a call reached through a longer chain than the import
    spelled resolves to nothing, and the walker drops the edge silently.

    Returns:
        Repo-relative module paths, empty when nothing binds the qualifier.
    """
    parts = qualifier.split(".")
    for cut in range(len(parts), 0, -1):
        bound = imports.get(".".join(parts[:cut]))
        if not bound:
            continue
        tail = "/".join(parts[cut:])
        if not tail:
            return list(bound)
        return [f"{base.removesuffix('.py')}/{tail}.py" for base in bound]
    return []


def _reaches(root: Path, rel: str, entry: str, target: str) -> bool:
    """Whether *target* is called from *entry*, directly or through helpers.

    A whole-module name match would accept a module where the probe is
    called only from a helper nothing reaches, which is the shape a
    refactor produces by accident and a gate is supposed to catch. Walking
    the call graph accepts the honest refactor -- the guard moved into a
    helper the entry point calls -- and rejects the stranded one.

    The walk crosses first-party module boundaries, because the module-size
    budget makes extracting a helper into a sibling module the ordinary way
    a module stays under its cap. A same-module walk would call that legal
    extraction a missing guard, which teaches the wrong lesson: the guard is
    the reachable call, not the file it happens to sit in. Each function is
    keyed by ``(module, name)``, so two modules owning a same-named private
    helper stay distinct.

    What it follows, stated rather than implied, because a walker that
    silently drops an edge reports a missing guard where one exists: bare
    calls and attribute calls; absolute, relative and plain ``import``
    bindings, the longest bound prefix of a dotted qualifier winning;
    functions at any nesting depth, methods included, and a nested one from
    the point its name is referenced. Where a binding is ambiguous (a name
    that is either a submodule or something the package defines) both
    candidates are followed. Resolution is by NAME, not by type, so
    ``self._guard()`` and a module-level ``_guard`` in the same file are one
    node. Every one of those is a widening: this walk answers "is there a
    plausible path", and the guard it protects is the answer being no.

    Returns:
        ``True`` when a path of calls leads from *entry* to *target*.
    """
    cache: dict[str, tuple[dict[str, ast.AST], dict[str, list[str]]] | None] = {}

    def load(
        module_rel: str,
    ) -> tuple[dict[str, ast.AST], dict[str, list[str]]] | None:
        if module_rel not in cache:
            parsed = _read(root, module_rel)
            cache[module_rel] = (
                None
                if parsed is None
                else (
                    _functions_by_name(parsed[1]),
                    _first_party_import_sources(parsed[1], module_rel),
                )
            )
        return cache[module_rel]

    seen: set[tuple[str, str]] = set()
    frontier = [(rel, entry)]
    while frontier:
        current = frontier.pop()
        if current in seen:
            continue
        seen.add(current)
        module = load(current[0])
        if module is None or current[1] not in module[0]:
            continue
        functions, imports = module
        for qualifier, called in _calls_in(functions[current[1]]):
            if called == target:
                return True
            elsewhere = (
                _qualifier_modules(qualifier, imports)
                if qualifier
                else imports.get(called, [])
            )
            frontier.extend((where, called) for where in [*elsewhere, current[0]])
    return False


def _module_scope_statements(body: list[ast.stmt]) -> Iterator[ast.stmt]:
    """Walk *body* into module-scope blocks, stopping at nested scopes.

    An ``if`` / ``try`` / ``with`` / loop at module level still executes
    at import, so a rebinding inside one is as real as a top-level
    statement. A ``def`` or ``class`` body is a different scope, and a
    name bound there is a local or an attribute, not this module's.

    Yields:
        Every statement that executes in the module's own scope.
    """
    for node in body:
        yield node
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            continue
        for name in ("body", "orelse", "finalbody"):
            nested = getattr(node, name, None)
            if isinstance(nested, list):
                yield from _module_scope_statements(nested)
        handlers = getattr(node, "handlers", None)
        if isinstance(handlers, list):
            for handler in handlers:
                yield from _module_scope_statements(handler.body)
        # A ``match`` keeps its branches on ``cases``, not on ``body``, so
        # without this a rebinding inside one is invisible to a walk that
        # only follows the common statement fields.
        if isinstance(node, ast.Match):
            for case in node.cases:
                yield from _module_scope_statements(case.body)


def _table_bindings(tree: ast.Module, table: str) -> list[ast.expr | None]:
    """Collect every module-scope assignment to *table*, in source order.

    Returns:
        One entry per binding: the assigned value, or ``None`` for a bare
        annotation that binds nothing.
    """
    bindings: list[ast.expr | None] = []
    for node in _module_scope_statements(tree.body):
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
    if not _reaches(root, rel, _POST_EXECUTION_ENTRY, _ARTIFACT_PROBE_CALL):
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
