"""Backend route inventory: AST walk every registered Litestar route.

Source-of-truth model:

1. ``src/synthorg/api/controllers/__init__.py`` declares three tuples
   (``BASE_CONTROLLERS``, ``OPTIONAL_CONTROLLERS``,
   ``INTEGRATION_CONTROLLERS``) and a union ``ALL_CONTROLLERS``.
   Every controller class mentioned in any of these is treated as
   "registered" -- conditional gating
   (``app_state.has_*`` / ``effective_config.integrations.enabled``)
   is intentionally ignored to avoid false-positive
   "dead endpoint" findings for optional features.

2. ``src/synthorg/api/app.py`` registers two A2A controllers gated by
   ``effective_config.a2a.enabled``: ``WellKnownAgentCardController``
   (mounted at the app root, NOT under the API prefix) and
   ``A2AGatewayController`` (mounted under the API prefix). These
   imports live inside an ``if`` block; we walk the file's AST and
   collect any ``ImportFrom`` whose module starts with
   ``synthorg.a2a``.

3. ``src/synthorg/api/controllers/ws.py`` carries the
   ``@websocket("/ws", ...)`` handler. It is mounted under the API
   prefix.

For each discovered controller class, we walk its module, find the
``path = "..."`` class attribute, and collect every method-level
HTTP-verb decorator (``@get/@post/@put/@patch/@delete``) along with
its path argument (positional, kwarg ``path=...``, or empty -- empty
maps to the controller's own ``path`` only). Every decorator on a
single method emits a separate route record.

Path-param normalisation (``{name:str}`` -> ``{*}``) happens via
:func:`scripts._dead_api_endpoints_models.normalise_path` at insertion
time so every subsequent comparison is a plain string compare.
"""

import ast
import sys
from pathlib import Path
from typing import Final

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from _dead_api_endpoints_models import (  # type: ignore[import-not-found]
        RouteRecord,
        normalise_path,
    )
else:
    from scripts._dead_api_endpoints_models import RouteRecord, normalise_path

# ── Constants ──────────────────────────────────────────────────

_HTTP_DECORATORS: Final[frozenset[str]] = frozenset(
    {"get", "post", "put", "patch", "delete"}
)
"""Method names in :class:`litestar.handlers` that mark an HTTP route.
``head`` / ``options`` are intentionally omitted -- the codebase does
not use them and gating on a closed set keeps false matches
(non-Litestar decorators with one of these names) from registering
routes."""

_WEBSOCKET_DECORATORS: Final[frozenset[str]] = frozenset(
    {"websocket", "websocket_listener"}
)
"""WebSocket decorators: ``@websocket(...)`` for module-level
handlers (``ws_handler``) and ``@websocket_listener(...)`` for the
listener-style API. The codebase currently only uses the former."""

_DEFAULT_API_PREFIX: Final[str] = "/api/v1"


# ── AST helpers ────────────────────────────────────────────────


def _read_module(path: Path) -> ast.Module | None:
    """Parse *path* into an :class:`ast.Module`, returning None on failure.

    Failures (OSError, UnicodeDecodeError, SyntaxError) are surfaced as a
    one-line warning to stderr so a broken controller file is visible to
    the operator -- otherwise a single syntax error would silently empty
    the route inventory and turn every frontend call into a HIGH violation.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        print(
            f"check_dead_api_endpoints: cannot read {path}: "
            f"{type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        return None
    try:
        return ast.parse(text, filename=str(path))
    except SyntaxError as exc:
        print(
            f"check_dead_api_endpoints: cannot parse {path}: {exc.msg} "
            f"(line {exc.lineno})",
            file=sys.stderr,
        )
        return None


def _resolve_string(node: ast.expr) -> str | None:
    """Return the literal string value of *node* if statically resolvable.

    Recognises plain :class:`ast.Constant` strings and the
    no-arg / single-arg form of f-strings whose only segment is a
    constant. Anything else (variable, function call) returns ``None``
    so the caller can fall back to "no static path" handling.
    """
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        parts: list[str] = []
        for v in node.values:
            if isinstance(v, ast.Constant) and isinstance(v.value, str):
                parts.append(v.value)
            else:
                return None
        return "".join(parts)
    return None


def _decorator_method_and_path(
    deco: ast.expr,
) -> tuple[str, str] | None:
    """Return ``(method, path)`` for an HTTP / WebSocket decorator.

    Recognises:

    - ``@get`` / ``@post`` -- bare reference, no call. Path is empty.
    - ``@get()`` / ``@post()`` -- empty call. Path is empty.
    - ``@get("/x")`` -- positional path.
    - ``@get(path="/x")`` -- kwarg path.
    - ``@websocket("/ws")`` -- positional path.

    Method names are uppercased; WebSocket is reported as ``WS``.
    Returns ``None`` for any decorator shape that is not an HTTP/WS
    route handler.
    """
    func: ast.expr | None
    args: list[ast.expr] = []
    keywords: list[ast.keyword] = []
    if isinstance(deco, ast.Call):
        func = deco.func
        args = list(deco.args)
        keywords = list(deco.keywords)
    else:
        func = deco

    if isinstance(func, ast.Name):
        name = func.id
    elif isinstance(func, ast.Attribute):
        name = func.attr
    else:
        return None

    if name in _HTTP_DECORATORS:
        method = name.upper()
    elif name in _WEBSOCKET_DECORATORS:
        method = "WS"
    else:
        return None

    path: str | None = None
    if args:
        path = _resolve_string(args[0])
    if path is None:
        for kw in keywords:
            if kw.arg == "path":
                path = _resolve_string(kw.value)
                break
    if path is None:
        path = ""
    return method, path


def _class_path_attribute(class_node: ast.ClassDef) -> str:
    """Return the value of the ``path = "..."`` class attribute or ``""``."""
    for stmt in class_node.body:
        if isinstance(stmt, ast.Assign):
            for target in stmt.targets:
                if isinstance(target, ast.Name) and target.id == "path":
                    resolved = _resolve_string(stmt.value)
                    if resolved is not None:
                        return resolved
        elif (
            isinstance(stmt, ast.AnnAssign)
            and isinstance(stmt.target, ast.Name)
            and stmt.target.id == "path"
            and stmt.value is not None
        ):
            resolved = _resolve_string(stmt.value)
            if resolved is not None:
                return resolved
    return ""


def _is_controller_subclass(class_node: ast.ClassDef) -> bool:
    """Heuristic: bases mention ``Controller`` somewhere in their chain."""
    for base in class_node.bases:
        if isinstance(base, ast.Name) and base.id == "Controller":
            return True
        if isinstance(base, ast.Attribute) and base.attr == "Controller":
            return True
    return False


# ── Tuple extraction ───────────────────────────────────────────


def _extract_controller_names_from_tuples(tree: ast.Module) -> set[str]:
    """Return controller class names referenced in the registration tuples.

    Walks every module-level assignment whose target name is one of
    ``BASE_CONTROLLERS`` / ``OPTIONAL_CONTROLLERS`` /
    ``INTEGRATION_CONTROLLERS`` / ``ALL_CONTROLLERS`` (annotated or
    plain) and collects every :class:`ast.Name` reference found in
    the right-hand-side tuple/starred-tuple expression. Tuple-of-tuple
    shapes (``OPTIONAL_CONTROLLERS = ((Class, "predicate"), ...)``)
    are flattened.
    """
    targets = {
        "BASE_CONTROLLERS",
        "OPTIONAL_CONTROLLERS",
        "INTEGRATION_CONTROLLERS",
        "ALL_CONTROLLERS",
    }
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Assign):
            target_names = {
                t.id
                for t in node.targets
                if isinstance(t, ast.Name) and t.id in targets
            }
            if target_names:
                _collect_names(node.value, names)
        elif (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id in targets
            and node.value is not None
        ):
            _collect_names(node.value, names)
    return names


def _collect_names(node: ast.expr, sink: set[str]) -> None:
    """Recursively collect every :class:`ast.Name` id under *node*."""
    for child in ast.walk(node):
        if isinstance(child, ast.Name):
            sink.add(child.id)


def _resolve_imports(tree: ast.Module) -> dict[str, Path]:
    """Map ``{class_name: source_file_relative_to_src_synthorg}``.

    Walks every ``from synthorg.<dotted> import X[, Y]`` line and
    records, for each name imported, the .py file the import would
    resolve to. The dotted module is converted to a path; the
    resulting :class:`pathlib.Path` is RELATIVE to the project root.
    Names imported from outside ``synthorg.`` are skipped.
    """
    out: dict[str, Path] = {}
    for node in tree.body:
        if not isinstance(node, ast.ImportFrom) or not node.module:
            continue
        if not node.module.startswith("synthorg."):
            continue
        rel_path = Path("src") / Path(*node.module.split("."))
        candidate = rel_path.with_suffix(".py")
        for alias in node.names:
            local = alias.asname or alias.name
            out[local] = candidate
    return out


# ── Orchestration ──────────────────────────────────────────────


def _walk_controller_module(
    module_path: Path,
    project_root: Path,
    api_prefix: str,
    *,
    strip_api_prefix: bool,
) -> list[RouteRecord]:
    """Return every route declared inside one controller-bearing module.

    Module-level handlers (``@websocket("/ws")``) are detected too
    -- the function isn't required to live inside a ``Controller``
    subclass.
    """
    abs_path = module_path if module_path.is_absolute() else project_root / module_path
    tree = _read_module(abs_path)
    if tree is None:
        return []
    rel = abs_path.relative_to(project_root).as_posix()
    routes: list[RouteRecord] = []
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and _is_controller_subclass(node):
            class_path = _class_path_attribute(node)
            controller_name = node.name
            for inner in node.body:
                if isinstance(inner, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    for deco in inner.decorator_list:
                        match = _decorator_method_and_path(deco)
                        if match is None:
                            continue
                        method, deco_path = match
                        full = _join_paths(
                            api_prefix if strip_api_prefix else "",
                            class_path,
                            deco_path,
                            strip_api_prefix=strip_api_prefix,
                        )
                        routes.append(
                            RouteRecord(
                                method=method,
                                path=normalise_path(full),
                                controller_name=controller_name,
                                source_file=rel,
                                source_line=inner.lineno,
                            )
                        )
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for deco in node.decorator_list:
                match = _decorator_method_and_path(deco)
                if match is None or match[0] != "WS":
                    # Only module-level WS handlers are interesting;
                    # bare module-level @get/@post would already be
                    # picked up if Litestar recognised them, but the
                    # codebase mounts those through controllers.
                    continue
                method, deco_path = match
                full = _join_paths(
                    api_prefix if strip_api_prefix else "",
                    "",
                    deco_path,
                    strip_api_prefix=strip_api_prefix,
                )
                routes.append(
                    RouteRecord(
                        method=method,
                        path=normalise_path(full),
                        controller_name=node.name,
                        source_file=rel,
                        source_line=node.lineno,
                    )
                )
    return routes


def _join_paths(
    api_prefix: str,
    class_path: str,
    deco_path: str,
    *,
    strip_api_prefix: bool,
) -> str:
    """Compose ``<api_prefix><class_path><deco_path>`` and strip the prefix.

    Frontend URLs are written relative to ``apiClient.baseURL`` which
    Axios appends ``/api/v1`` to. Backend routes carry the prefix
    explicitly via Litestar's :class:`Router`. We strip the prefix on
    the backend side so both halves of the comparator share a
    coordinate system.

    For controllers mounted at the app root (``WellKnownAgentCardController``
    on ``/.well-known``), the caller passes ``strip_api_prefix=False``
    AND ``api_prefix=""`` so no strip happens.
    """
    full = (api_prefix or "") + (class_path or "") + (deco_path or "")
    if not full.startswith("/"):
        full = "/" + full
    # Collapse duplicate slashes that arise when class_path == "/" or
    # deco_path == "/".
    while "//" in full:
        full = full.replace("//", "/")
    if strip_api_prefix and api_prefix and full.startswith(api_prefix):
        stripped = full[len(api_prefix) :]
        return stripped or "/"
    return full


def _find_a2a_controllers(
    app_module: ast.Module,
) -> list[tuple[str, Path, bool]]:
    """Return ``(class_name, source_file, mounted_under_api_prefix)`` triples.

    Walks the A2A registration block in ``api/app.py``. Two controller
    families surface there:

    - ``WellKnownAgentCardController`` from ``synthorg.a2a.well_known``
      -- mounted at the app root (``/.well-known``); not under the
      API prefix.
    - ``A2AGatewayController`` from ``synthorg.a2a.gateway`` -- mounted
      under the API prefix via ``api_router``.

    The ``synthorg.a2a.well_known`` import is the discriminator for
    "root-mounted"; everything else from ``synthorg.a2a`` is treated
    as API-prefix-mounted.
    """
    out: list[tuple[str, Path, bool]] = []
    for node in ast.walk(app_module):
        if not isinstance(node, ast.ImportFrom):
            continue
        if not node.module or not node.module.startswith("synthorg.a2a"):
            continue
        rel_path = Path("src") / Path(*node.module.split("."))
        candidate = rel_path.with_suffix(".py")
        is_well_known = node.module == "synthorg.a2a.well_known"
        for alias in node.names:
            local = alias.asname or alias.name
            if local.endswith("Controller"):
                out.append((local, candidate, not is_well_known))
    return out


def collect_backend_routes(
    project_root: Path,
    api_prefix: str = _DEFAULT_API_PREFIX,
) -> list[RouteRecord]:
    """Return every backend route registered by the Litestar app.

    Args:
        project_root: Repo root containing ``src/synthorg/``.
        api_prefix: API prefix to strip from controller routes
            (default ``/api/v1``). Pass ``""`` to keep the prefix in
            the comparator coordinate system (rare; testing only).

    Returns:
        A list of :class:`RouteRecord` covering every controller
        registered in the controllers/__init__.py tuples plus the
        A2A registration block plus the module-level WS handler.
    """
    src_root = project_root / "src" / "synthorg"
    init_path = src_root / "api" / "controllers" / "__init__.py"
    init_tree = _read_module(init_path)
    if init_tree is None:
        # The init file is the route-inventory source of truth -- if we
        # can't parse it, every backend route is missing and every
        # frontend call would falsely report as a dead endpoint. Raise
        # so the CLI's exit-code-2 path fires instead of a misleading
        # exit-1 violation cascade. ``_read_module`` already wrote a
        # specific stderr line describing the failure.
        msg = (
            f"cannot read controller registration file {init_path}; "
            "see preceding stderr line for the parse error"
        )
        raise ValueError(msg)

    # Step 1: discover controller class names and where they live.
    referenced = _extract_controller_names_from_tuples(init_tree)
    imports = _resolve_imports(init_tree)

    # Auth controller is imported via ``synthorg.api.auth.controller``
    # in __init__.py, so the import map already covers it. ws_handler
    # comes from synthorg.api.controllers.ws and needs walking
    # explicitly because it's a module-level handler, not a class.
    routes: list[RouteRecord] = []
    seen_modules: set[Path] = set()
    for name in referenced:
        module_rel = imports.get(name)
        if module_rel is None:
            continue
        if module_rel in seen_modules:
            continue
        seen_modules.add(module_rel)
        routes.extend(
            _walk_controller_module(
                module_rel,
                project_root,
                api_prefix,
                strip_api_prefix=True,
            )
        )

    # Step 2: WS handler (module-level @websocket).
    ws_module = Path("src/synthorg/api/controllers/ws.py")
    if ws_module not in seen_modules:
        seen_modules.add(ws_module)
        routes.extend(
            _walk_controller_module(
                ws_module,
                project_root,
                api_prefix,
                strip_api_prefix=True,
            )
        )

    # Step 3: A2A controllers (gated by effective_config.a2a.enabled).
    app_path = src_root / "api" / "app.py"
    app_tree = _read_module(app_path)
    if app_tree is not None:
        for class_name, module_rel, mounted_under_prefix in _find_a2a_controllers(
            app_tree
        ):
            if module_rel in seen_modules:
                continue
            seen_modules.add(module_rel)
            routes.extend(
                _walk_controller_module(
                    module_rel,
                    project_root,
                    api_prefix if mounted_under_prefix else "",
                    strip_api_prefix=mounted_under_prefix,
                )
            )
            # We collected every controller class in module_rel above,
            # not just *class_name* -- typical A2A modules carry one
            # controller per file so this is what we want; if they
            # ever carry multiple, the walker still produces one
            # record per Controller subclass.
            del class_name  # silence unused-warning; class_name is the discriminator

    return routes
