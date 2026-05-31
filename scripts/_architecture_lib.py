"""Shared architecture-metric computation for the feedback loop.

Consumed by ``scripts/architecture_report.py`` (writes the committed
``data/architecture_report.json`` baseline) and
``scripts/check_architecture_drift.py`` (the pre-push gate that recomputes
the same metrics live and fails on a regression past the baseline).
Centralised here so the report and the gate cannot drift on how a metric
is defined.

Three metrics, each a distinct shape of architectural pressure:

* **fan-in** -- how many modules directly import a given module. A high,
  rising fan-in marks a module the whole codebase is coupling to (a
  hub). Computed from the ``grimp`` import graph.
* **budget pressure** -- source files whose LOC is within 20% of their
  module-size tier cap. These pass the size gate today but are the next
  files to breach it; the drift gate fails when one keeps growing.
* **LCOM4** -- lack-of-cohesion for large service classes (>= 400 LOC).
  Methods are nodes; two methods are connected when they share an
  instance attribute or one calls the other. LCOM4 is the number of
  connected components: 1 is cohesive, >= 2 means the class hosts
  unrelated responsibilities that want splitting.
"""

import ast
import importlib.util
from pathlib import Path
from types import ModuleType
from typing import Any, Final, cast

ROOT_PACKAGE: Final[str] = "synthorg"
FAN_IN_RECORD_FLOOR: Final[int] = 20
FAN_IN_FAIL_THRESHOLD: Final[int] = 30
# A hub may absorb a few new importers before the drift gate fires; only a
# jump well past the baseline (or a brand-new >= 30 hub) signals real
# coupling growth. Tuned so foundation modules (core.types, observability)
# tolerate routine churn but a synthetic +50 fan-in fails.
FAN_IN_DRIFT_TOLERANCE: Final[int] = 5
BUDGET_PRESSURE_RATIO: Final[float] = 0.8
LCOM_SERVICE_MIN_LOC: Final[int] = 400
# A class with LCOM4 above this hosts more than one cohesive responsibility.
LCOM_COHESIVE_MAX: Final[int] = 1
_LCOM_TIERS: Final[frozenset[str]] = frozenset(
    {"service", "orchestrator", "complex_service"}
)
_SCAN_REL: Final[Path] = Path("src") / "synthorg"


def _load_module_size_lib() -> ModuleType:
    lib_path = Path(__file__).resolve().parent / "_module_size_lib.py"
    spec = importlib.util.spec_from_file_location("_module_size_lib", lib_path)
    if spec is None or spec.loader is None:
        msg = f"could not load module spec for {lib_path}"
        raise RuntimeError(msg)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_SIZE_LIB: Any = cast("Any", _load_module_size_lib())


def compute_fan_in(*, record_floor: int = FAN_IN_RECORD_FLOOR) -> dict[str, int]:
    """Return ``{module: direct_importer_count}`` for hub modules.

    Only modules whose fan-in is at or above *record_floor* are returned,
    keeping the committed baseline bounded to the modules worth watching.

    Returns:
        Mapping of dotted module name to direct-importer count, sorted by
        key, filtered to fan-in >= *record_floor*.
    """
    import grimp

    graph = grimp.build_graph(ROOT_PACKAGE)
    counts: dict[str, int] = {}
    for module in graph.modules:
        fan_in = len(graph.find_modules_that_directly_import(module))
        if fan_in >= record_floor:
            counts[module] = fan_in
    return dict(sorted(counts.items()))


def _iter_source_files(project_root: Path) -> list[Path]:
    scan_root = project_root / _SCAN_REL
    if not scan_root.is_dir():
        return []
    return sorted(scan_root.rglob("*.py"))


def compute_budget_pressure(project_root: Path) -> dict[str, dict[str, object]]:
    """Return files within 20% of their tier cap.

    Returns:
        Mapping of POSIX repo-relative path to ``{tier, loc, cap, ratio}``
        for every file whose ``loc / cap`` is at or above
        :data:`BUDGET_PRESSURE_RATIO`. Declarative / generated (capless)
        files are skipped.
    """
    result: dict[str, dict[str, object]] = {}
    for path in _iter_source_files(project_root):
        tier = _SIZE_LIB.resolve_tier(path, project_root=project_root)
        cap = _SIZE_LIB.TIER_LIMITS[tier]
        if cap is None:
            continue
        loc = _SIZE_LIB.count_loc(path)
        ratio = loc / cap
        if ratio >= BUDGET_PRESSURE_RATIO:
            rel = path.relative_to(project_root).as_posix()
            result[rel] = {
                "tier": tier,
                "loc": loc,
                "cap": cap,
                "ratio": round(ratio, 3),
            }
    return dict(sorted(result.items()))


def _class_loc(node: ast.ClassDef, lines: list[str]) -> int:
    start = node.lineno - 1
    end = node.end_lineno or node.lineno
    body = lines[start:end]
    return sum(1 for line in body if line.strip() and not line.lstrip().startswith("#"))


def _method_touches(method: ast.FunctionDef | ast.AsyncFunctionDef) -> set[str]:
    """Return the ``self.X`` attribute/method names a method references.

    Nested class definitions are pruned from the walk: inside a nested
    class ``self`` rebinds to that class's instance, so its attribute
    accesses must not count toward the enclosing method's touches (which
    would corrupt the LCOM4 cohesion metric). Nested functions / closures
    are deliberately not pruned because they still capture the enclosing
    method's ``self``.
    """
    touched: set[str] = set()

    def _collect(node: ast.AST) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.ClassDef):
                continue
            if (
                isinstance(child, ast.Attribute)
                and isinstance(child.value, ast.Name)
                and child.value.id == "self"
            ):
                touched.add(child.attr)
            _collect(child)

    _collect(method)
    return touched


def _lcom4(cls: ast.ClassDef) -> int:
    """Compute LCOM4 (connected-component count) for a class.

    Methods are nodes; an edge joins two methods that share a ``self``
    attribute or where one calls the other. Returns the number of
    connected components (1 = cohesive). A class with 0 or 1 methods
    scores 1.
    """
    methods = [
        m
        for m in cls.body
        if isinstance(m, ast.FunctionDef | ast.AsyncFunctionDef)
        and not (m.name.startswith("__") and m.name.endswith("__"))
    ]
    if len(methods) <= 1:
        return 1
    names = [m.name for m in methods]
    touches = {m.name: _method_touches(m) for m in methods}
    parent: dict[str, str] = {n: n for n in names}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: str, b: str) -> None:
        parent[find(a)] = find(b)

    name_set = set(names)
    for i, mi in enumerate(methods):
        for mj in methods[i + 1 :]:
            shared_attr = bool((touches[mi.name] & touches[mj.name]) - name_set)
            calls = (mj.name in touches[mi.name]) or (mi.name in touches[mj.name])
            if shared_attr or calls:
                union(mi.name, mj.name)
    return len({find(n) for n in names})


def compute_lcom(project_root: Path) -> dict[str, dict[str, int]]:
    """Return LCOM4 for service-tier classes that are >= 400 LOC.

    Returns:
        Mapping of ``module:ClassName`` to ``{loc, lcom4}`` for every
        class in a service / orchestrator / complex_service file whose
        class body is at or above :data:`LCOM_SERVICE_MIN_LOC`.
    """
    result: dict[str, dict[str, int]] = {}
    for path in _iter_source_files(project_root):
        tier = _SIZE_LIB.resolve_tier(path, project_root=project_root)
        if tier not in _LCOM_TIERS:
            continue
        text = path.read_text(encoding="utf-8")
        lines = text.splitlines()
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue
        rel = path.relative_to(project_root).as_posix()
        dotted = rel.removeprefix("src/").removesuffix(".py").replace("/", ".")
        for node in tree.body:
            if not isinstance(node, ast.ClassDef):
                continue
            loc = _class_loc(node, lines)
            if loc < LCOM_SERVICE_MIN_LOC:
                continue
            result[f"{dotted}:{node.name}"] = {"loc": loc, "lcom4": _lcom4(node)}
    return dict(sorted(result.items()))


def build_report(project_root: Path) -> dict[str, Any]:
    """Assemble the full architecture-metrics report.

    Returns:
        The report payload: ``{fan_in, budget_pressure, lcom}``.
    """
    return {
        "fan_in": compute_fan_in(),
        "budget_pressure": compute_budget_pressure(project_root),
        "lcom": compute_lcom(project_root),
    }
