#!/usr/bin/env python3
"""Pre-push / CI gate: settings → startup wiring trace.

Detects "ghost-wired" settings -- registered in
``src/synthorg/settings/definitions/`` but consumed by a service whose
owning class is never instantiated at boot. Two known patterns surface
in the codebase today:

1. **Hardcoded-None ghost.** A service variable of form
   ``x: T | None = None`` at module scope in ``api/app.py`` (or a
   sibling lifecycle file) paired with a conditional
   ``if x is not None: x.start()`` guard. The guard always evaluates
   False, so the service never starts, and any setting whose
   consumer lives inside that service is dead at runtime even though
   the consumer code exists. Example: ``ApprovalTimeoutScheduler``.

2. **Factory-gated ghost.** A factory ``build_x(config)`` returning
   ``T | None`` whose ``None`` branch fires when a registered
   default-disabled flag is False. The lifecycle then conditions
   ``if x is not None: x.start()`` on the factory result. Example:
   ``BackupService`` gated on ``backup.enabled=False``.

Each ghost service is then matched to settings via:

- **Gating-namespace match** (factory case): every setting whose
  ``namespace`` equals the gating namespace is ghost-wired.
- **Class-file containment match** (hardcoded-None case): a setting
  is ghost-wired iff its ``key`` appears as a substring in the
  source file of the ghost class AND its ``namespace`` appears in
  that file's path. Conservative -- catches the known positive
  (``security.timeout_check_interval_seconds`` referenced in the
  scheduler module's docstring) without flagging neighbouring
  settings whose strings are not present.

Settings tagged ``read_only_post_init=True`` are skipped because
they are discoverability-only by design (the registry entry exists
so operators can introspect via ``/settings``; mutation is
rejected).

Per-line opt-out: append ``# lint-allow: bootstrap-wiring -- <reason>``
to the closing ``)`` of the ``_r.register(...)`` block. The
justification after ``--`` is required and must be non-empty
(mirrors ``# lint-allow: persistence-boundary``).

Baseline allowlist: ``scripts/setting_to_startup_trace_baseline.txt``
freezes pre-existing violations so the lint can ship without forcing
the wiring fix in the same PR. Lint behaviour: pass when current
violations ⊆ baseline; fail when new violations appear; warn (but
pass) when baseline entries are stale (fix landed). Regenerate via
``--update-baseline`` (explicit user approval to commit).

Usage::

    python scripts/check_setting_to_startup_trace.py
    python scripts/check_setting_to_startup_trace.py --paths src/synthorg
    python scripts/check_setting_to_startup_trace.py --update-baseline
"""

import argparse
import ast
import io
import sys
import tokenize
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal

_SUPPRESSION_MARKER: Final[str] = "lint-allow: bootstrap-wiring"

_BASELINE_FIELDS: Final[int] = 3

_LIFECYCLE_FILES: Final[tuple[str, ...]] = (
    "src/synthorg/api/app.py",
    "src/synthorg/api/lifecycle.py",
    "src/synthorg/api/lifecycle_builder.py",
    "src/synthorg/api/lifecycle_helpers.py",
    "src/synthorg/api/auto_wire.py",
)

_BASELINE_HEADER = """\
# Frozen baseline of pre-existing settings → startup-wiring violations.
# Each line is `<yaml_path>:<kind>:<owning_class>` sorted in
# deterministic order.
#
# scripts/check_setting_to_startup_trace.py reads this file to
# suppress violations at these exact entries. New violations NOT in
# this list will fail the pre-push hook.
#
# Regenerate (rare; requires explicit user approval) with:
#   uv run python scripts/check_setting_to_startup_trace.py --update-baseline
"""


# ── Public dataclasses ───────────────────────────────────────────


@dataclass(frozen=True)
class SettingRecord:
    """Metadata for one registered setting, extracted from definitions/."""

    namespace: str
    key: str
    yaml_path: str
    default: str | None
    read_only_post_init: bool
    source_file: str
    source_line: int
    has_suppression: bool


_GhostKind = Literal["hardcoded-none", "factory-gated"]
"""Discriminator for :class:`GhostService`. ``hardcoded-none`` covers
``x: T | None = None`` paired with a conditional ``if x is not None:
x.start()``. ``factory-gated`` covers ``x = factory(...)`` where the
factory returns ``None`` on a default-disabled gating flag."""

_ViolationKind = Literal["ghost-wired"]
"""Currently the lint only emits one violation kind. Reserved as a
``Literal`` (rather than a bare string) so future kinds (e.g.
``unconsumed-setting``) can extend the union without silent drift in
baseline-file parsing."""


@dataclass(frozen=True)
class GhostService:
    """A class whose .start() never runs at boot.

    Invariant: ``gating_namespace`` is non-None iff
    ``kind == "factory-gated"``. The ``__post_init__`` enforces this
    so callers that construct a ``GhostService`` with a mismatched
    pair fail fast instead of silently producing nonsense violations.
    """

    class_name: str
    kind: _GhostKind
    gating_namespace: str | None
    source_file: str  # path to lifecycle/app file where the ghost was detected

    def __post_init__(self) -> None:
        """Reject invalid (kind, gating_namespace) pairs."""
        has_gating = self.gating_namespace is not None
        is_factory = self.kind == "factory-gated"
        if has_gating != is_factory:
            msg = (
                f"GhostService(kind={self.kind!r}) requires "
                f"gating_namespace="
                f"{'<non-None>' if is_factory else 'None'}, "
                f"got {self.gating_namespace!r}"
            )
            raise ValueError(msg)


@dataclass(frozen=True)
class Violation:
    """A single ghost-wired setting flagged by the lint.

    Invariant: ``yaml_path`` and ``owning_class`` must not contain
    ``:`` because :meth:`baseline_key` joins fields with that
    delimiter. Setting names are dotted-lowercase (no colons by
    convention) and class names are bare identifiers; both
    invariants hold for every existing site, but the
    ``__post_init__`` makes the assumption explicit so a future
    rename that breaks the format fails fast.
    """

    yaml_path: str
    kind: _ViolationKind
    owning_class: str
    source_file: str
    source_line: int
    reason: str

    def __post_init__(self) -> None:
        """Reject field values that would corrupt the baseline format."""
        if ":" in self.yaml_path:
            msg = f"yaml_path may not contain ':'; got {self.yaml_path!r}"
            raise ValueError(msg)
        if ":" in self.owning_class:
            msg = f"owning_class may not contain ':'; got {self.owning_class!r}"
            raise ValueError(msg)

    def baseline_key(self) -> str:
        """Compact key used in the baseline file format."""
        return f"{self.yaml_path}:{self.kind}:{self.owning_class}"


# ── Suppression marker (tokenize-based, mirrors check_persistence_boundary) ──


def _line_has_trailing_marker(line: str) -> bool:
    """Return True iff *line* carries the marker as a trailing ``#`` comment.

    The marker name (``lint-allow: bootstrap-wiring``) must be
    followed by `` -- `` (a separator with surrounding whitespace) and
    non-empty justification text -- the canonical form is
    ``# lint-allow: bootstrap-wiring -- <reason>``.
    """
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(line).readline))
    except tokenize.TokenError, IndentationError, SyntaxError:
        return False
    for tok in tokens:
        if tok.type != tokenize.COMMENT:
            continue
        comment = tok.string.lstrip("#").strip()
        if not comment.startswith(_SUPPRESSION_MARKER):
            continue
        suffix = comment[len(_SUPPRESSION_MARKER) :].strip()
        if suffix.startswith("--"):
            justification = suffix[2:].strip()
            if justification:
                return True
    return False


# ── Settings inventory loader ───────────────────────────────────


def _resolve_namespace_member(value_node: ast.expr) -> str | None:
    """Resolve ``SettingNamespace.X`` to the lower-case member name.

    The :class:`SettingNamespace` enum is a :class:`StrEnum` whose
    member values are the lowercase form of the member name (per
    ``src/synthorg/settings/enums.py``). The lint relies on this
    invariant: the member name ``BACKUP`` always corresponds to the
    namespace string ``"backup"``.
    """
    if not isinstance(value_node, ast.Attribute):
        return None
    if not isinstance(value_node.value, ast.Name):
        return None
    if value_node.value.id != "SettingNamespace":
        return None
    return value_node.attr.lower()


def _extract_string(node: ast.expr | None) -> str | None:
    """Return the string-literal value of *node*, or None."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _extract_bool(node: ast.expr | None) -> bool | None:
    """Return the bool-literal value of *node*, or None."""
    if isinstance(node, ast.Constant) and isinstance(node.value, bool):
        return node.value
    return None


def _find_register_calls(tree: ast.Module) -> list[ast.Call]:
    """Return every ``_r.register(SettingDefinition(...))`` Call node.

    The match is structural: a Call whose ``func`` is an Attribute
    with attr="register", and whose first positional arg is a Call
    constructing ``SettingDefinition``. Anything else is ignored.
    """
    matches: list[ast.Call] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr != "register":
            continue
        if not node.args:
            continue
        first = node.args[0]
        if not isinstance(first, ast.Call):
            continue
        if not isinstance(first.func, ast.Name):
            continue
        if first.func.id != "SettingDefinition":
            continue
        matches.append(first)
    return matches


def _build_setting_record(
    defn_call: ast.Call,
    *,
    source_file: str,
    file_lines: list[str],
) -> SettingRecord | None:
    """Build a :class:`SettingRecord` from a ``SettingDefinition(...)`` call.

    Returns ``None`` when required keyword args (namespace, key) are
    missing or have non-resolvable shapes -- those are skipped rather
    than failing the lint, since the loader would otherwise reject
    legitimate edge cases that don't matter for ghost-wiring.
    """
    kwargs = {kw.arg: kw.value for kw in defn_call.keywords}
    namespace_node = kwargs.get("namespace")
    key_node = kwargs.get("key")
    if namespace_node is None or key_node is None:
        return None
    namespace = _resolve_namespace_member(namespace_node)
    key = _extract_string(key_node)
    if namespace is None or key is None:
        return None
    default = _extract_string(kwargs.get("default"))
    read_only = _extract_bool(kwargs.get("read_only_post_init")) is True
    yaml_path = _extract_string(kwargs.get("yaml_path")) or f"{namespace}.{key}"
    has_suppression = _detect_register_suppression(
        defn_call,
        file_lines=file_lines,
    )
    return SettingRecord(
        namespace=namespace,
        key=key,
        yaml_path=yaml_path,
        default=default,
        read_only_post_init=read_only,
        source_file=source_file,
        source_line=defn_call.lineno,
        has_suppression=has_suppression,
    )


def _detect_register_suppression(
    defn_call: ast.Call,
    *,
    file_lines: list[str],
) -> bool:
    """True iff the ``_r.register(...)`` block carries a suppression marker.

    The ``_r.register(SettingDefinition(...))`` block spans multiple
    lines; the marker is conventionally placed on the closing ``)``
    line. Search the few lines after the SettingDefinition call's
    end_lineno for the marker (plus the call's own line in case the
    registration is single-line, which is rare but legal).
    """
    end_line = getattr(defn_call, "end_lineno", defn_call.lineno) or defn_call.lineno
    # Allow up to 3 lines past end_lineno to land on the closing
    # ``)`` of the surrounding register(...) -- tighter than that
    # and unusual formatting trips us up; looser and we'd risk
    # picking up an unrelated marker on the next registration.
    last = min(len(file_lines), end_line + 3)
    for idx in range(defn_call.lineno - 1, last):
        if _line_has_trailing_marker(file_lines[idx]):
            return True
    return False


def load_setting_definitions(definitions_dir: Path) -> list[SettingRecord]:
    """Walk ``definitions_dir`` and return every registered setting.

    Raises:
        ValueError: If any definitions file is unreadable or has
            invalid Python syntax. Silently dropping a definitions
            file would let ghost-wired settings slip through the
            lint -- a typo in ``settings/definitions/X.py`` could
            make the entire file's settings invisible. The
            ``__init__.py`` re-export module is skipped by name.
    """
    records: list[SettingRecord] = []
    parse_errors: list[str] = []
    for path in sorted(definitions_dir.glob("*.py")):
        if path.name == "__init__.py":
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            parse_errors.append(f"{path.as_posix()}: read error: {exc}")
            continue
        except UnicodeDecodeError as exc:
            parse_errors.append(f"{path.as_posix()}: encoding error: {exc}")
            continue
        try:
            tree = ast.parse(text, filename=str(path))
        except SyntaxError as exc:
            parse_errors.append(
                f"{path.as_posix()}:{exc.lineno or 0}: syntax error: {exc.msg}"
            )
            continue
        file_lines = text.splitlines()
        rel = path.as_posix()
        for defn_call in _find_register_calls(tree):
            record = _build_setting_record(
                defn_call,
                source_file=rel,
                file_lines=file_lines,
            )
            if record is not None:
                records.append(record)
    if parse_errors:
        msg = (
            "Settings definitions could not be parsed "
            "(fix before proceeding):\n" + "\n".join(f"  {err}" for err in parse_errors)
        )
        raise ValueError(msg)
    return records


# ── Type-annotation utilities ───────────────────────────────────


def _extract_class_from_optional(annotation: ast.expr) -> str | None:
    """Return the bare class name from ``T | None`` / ``Optional[T]``.

    Returns ``None`` when the annotation is not a recognised Optional
    shape, or when ``T`` is not a bare ``Name``. Parametrised generics
    like ``Mapping[str, T] | None`` naturally fall through this check
    because ``_bare_name`` only succeeds for plain ``ast.Name`` nodes;
    this is intentional -- every known service-class wiring pattern
    uses a bare class identifier.
    """
    if isinstance(annotation, ast.BinOp) and isinstance(annotation.op, ast.BitOr):
        # T | None
        left, right = annotation.left, annotation.right
        if isinstance(right, ast.Constant) and right.value is None:
            return _bare_name(left)
        if isinstance(left, ast.Constant) and left.value is None:
            return _bare_name(right)
    if (
        isinstance(annotation, ast.Subscript)
        and isinstance(annotation.value, ast.Name)
        and annotation.value.id == "Optional"
    ):
        return _bare_name(annotation.slice)
    return None


def _bare_name(node: ast.expr) -> str | None:
    """Return ``node.id`` for a bare ``Name``, else ``None``."""
    if isinstance(node, ast.Name):
        return node.id
    return None


def _is_literal_none(node: ast.expr | None) -> bool:
    """True iff *node* is the constant ``None``."""
    return isinstance(node, ast.Constant) and node.value is None


# ── Hardcoded-None ghost detection ──────────────────────────────


def _collect_hardcoded_none_candidates(
    tree: ast.Module,
) -> list[tuple[str, str, int]]:
    """Return ``(var_name, class_name, lineno)`` for every ``x: T | None = None``.

    Walks the module recursively so detections inside function bodies
    are caught too (the hardcoded-None pattern most commonly lives
    inside ``def build_app(...)`` or similar).
    """
    candidates: list[tuple[str, str, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.AnnAssign):
            if not isinstance(node.target, ast.Name):
                continue
            if not _is_literal_none(node.value):
                continue
            class_name = _extract_class_from_optional(node.annotation)
            if class_name is not None:
                candidates.append((node.target.id, class_name, node.lineno))
    return candidates


def _is_started_at_some_site(
    var_name: str,
    trees_by_path: dict[str, ast.Module],
) -> bool:
    """True iff *var_name* appears in a ``if x is not None: x.start()|x.run()`` gate.

    Checks across every parsed lifecycle/app file. The pattern is
    structural: an ``If`` test of ``Compare(Name(var_name), IsNot,
    Constant(None))`` (or ``Name(var_name) != None``) whose body
    calls ``var_name.start()`` or ``var_name.run()``.
    """
    for tree in trees_by_path.values():
        if _tree_contains_start_gate(tree, var_name):
            return True
    return False


def _tree_contains_start_gate(tree: ast.Module, var_name: str) -> bool:
    """Walk *tree* for an ``if x is not None: x.start()|x.run()`` gate."""
    for node in ast.walk(tree):
        if not isinstance(node, ast.If):
            continue
        if not _is_isnot_none(node.test, var_name):
            continue
        if _body_calls_start(node.body, var_name):
            return True
    return False


def _is_isnot_none(test: ast.expr, var_name: str) -> bool:
    """True iff *test* is ``var_name is not None`` (or ``!=`` form)."""
    if not isinstance(test, ast.Compare):
        return False
    if not isinstance(test.left, ast.Name) or test.left.id != var_name:
        return False
    if len(test.ops) != 1 or len(test.comparators) != 1:
        return False
    op = test.ops[0]
    if not isinstance(op, (ast.IsNot, ast.NotEq)):
        return False
    return _is_literal_none(test.comparators[0])


def _body_calls_start(body: list[ast.stmt], var_name: str) -> bool:
    """True iff any statement in *body* awaits/calls ``var_name.start()|run()``.

    Recurses into nested ``If`` / ``Try`` / ``With`` so a start call
    nested inside a try/except inside the gate still counts.
    """
    for stmt in body:
        for node in ast.walk(stmt):
            if not isinstance(node, ast.Call):
                continue
            if not isinstance(node.func, ast.Attribute):
                continue
            if node.func.attr not in ("start", "run"):
                continue
            if not isinstance(node.func.value, ast.Name):
                continue
            if node.func.value.id == var_name:
                return True
    return False


def find_hardcoded_none_ghosts(src_root: Path) -> list[GhostService]:
    """Return every hardcoded-None ghost service in lifecycle/app files.

    A candidate is a module-scope or function-scope variable
    ``x: T | None = None`` that ALSO appears in a conditional
    ``if x is not None: x.start()`` gate somewhere in the same set
    of lifecycle/app files. Both halves are required: a hardcoded-None
    that's never used in a start gate is just a dead variable, not a
    ghost service worth flagging.
    """
    trees_by_path = _load_lifecycle_trees(src_root)
    ghosts: dict[str, GhostService] = {}
    for path, tree in trees_by_path.items():
        for var_name, class_name, _line in _collect_hardcoded_none_candidates(tree):
            # Dedup: a class flagged via one site is the same ghost
            # regardless of how many lifecycle files reference it.
            if class_name in ghosts:
                continue
            if not _is_started_at_some_site(var_name, trees_by_path):
                continue
            ghosts[class_name] = GhostService(
                class_name=class_name,
                kind="hardcoded-none",
                gating_namespace=None,
                source_file=path,
            )
    return list(ghosts.values())


def _load_lifecycle_trees(src_root: Path) -> dict[str, ast.Module]:
    """Parse the 5 lifecycle/app files and return ``{path: tree}``.

    ``src_root`` points at ``<repo>/src/synthorg/`` so relative
    lifecycle paths are resolved from there. Files that don't exist
    are silently skipped (test fake-repos may not have all of them).

    Raises:
        ValueError: If any lifecycle/app file exists but cannot be
            read or parsed. These files are non-negotiable inputs to
            the lint -- if app.py has a merge-conflict marker or
            syntax error, ghost-service detection silently fails and
            ghost-wired settings could ship to production. Failing
            loud here forces a human fix before the lint can run.
    """
    trees: dict[str, ast.Module] = {}
    parse_errors: list[str] = []
    for rel in _LIFECYCLE_FILES:
        # rel is project-relative (``src/synthorg/api/app.py``); strip
        # the prefix so it resolves under ``src_root``.
        bare = rel.removeprefix("src/synthorg/")
        path = src_root / bare
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            parse_errors.append(f"{path.as_posix()}: read error: {exc}")
            continue
        except UnicodeDecodeError as exc:
            parse_errors.append(f"{path.as_posix()}: encoding error: {exc}")
            continue
        try:
            trees[path.as_posix()] = ast.parse(text, filename=str(path))
        except SyntaxError as exc:
            parse_errors.append(
                f"{path.as_posix()}:{exc.lineno or 0}: syntax error: {exc.msg}"
            )
            continue
    if parse_errors:
        msg = (
            "Lifecycle files could not be parsed "
            "(fix before proceeding):\n" + "\n".join(f"  {err}" for err in parse_errors)
        )
        raise ValueError(msg)
    return trees


# ── Factory-gated ghost detection ──────────────────────────────


def _build_import_map(tree: ast.Module) -> dict[str, str]:
    """Return ``{local_name: dotted_module}`` for ``from X import Y`` lines.

    Used to resolve ``Y(...)`` calls back to their source module so
    the lint can locate factory definitions to inspect.
    """
    aliases: dict[str, str] = {}
    for node in tree.body if isinstance(tree, ast.Module) else ():
        if isinstance(node, ast.ImportFrom) and node.module:
            for alias in node.names:
                aliases[alias.asname or alias.name] = node.module
    return aliases


def _resolve_module_to_path(module: str, src_root: Path) -> Path | None:
    """Resolve a dotted module path under ``src/synthorg/`` to a ``.py`` file."""
    if not module.startswith("synthorg."):
        return None
    rel = module.removeprefix("synthorg.").replace(".", "/") + ".py"
    candidate = src_root / rel
    if candidate.is_file():
        return candidate
    # Allow ``synthorg.x`` -> ``synthorg/x/__init__.py`` fallback.
    package_init = (
        src_root / module.removeprefix("synthorg.").replace(".", "/") / "__init__.py"
    )
    if package_init.is_file():
        return package_init
    return None


def _find_factory_function(
    factory_name: str,
    src_root: Path,
    import_map: dict[str, str],
) -> tuple[ast.FunctionDef, Path] | None:
    """Locate the factory's ``FunctionDef`` AST node and source file.

    Resolves via the importing file's ``from X import factory_name``
    and parses ``X``'s source. Returns ``None`` if the factory is
    builtin / external or its source can't be parsed.
    """
    module = import_map.get(factory_name)
    if module is None:
        return None
    path = _resolve_module_to_path(module, src_root)
    if path is None:
        return None
    try:
        text = path.read_text(encoding="utf-8")
    except OSError, UnicodeDecodeError:
        return None
    try:
        tree = ast.parse(text, filename=str(path))
    except SyntaxError:
        return None
    for node in ast.walk(tree):
        if (
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == factory_name
        ):
            # Mypy/pyright struggle with the union; cast via type
            # narrowing here -- the FunctionDef return type covers
            # both sync and async factories (the body inspection
            # is identical).
            return node, path  # type: ignore[return-value]
    return None


def _factory_gating_namespace(factory: ast.FunctionDef) -> str | None:
    """Inspect a factory body for an early ``if not <ns>.<flag>: return None`` branch.

    Returns the gating namespace (``"backup"`` from
    ``if not config.backup.enabled: return None``) when any of the
    recognised shapes match:

    - ``if not config.<ns>.<flag>: return None`` (full chain).
    - ``<var> = something.<ns>`` followed by
      ``if not <var>.<flag>: return None`` (aliased intermediate).

    More elaborate predicates (compound conditions, walrus,
    multi-step indirection) are intentionally skipped -- the lint
    falls silent rather than guessing.
    """
    aliases = _build_factory_alias_map(factory.body)
    for node in factory.body:
        if not isinstance(node, ast.If):
            continue
        if not _branch_returns_none(node.body):
            continue
        ns = _gating_namespace_from_test(node.test, aliases)
        if ns is not None:
            return ns
    return None


def _build_factory_alias_map(body: list[ast.stmt]) -> dict[str, str]:
    """Return ``{local_var: namespace}`` for ``var = <chain>.<ns>`` assignments.

    Scans top-level statements only -- nested-scope rebinds are
    rare in factory functions and adding scope tracking would balloon
    the lint without catching any known pattern.
    """
    aliases: dict[str, str] = {}
    for stmt in body:
        if not isinstance(stmt, ast.Assign):
            continue
        if len(stmt.targets) != 1 or not isinstance(stmt.targets[0], ast.Name):
            continue
        if not isinstance(stmt.value, ast.Attribute):
            continue
        # value: Attribute(value=*, attr=ns) -- e.g. config.backup
        aliases[stmt.targets[0].id] = stmt.value.attr
    return aliases


def _gating_namespace_from_test(
    test: ast.expr,
    aliases: dict[str, str],
) -> str | None:
    """Extract ``ns`` from ``not config.<ns>.<flag>`` or ``not <alias>.<flag>``.

    Recognises:
    - ``UnaryOp(Not, Attribute(Attribute(value=*, attr=ns), attr=flag))``
    - ``UnaryOp(Not, Attribute(value=Name(alias), attr=flag))`` when
      ``alias`` resolves to a namespace via the factory's alias map.
    - Implicit-truthy form: ``not <attr_chain>`` (no equality comparison).
    - Explicit-False form: ``<attr_chain> == False``.
    """
    target = test
    if isinstance(test, ast.UnaryOp) and isinstance(test.op, ast.Not):
        target = test.operand
    if isinstance(target, ast.Compare) and len(target.comparators) == 1:
        cmp = target.comparators[0]
        if isinstance(cmp, ast.Constant) and cmp.value is False:
            target = target.left
    if not isinstance(target, ast.Attribute):
        return None
    parent = target.value
    if isinstance(parent, ast.Attribute):
        # config.<ns>.<flag>
        return parent.attr
    if isinstance(parent, ast.Name):
        # <alias>.<flag>; resolve alias via the factory's local map.
        return aliases.get(parent.id)
    return None


def _branch_returns_none(body: list[ast.stmt]) -> bool:
    """True iff the branch's top-level statements include ``return None``.

    Walks the body's top-level statements looking for any
    ``Return(None)`` or bare ``Return()``. Side-effecting statements
    before the return are allowed -- the canonical pattern is::

        if not config.backup.enabled:
            logger.info(...)
            return None

    where the early-return guard logs the disabled-by-config
    decision before bailing out. We do NOT recurse into nested
    ``if`` / ``try`` clauses inside the branch; only the immediate
    body's top-level statements are inspected. A return wrapped in
    a nested condition wouldn't unconditionally terminate the
    branch, so flagging on it would over-report.
    """
    for stmt in body:
        if not isinstance(stmt, ast.Return):
            continue
        if stmt.value is None or _is_literal_none(stmt.value):
            return True
    return False


def find_factory_gated_ghosts(
    src_root: Path,
    *,
    settings_by_yaml: dict[str, SettingRecord] | None = None,
) -> list[GhostService]:
    """Return every factory-gated ghost service in lifecycle/app files.

    Detects: ``x = factory(...)`` where ``factory`` is imported from
    ``synthorg.<pkg>.<mod>``, returns ``T | None``, and has an early
    ``if not <ns>.<flag>: return None`` branch. The ghost is recorded
    iff:

    1. ``factory``'s return annotation contains a bare class ``T``.
    2. The factory body has a recognised gating-namespace pattern.
    3. Some lifecycle file gates ``x.start()`` on ``if x is not None:``.
    4. The gating flag's registered default disables the service
       (``<ns>.enabled=false``); when the default enables the
       service, the factory returns a real instance in the default
       boot and the service is NOT a ghost.

    ``settings_by_yaml`` is optional -- when omitted, it's loaded
    from ``src_root``. Passing the inventory in lets :func:`scan_repo`
    cache it across the two ghost detectors.
    """
    if settings_by_yaml is None:
        records = load_setting_definitions(src_root / "settings" / "definitions")
        settings_by_yaml = {s.yaml_path: s for s in records}
    trees_by_path = _load_lifecycle_trees(src_root)
    ghosts: dict[str, GhostService] = {}
    for path, tree in trees_by_path.items():
        import_map = _build_import_map(tree)
        for var_name, factory_name in _factory_call_assignments(tree):
            if not _is_started_at_some_site(var_name, trees_by_path):
                continue
            located = _find_factory_function(factory_name, src_root, import_map)
            if located is None:
                continue
            factory_node, _factory_path = located
            class_name = _factory_return_class(factory_node)
            if class_name is None:
                continue
            namespace = _factory_gating_namespace(factory_node)
            if namespace is None:
                continue
            gating_setting = settings_by_yaml.get(f"{namespace}.enabled")
            if gating_setting is not None and _is_default_enabled(
                gating_setting.default
            ):
                # Default-enabled: factory returns real instance; not a ghost.
                continue
            if class_name in ghosts:
                continue
            ghosts[class_name] = GhostService(
                class_name=class_name,
                kind="factory-gated",
                gating_namespace=namespace,
                source_file=path,
            )
    return list(ghosts.values())


def _factory_call_assignments(
    tree: ast.Module,
) -> list[tuple[str, str]]:
    """Yield ``(var_name, factory_name)`` for ``x = factory(...)`` patterns.

    Covers both ``x = factory(...)`` and ``x = await factory(...)``.
    Walks the entire tree (including function bodies) since lifecycle
    wiring routinely happens inside ``def build_app(...)`` rather than
    at module scope.
    """
    pairs: list[tuple[str, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        if len(node.targets) != 1 or not isinstance(node.targets[0], ast.Name):
            continue
        var_name = node.targets[0].id
        call = node.value
        if isinstance(call, ast.Await):
            call = call.value
        if not isinstance(call, ast.Call):
            continue
        if not isinstance(call.func, ast.Name):
            continue
        pairs.append((var_name, call.func.id))
    return pairs


def _factory_return_class(factory: ast.FunctionDef) -> str | None:
    """Extract bare ``T`` from a ``-> T | None`` factory return annotation."""
    if factory.returns is None:
        return None
    return _extract_class_from_optional(factory.returns)


# ── Class-file resolution (for hardcoded-None matching) ────────


def _build_class_index(src_root: Path) -> dict[str, list[Path]]:
    """Build ``{class_name: [file_paths]}`` for every top-level class.

    Walked once per :func:`scan_repo` invocation to avoid the
    quadratic cost of resolving each ghost's class file individually.
    Multi-mapping (list of paths) lets the caller refuse to guess
    when a class name is ambiguous.
    """
    index: dict[str, list[Path]] = {}
    for path in src_root.rglob("*.py"):
        try:
            text = path.read_text(encoding="utf-8")
        except OSError, UnicodeDecodeError:
            continue
        try:
            tree = ast.parse(text, filename=str(path))
        except SyntaxError:
            continue
        for node in ast.iter_child_nodes(tree):
            if isinstance(node, ast.ClassDef):
                index.setdefault(node.name, []).append(path)
    return index


def _resolve_class_file(
    class_name: str,
    class_index: dict[str, list[Path]],
) -> Path | None:
    """Return the unique class-file path for *class_name*, or ``None``.

    Returns ``None`` when zero matches or multiple matches
    (ambiguous; refuse to guess).
    """
    matches = class_index.get(class_name, [])
    if len(matches) == 1:
        return matches[0]
    return None


# ── Setting → ghost matching + violation construction ─────────


# ── Pattern A: ConfigResolver consumer discovery in ghost classes ──


_RESOLVER_GET_METHODS: Final[frozenset[str]] = frozenset(
    {"get", "get_int", "get_float", "get_bool", "get_str", "get_enum", "get_json"}
)

_RESOLVER_MIN_ARGS: Final[int] = 2
"""Minimum positional arg count for a recognised
``ConfigResolver.get_*(namespace, key)`` call."""
"""ConfigResolver scalar-accessor method names. Composed-config readers
(``get_api_config`` etc.) are intentionally excluded -- they fan out
to many settings and Pattern A is meant to catch direct point reads,
not config-object assembly."""


def _resolve_resolver_arg(node: ast.expr) -> str | None:
    """Resolve a ConfigResolver.get_*() arg to its string value.

    Recognises:

    - ``Constant("...")`` -- literal string.
    - ``SettingNamespace.<X>.value`` -- enum member's value (lower-case
      name per the ``StrEnum`` invariant).

    Anything else (variable, function call, format-string) is treated
    as dynamic and returns None; Pattern A only fires when both args
    are statically resolvable.
    """
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if (
        isinstance(node, ast.Attribute)
        and node.attr == "value"
        and isinstance(node.value, ast.Attribute)
        and isinstance(node.value.value, ast.Name)
        and node.value.value.id == "SettingNamespace"
    ):
        return node.value.attr.lower()
    return None


def _find_resolver_consumers_in_file(path: Path) -> list[tuple[str, str]]:
    """Return every ``ConfigResolver.get_*("<ns>", "<key>")`` (ns, key) pair.

    Walks the file's AST for any ``Call(Attribute(attr=∈ get_methods))``
    whose first two positional args resolve to string values via
    :func:`_resolve_resolver_arg`. Calls with dynamic args, missing
    args, or non-method shapes are skipped.

    The receiver is NOT validated -- the lint trusts that any
    ``.get_int("backup", "enabled")`` site in a ghost's class file is
    a config read, not a coincidence. False-positive risk is low
    because the method-name set is narrow and the arg-resolution
    rejects anything non-static.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError, UnicodeDecodeError:
        return []
    try:
        tree = ast.parse(text, filename=str(path))
    except SyntaxError:
        return []
    pairs: list[tuple[str, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr not in _RESOLVER_GET_METHODS:
            continue
        if len(node.args) < _RESOLVER_MIN_ARGS:
            continue
        ns = _resolve_resolver_arg(node.args[0])
        key = _resolve_resolver_arg(node.args[1])
        if ns is not None and key is not None:
            pairs.append((ns, key))
    return pairs


def _build_violation_for_pattern_a(
    setting: SettingRecord,
    ghost: GhostService,
    class_index: dict[str, list[Path]],
    resolver_consumers_cache: dict[Path, list[tuple[str, str]]],
) -> Violation | None:
    """Pattern A: ghost class file contains ``ConfigResolver.get_*(ns, key)``.

    Catches cross-namespace consumption (a ghost class in
    ``api/foo.py`` that reads ``engine.X`` via ConfigResolver) which
    the gating-namespace and class-file-containment matchers would
    miss because neither requires the setting's namespace to live in
    the ghost class.
    """
    class_file = _resolve_class_file(ghost.class_name, class_index)
    if class_file is None:
        return None
    consumers = resolver_consumers_cache.get(class_file)
    if consumers is None:
        consumers = _find_resolver_consumers_in_file(class_file)
        resolver_consumers_cache[class_file] = consumers
    if (setting.namespace, setting.key) not in consumers:
        return None
    return Violation(
        yaml_path=setting.yaml_path,
        kind="ghost-wired",
        owning_class=ghost.class_name,
        source_file=setting.source_file,
        source_line=setting.source_line,
        reason=(
            f"consumer {ghost.class_name} reads this setting via "
            f"ConfigResolver.get_*({setting.namespace!r}, "
            f"{setting.key!r}) (in {class_file.as_posix()}), but the "
            "service is never started at boot. Either wire the "
            "service or remove the setting."
        ),
    )


def _build_violation_for_factory_gated(
    setting: SettingRecord,
    ghost: GhostService,
    settings_by_yaml: dict[str, SettingRecord],
) -> Violation | None:
    """Flag a factory-gated ghost iff the gating setting is default-disabled.

    The factory pattern is ``if not config.<ns>.<flag>: return None``.
    To know whether the ghost is reachable in default config, we read
    the gating flag's registered default. If the default is ``"true"``
    / ``"1"``, the factory returns a real instance in default config
    and the service starts -- not a ghost. Only when the default is
    ``"false"`` / ``"0"`` (or missing) is the ghost confirmed.

    The gating-flag setting itself is conventionally named ``enabled``
    in the same namespace; if not present, fall back to assuming the
    ghost is real (conservative -- factory wouldn't have a None-return
    branch otherwise).
    """
    if ghost.gating_namespace is None:
        return None
    if setting.namespace != ghost.gating_namespace:
        return None
    gating_yaml = f"{ghost.gating_namespace}.enabled"
    gating_setting = settings_by_yaml.get(gating_yaml)
    if gating_setting is not None and _is_default_enabled(gating_setting.default):
        # Default-enabled: service starts in default config; not a ghost.
        return None
    return Violation(
        yaml_path=setting.yaml_path,
        kind="ghost-wired",
        owning_class=ghost.class_name,
        source_file=setting.source_file,
        source_line=setting.source_line,
        reason=(
            f"consumer {ghost.class_name} is gated on factory "
            f"return None when {gating_yaml}=False (the registered "
            "default), so all settings in this namespace are dead in "
            "default config. Wire the service unconditionally OR "
            f"flip the {gating_yaml} default."
        ),
    )


def _is_default_enabled(default: str | None) -> bool:
    """True iff *default* parses as a True boolean string."""
    if default is None:
        return False
    return default.strip().lower() in ("true", "1", "yes")


def _build_violation_for_hardcoded_none(
    setting: SettingRecord,
    ghost: GhostService,
    class_index: dict[str, list[Path]],
    class_file_text_cache: dict[Path, str],
) -> Violation | None:
    """Flag a hardcoded-None ghost iff its class file references the setting.

    The match is conservative: setting.key must appear as a substring
    in the ghost class's source file AND setting.namespace must
    appear in that file's path. Both halves are needed to avoid
    false positives -- a key string match alone could collide with
    unrelated identifiers.
    """
    class_file = _resolve_class_file(ghost.class_name, class_index)
    if class_file is None:
        return None
    rel_path = class_file.as_posix()
    if f"/{setting.namespace}/" not in rel_path:
        return None
    text = class_file_text_cache.get(class_file)
    if text is None:
        try:
            text = class_file.read_text(encoding="utf-8")
        except OSError, UnicodeDecodeError:
            return None
        class_file_text_cache[class_file] = text
    if setting.key not in text:
        return None
    return Violation(
        yaml_path=setting.yaml_path,
        kind="ghost-wired",
        owning_class=ghost.class_name,
        source_file=setting.source_file,
        source_line=setting.source_line,
        reason=(
            f"consumer {ghost.class_name} (in {rel_path}) is "
            "hardcoded to None at boot in lifecycle/app wiring; the "
            f"start guard `if {ghost.class_name.lower()} is not "
            "None:` always evaluates False. Either wire the service "
            "or remove the setting."
        ),
    )


def _detect_violation(  # noqa: PLR0913 -- caches passed in to avoid quadratic re-reads
    setting: SettingRecord,
    ghosts: list[GhostService],
    settings_by_yaml: dict[str, SettingRecord],
    class_index: dict[str, list[Path]],
    class_file_text_cache: dict[Path, str],
    resolver_consumers_cache: dict[Path, list[tuple[str, str]]],
) -> Violation | None:
    """Run all three matchers; return the first violation (or None).

    Match order:

    1. Factory-gated namespace match (every setting in the gating
       namespace is ghost-wired when the gating flag's default
       disables the service).
    2. Hardcoded-None class-file containment (setting key appears
       in the ghost class's source AND namespace appears in its
       file path).
    3. Pattern A direct ConfigResolver consumption (ghost class
       reads ``ConfigResolver.get_*(<ns>, <key>)`` matching the
       setting). Catches cross-namespace consumption that 1 + 2
       miss.

    First matcher to produce a violation wins; remaining matchers
    are skipped.
    """
    if setting.read_only_post_init:
        return None
    if setting.has_suppression:
        return None
    for ghost in ghosts:
        if ghost.kind == "factory-gated":
            v = _build_violation_for_factory_gated(setting, ghost, settings_by_yaml)
            if v is not None:
                return v
        elif ghost.kind == "hardcoded-none":
            v = _build_violation_for_hardcoded_none(
                setting,
                ghost,
                class_index,
                class_file_text_cache,
            )
            if v is not None:
                return v
    # Pattern A: cross-namespace direct ConfigResolver consumption.
    # Run after the namespace + class-file matchers so the more
    # specific matchers win first; this preserves the existing
    # baseline keys (each setting maps to one violation, not three).
    for ghost in ghosts:
        v = _build_violation_for_pattern_a(
            setting,
            ghost,
            class_index,
            resolver_consumers_cache,
        )
        if v is not None:
            return v
    return None


def scan_repo(
    project_root: Path,
    *,
    baseline_path: Path | None,  # noqa: ARG001 -- consumed by run_with_baseline
) -> list[Violation]:
    """Scan the repo and return every ghost-wired violation, ignoring baseline.

    The ``baseline_path`` parameter is accepted for API symmetry with
    :func:`run_with_baseline` so callers that already hold a
    ``baseline_path`` can pass it through without dispatching on
    which function to call. This function does not consult it;
    callers wanting baseline subtraction should use
    :func:`run_with_baseline` directly.
    """
    src_root = project_root / "src" / "synthorg"
    if not src_root.is_dir():
        return []
    definitions_dir = src_root / "settings" / "definitions"
    settings = load_setting_definitions(definitions_dir)
    settings_by_yaml = {s.yaml_path: s for s in settings}
    class_index = _build_class_index(src_root)
    class_file_text_cache: dict[Path, str] = {}
    resolver_consumers_cache: dict[Path, list[tuple[str, str]]] = {}
    ghosts = [
        *find_hardcoded_none_ghosts(src_root),
        *find_factory_gated_ghosts(src_root, settings_by_yaml=settings_by_yaml),
    ]
    violations: list[Violation] = []
    for setting in settings:
        v = _detect_violation(
            setting,
            ghosts,
            settings_by_yaml,
            class_index,
            class_file_text_cache,
            resolver_consumers_cache,
        )
        if v is not None:
            violations.append(v)
    violations.sort(key=lambda v: v.baseline_key())
    return violations


# ── Baseline ───────────────────────────────────────────────────


def _load_baseline(path: Path) -> set[str]:
    """Parse a baseline file into a set of ``<yaml_path>:<kind>:<class>`` keys.

    Blank lines and ``#`` comment lines are ignored. Other lines must
    match the expected three-field shape; malformed entries raise to
    fail loud (silently dropping entries lets violations slip past).

    Raises:
        ValueError: When the baseline file exists but cannot be read
            (OSError / encoding error) or contains a malformed entry.
    """
    if not path.is_file():
        return set()
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        msg = f"Cannot read baseline file {path.as_posix()}: {exc}"
        raise ValueError(msg) from exc
    except UnicodeDecodeError as exc:
        msg = f"Baseline file {path.as_posix()} has encoding error: {exc}"
        raise ValueError(msg) from exc
    entries: set[str] = set()
    for lineno, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        parts = stripped.split(":")
        if len(parts) != _BASELINE_FIELDS or not all(p for p in parts):
            msg = (
                f"{path.as_posix()}:{lineno}: malformed baseline entry "
                f"(expected '<yaml_path>:<kind>:<owning_class>', got {stripped!r})"
            )
            raise ValueError(msg)
        entries.add(stripped)
    return entries


def run_with_baseline(
    project_root: Path,
    *,
    baseline_path: Path,
) -> tuple[list[Violation], list[str]]:
    """Run the lint and subtract the baseline.

    Returns ``(new_violations, stale_baseline_entries)``:

    - ``new_violations`` -- violations not present in the baseline
      (these fail the lint).
    - ``stale_baseline_entries`` -- entries listed in the baseline
      that are NOT in the current violation set (warning-only; the
      baseline file is out of date but the lint still passes).
    """
    violations = scan_repo(project_root, baseline_path=None)
    baseline_keys = _load_baseline(baseline_path) if baseline_path.is_file() else set()
    current_keys = {v.baseline_key() for v in violations}
    new = [v for v in violations if v.baseline_key() not in baseline_keys]
    stale = sorted(baseline_keys - current_keys)
    return new, stale


def _write_baseline(violations: list[Violation], path: Path) -> None:
    """Overwrite the baseline file with sorted current-violation keys."""
    body = _BASELINE_HEADER + "\n".join(v.baseline_key() for v in violations) + "\n"
    path.write_text(body, encoding="utf-8")


# ── CLI driver ────────────────────────────────────────────────


def _format_violation_line(v: Violation) -> str:
    """One-line stdout violation report."""
    return (
        f"{v.source_file}:{v.source_line}: setting {v.yaml_path} is "
        f"{v.kind} -- {v.reason}"
    )


def _resolve_project_root(repo_root: Path | None) -> Path:
    """Resolve the project root, defaulting to this script's repo."""
    if repo_root is not None:
        return repo_root.resolve(strict=True)
    return Path(__file__).resolve().parent.parent


def main(argv: list[str] | None = None) -> int:  # noqa: PLR0911 -- distinct exit codes for CLI failure modes
    """CLI entry point."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=None,
        help=(
            "Project root to scan. Defaults to the script's repo "
            "(parent of scripts/). Pass ${{ github.workspace }} in CI "
            "to remove ambiguity."
        ),
    )
    parser.add_argument(
        "--baseline",
        type=Path,
        default=None,
        help=(
            "Path to the baseline file. Defaults to "
            "scripts/setting_to_startup_trace_baseline.txt under the "
            "resolved repo root."
        ),
    )
    parser.add_argument(
        "--update-baseline",
        action="store_true",
        help=(
            "Overwrite the baseline file with the current violation set "
            "(commit the diff after manual review)."
        ),
    )
    args = parser.parse_args(argv)

    try:
        project_root = _resolve_project_root(args.repo_root)
    except OSError as exc:
        print(f"--repo-root not accessible: {exc}", file=sys.stderr)
        return 2

    baseline_path = args.baseline or (
        project_root / "scripts" / "setting_to_startup_trace_baseline.txt"
    )

    if args.update_baseline:
        try:
            violations = scan_repo(project_root, baseline_path=None)
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            return 2
        try:
            _write_baseline(violations, baseline_path)
        except OSError as exc:
            print(
                f"Cannot write baseline {baseline_path.as_posix()}: {exc}",
                file=sys.stderr,
            )
            return 2
        print(
            f"Wrote {len(violations)} entries to {baseline_path.as_posix()}.",
            file=sys.stderr,
        )
        return 0

    try:
        new, stale = run_with_baseline(project_root, baseline_path=baseline_path)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    for v in new:
        print(_format_violation_line(v))

    if stale:
        print(
            f"\nWarning: {len(stale)} stale baseline entries (no longer violated):",
            file=sys.stderr,
        )
        for entry in stale:
            print(f"  {entry}", file=sys.stderr)
        print(
            "Regenerate via 'uv run python scripts/check_setting_to_startup_trace.py "
            "--update-baseline' once the wiring fix has merged.",
            file=sys.stderr,
        )

    if new:
        print(
            f"\n{len(new)} new ghost-wired setting(s). See "
            "docs/reference/configuration-precedence.md for the wiring "
            "contract; either start the consuming service unconditionally "
            "or remove the setting. Per-setting opt-out: append "
            "'# lint-allow: bootstrap-wiring -- <reason>' on the "
            "register(...) closing line.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
