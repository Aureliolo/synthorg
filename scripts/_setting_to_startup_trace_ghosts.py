"""Lifecycle parsing + ghost-service detection for the bootstrap-wiring lint.

Two ghost-service patterns are detected here:

1. **Hardcoded-None ghost.** A service variable
   ``x: T | None = None`` paired with a conditional
   ``if x is not None: x.start()`` somewhere in the lifecycle/app
   files. Both halves are required: a hardcoded-None that never
   participates in a start gate is a dead variable, not a ghost.

2. **Factory-gated ghost.** ``x = factory(...)`` where the factory
   returns ``T | None`` and has an early ``return None`` branch
   gated on a registered, default-disabled flag.

Scope-aware matching defends against same-name collisions: a start
gate counts only when the gate's variable binding resolves to the
same class as the candidate.

Extracted from :mod:`scripts.check_setting_to_startup_trace` to keep
that module under the 800-line ceiling. Behaviour is unchanged.
"""

import ast
import re
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from _setting_to_startup_trace_loader import (  # type: ignore[import-not-found]
        load_setting_definitions,
    )
    from _setting_to_startup_trace_models import (  # type: ignore[import-not-found]
        _LIFECYCLE_FILES,
        GhostService,
        SettingRecord,
        _FactoryNode,
    )
else:
    from scripts._setting_to_startup_trace_loader import load_setting_definitions
    from scripts._setting_to_startup_trace_models import (
        _LIFECYCLE_FILES,
        GhostService,
        SettingRecord,
        _FactoryNode,
    )


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


def _annotation_class(annotation: ast.expr | None) -> str | None:
    """Return the bare class name in an annotation, or ``None``.

    Recognises ``T``, ``T | None``, and ``Optional[T]``. Returns
    ``None`` for parametrised generics or anything that's not a bare
    Name -- callers treat that as "unknown; fall back to name match".
    """
    if annotation is None:
        return None
    return _extract_class_from_optional(annotation) or _bare_name(annotation)


def _is_default_enabled(default: str | None) -> bool:
    """True iff *default* parses as a True boolean string."""
    if default is None:
        return False
    return default.strip().lower() in ("true", "1", "yes")


# ── Lifecycle file loading ──────────────────────────────────────


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
            "Lifecycle files could not be parsed (fix before proceeding):\n"
            + "\n".join(f"  {err}" for err in parse_errors)
        )
        raise ValueError(msg)
    return trees


# ── Hardcoded-None candidates + scope-aware start-gate matching ──


def _collect_hardcoded_none_candidates(
    tree: ast.Module,
) -> list[tuple[str, str, int]]:
    """Return ``(var_name, class_name, lineno)`` for ``x: T | None = None``.

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
    class_name: str,
    trees_by_path: dict[str, ast.Module],
) -> bool:
    """True iff *var_name* (typed as *class_name*) is used in a start gate.

    Checks every parsed lifecycle/app file for a structural match of
    ``if <var_name> is not None: <var_name>.start()|run()`` AND
    requires the gate's variable binding to resolve to *class_name*.

    The class-name check defends against same-name collisions: if two
    unrelated functions or files both use ``backup_service`` for
    different types, only the binding annotated as ``BackupService``
    counts as the ghost's start gate.

    Falls back to name-only matching when the binding has no class
    annotation -- conservative, prevents false negatives on
    un-annotated test fixtures.
    """
    for tree in trees_by_path.values():
        if _tree_contains_start_gate(tree, var_name, class_name):
            return True
    return False


def _tree_contains_start_gate(
    tree: ast.Module,
    var_name: str,
    class_name: str,
) -> bool:
    """Walk *tree* for ``if x is not None: x.start()|x.run()`` matching *class_name*."""
    parents = _build_parent_map(tree)
    for node in ast.walk(tree):
        if not isinstance(node, ast.If):
            continue
        if not _is_isnot_none(node.test, var_name):
            continue
        if not _gate_var_matches_class(node, var_name, class_name, tree, parents):
            continue
        if _body_calls_start(node.body, var_name):
            return True
    return False


def _build_parent_map(tree: ast.AST) -> dict[int, ast.AST]:
    """Return ``{id(child): parent}`` for every node in *tree*."""
    parents: dict[int, ast.AST] = {}
    for parent_node in ast.walk(tree):
        for child in ast.iter_child_nodes(parent_node):
            parents[id(child)] = parent_node
    return parents


def _find_enclosing_function(
    target: ast.AST,
    parents: dict[int, ast.AST],
) -> _FactoryNode | None:
    """Walk up from *target* via *parents* to the deepest enclosing function."""
    current: ast.AST | None = parents.get(id(target))
    while current is not None:
        if isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return current
        current = parents.get(id(current))
    return None


def _gate_var_matches_class(
    if_node: ast.If,
    var_name: str,
    expected_class: str,
    tree: ast.Module,
    parents: dict[int, ast.AST],
) -> bool:
    """True iff *var_name*'s binding at *if_node* resolves to *expected_class*.

    Checks (in order):

    1. Enclosing function's parameter annotations.
    2. Enclosing function's local ``AnnAssign`` of ``var_name``.
    3. Module-level ``AnnAssign`` of ``var_name``.

    A binding with no annotation, or one that resolves to a
    non-bare class (e.g. parametrised generic), falls back to
    True -- the lint stays conservative when the type isn't
    statically clear, on the grounds that name-only matching is
    the previous-behaviour baseline and false negatives are worse
    than the rare false positive that motivated this check.
    """
    enclosing = _find_enclosing_function(if_node, parents)
    if enclosing is not None:
        all_args = (
            list(enclosing.args.args)
            + list(enclosing.args.posonlyargs)
            + list(enclosing.args.kwonlyargs)
        )
        for arg in all_args:
            if arg.arg != var_name:
                continue
            cls = _annotation_class(arg.annotation)
            return cls is None or cls == expected_class
        for stmt in ast.walk(enclosing):
            if (
                isinstance(stmt, ast.AnnAssign)
                and isinstance(stmt.target, ast.Name)
                and stmt.target.id == var_name
            ):
                cls = _annotation_class(stmt.annotation)
                return cls is None or cls == expected_class
    for stmt in ast.iter_child_nodes(tree):
        if (
            isinstance(stmt, ast.AnnAssign)
            and isinstance(stmt.target, ast.Name)
            and stmt.target.id == var_name
        ):
            cls = _annotation_class(stmt.annotation)
            return cls is None or cls == expected_class
    return True


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
    """Return every hardcoded-None ghost service in lifecycle/app files."""
    trees_by_path = _load_lifecycle_trees(src_root)
    ghosts: dict[str, GhostService] = {}
    for path, tree in trees_by_path.items():
        for var_name, class_name, _line in _collect_hardcoded_none_candidates(tree):
            # Dedup: a class flagged via one site is the same ghost
            # regardless of how many lifecycle files reference it.
            if class_name in ghosts:
                continue
            if not _is_started_at_some_site(var_name, class_name, trees_by_path):
                continue
            ghosts[class_name] = GhostService(
                class_name=class_name,
                kind="hardcoded-none",
                gating_namespace=None,
                source_file=path,
            )
    return list(ghosts.values())


# ── Factory-gated ghosts ────────────────────────────────────────


def _build_import_map(tree: ast.Module) -> dict[str, tuple[str, str]]:
    """Return ``{local_name: (dotted_module, exported_name)}`` for ``from X import Y``.

    Captures both halves of the import so aliased forms
    (``from X import Y as Z``) resolve correctly: the local lookup
    key is the alias (``Z``), but the function definition in ``X``'s
    source is named after the exported symbol (``Y``). Without
    tracking both, :func:`_find_factory_function` would search the
    target module for ``def Z(...)`` and silently miss the factory.
    """
    aliases: dict[str, tuple[str, str]] = {}
    for node in tree.body if isinstance(tree, ast.Module) else ():
        if isinstance(node, ast.ImportFrom) and node.module:
            for alias in node.names:
                aliases[alias.asname or alias.name] = (node.module, alias.name)
    return aliases


def _resolve_module_to_path(module: str, src_root: Path) -> Path | None:
    """Resolve a dotted module path under ``src/synthorg/`` to a ``.py`` file."""
    if not module.startswith("synthorg."):
        return None
    rel = module.removeprefix("synthorg.").replace(".", "/") + ".py"
    candidate = src_root / rel
    if candidate.is_file():
        return candidate
    package_init = (
        src_root / module.removeprefix("synthorg.").replace(".", "/") / "__init__.py"
    )
    if package_init.is_file():
        return package_init
    return None


def _find_factory_function(
    factory_name: str,
    src_root: Path,
    import_map: dict[str, tuple[str, str]],
) -> tuple[_FactoryNode, Path] | None:
    """Locate the factory's ``FunctionDef`` / ``AsyncFunctionDef`` AST node.

    Resolves via the importing file's ``from X import factory_name``
    and parses ``X``'s source. Returns ``None`` if the factory is
    builtin / external or its source can't be parsed. Both sync and
    async factories are returned -- the body inspection
    (``_factory_gating_namespace`` / ``_factory_return_class``) is
    identical for both.

    ``factory_name`` is the name as referenced in the call site (the
    alias when ``from X import Y as Z`` is used). The import map
    records both that local name and the original exported symbol;
    the AST search uses the exported name so aliased imports resolve.
    """
    resolved = import_map.get(factory_name)
    if resolved is None:
        return None
    module, exported_name = resolved
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
            and node.name == exported_name
        ):
            return node, path
    return None


def _factory_gating_namespace(factory: _FactoryNode) -> str | None:
    """Inspect a factory body for an early ``return None`` gate.

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
    """Return ``{local_var: namespace}`` for ``var = <chain>.<ns>`` assignments."""
    aliases: dict[str, str] = {}
    for stmt in body:
        if not isinstance(stmt, ast.Assign):
            continue
        if len(stmt.targets) != 1 or not isinstance(stmt.targets[0], ast.Name):
            continue
        if not isinstance(stmt.value, ast.Attribute):
            continue
        aliases[stmt.targets[0].id] = stmt.value.attr
    return aliases


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
        return parent.attr
    if isinstance(parent, ast.Name):
        return aliases.get(parent.id)
    return None


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


def _factory_return_class(factory: _FactoryNode) -> str | None:
    """Extract bare ``T`` from a ``-> T | None`` factory return annotation."""
    if factory.returns is None:
        return None
    return _extract_class_from_optional(factory.returns)


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
    4. The gating flag MUST be a registered setting AND its
       registered default must explicitly disable the service. A
       missing setting (e.g. an internal feature flag with no
       registry entry) is out of scope -- the lint has no policy
       contract to enforce on flags it doesn't know about.

    ``settings_by_yaml`` is optional -- when omitted, it's loaded
    from ``src_root``. Passing the inventory in lets :func:`scan_repo`
    cache it across the two ghost detectors.
    """
    if settings_by_yaml is None:
        records = load_setting_definitions(src_root / "settings" / "definitions")
        settings_by_yaml = {s.setting_key: s for s in records}
    trees_by_path = _load_lifecycle_trees(src_root)
    ghosts: dict[str, GhostService] = {}
    for path, tree in trees_by_path.items():
        import_map = _build_import_map(tree)
        for var_name, factory_name in _factory_call_assignments(tree):
            located = _find_factory_function(factory_name, src_root, import_map)
            if located is None:
                continue
            factory_node, _factory_path = located
            class_name = _factory_return_class(factory_node)
            if class_name is None:
                continue
            if not _is_started_at_some_site(var_name, class_name, trees_by_path):
                continue
            namespace = _factory_gating_namespace(factory_node)
            if namespace is None:
                continue
            gating_setting = settings_by_yaml.get(f"{namespace}.enabled")
            # Only classify as ghost when there's a REGISTERED setting
            # whose default explicitly disables the service. A factory
            # gated on a non-registered flag is out of scope -- without
            # a registry entry, the lint has no contract to enforce, and
            # treating "missing setting" as "default-disabled" would
            # turn every internal feature flag into a namespace-wide
            # false positive.
            if gating_setting is None or _is_default_enabled(gating_setting.default):
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


# ── Class-file index (for hardcoded-None matching + Pattern A) ──


_TOPLEVEL_CLASS_RE = re.compile(r"(?m)^class\s+\w")


def _build_class_index(src_root: Path) -> dict[str, list[Path]]:
    """Build ``{class_name: [file_paths]}`` for every top-level class.

    Walked once per :func:`scan_repo` invocation to avoid the
    quadratic cost of resolving each ghost's class file individually.
    Multi-mapping (list of paths) lets the caller refuse to guess
    when a class name is ambiguous.

    Files containing no top-level ``class`` (matched by a fast
    line-start regex) are skipped without paying the ``ast.parse``
    cost. Indented ``class`` statements inside functions don't
    participate in the resolver's "find the class file" lookup
    anyway, so the regex's top-of-line anchor is a precise filter.
    """
    index: dict[str, list[Path]] = {}
    for path in src_root.rglob("*.py"):
        try:
            text = path.read_text(encoding="utf-8")
        except OSError, UnicodeDecodeError:
            continue
        if not _TOPLEVEL_CLASS_RE.search(text):
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
