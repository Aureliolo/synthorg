"""Violation matchers, Pattern A, scan_repo + baseline I/O.

The three matchers are run by :func:`_detect_violation` in fixed
order (first hit wins):

1. **Factory-gated namespace match** -- every setting in the gating
   namespace flags when the factory's gating flag is registered as
   default-disabled.
2. **Hardcoded-None class-file containment match** -- for
   hardcoded-None ghosts, a setting flags iff its key appears as a
   substring in the ghost class's source file AND its namespace
   appears in that file's path.
3. **Pattern A direct ConfigResolver match** -- the ghost class file
   contains ``ConfigResolver.get_*("<ns>", "<key>")`` matching a
   registered setting. Catches cross-namespace consumption that the
   first two matchers miss.

Extracted from :mod:`scripts.check_setting_to_startup_trace` to keep
that module under the 800-line ceiling. Behaviour is unchanged.
"""

import ast
import sys
from pathlib import Path
from typing import Final

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from _setting_to_startup_trace_ghosts import (  # type: ignore[import-not-found]
        _build_class_index,
        _is_default_enabled,
        _resolve_class_file,
        find_factory_gated_ghosts,
        find_hardcoded_none_ghosts,
    )
    from _setting_to_startup_trace_loader import (  # type: ignore[import-not-found]
        load_setting_definitions,
    )
    from _setting_to_startup_trace_models import (  # type: ignore[import-not-found]
        _BASELINE_FIELDS,
        _BASELINE_HEADER,
        GhostService,
        SettingRecord,
        Violation,
    )
else:
    from scripts._setting_to_startup_trace_ghosts import (
        _build_class_index,
        _is_default_enabled,
        _resolve_class_file,
        find_factory_gated_ghosts,
        find_hardcoded_none_ghosts,
    )
    from scripts._setting_to_startup_trace_loader import load_setting_definitions
    from scripts._setting_to_startup_trace_models import (
        _BASELINE_FIELDS,
        _BASELINE_HEADER,
        GhostService,
        SettingRecord,
        Violation,
    )


# ── Pattern A: ConfigResolver consumer discovery in ghost classes ──


_RESOLVER_GET_METHODS: Final[frozenset[str]] = frozenset(
    {"get", "get_int", "get_float", "get_bool", "get_str", "get_enum", "get_json"}
)
"""ConfigResolver scalar-accessor method names. Composed-config readers
(``get_api_config`` etc.) are intentionally excluded -- they fan out
to many settings and Pattern A is meant to catch direct point reads,
not config-object assembly."""


_RESOLVER_MIN_ARGS: Final[int] = 2
"""Minimum positional arg count for a recognised
``ConfigResolver.get_*(namespace, key)`` call."""


_RESOLVER_RECEIVER_NAMES: Final[frozenset[str]] = frozenset(
    {"config_resolver", "configresolver", "resolver", "_config_resolver", "_resolver"}
)
"""Receiver-name vocabulary for ``ConfigResolver`` references in ghost
class files. Pattern A only fires when the call's receiver matches
one of these (e.g. ``self.config_resolver.get_int(...)``,
``app_state.config_resolver.get_*``, or ``ConfigResolver.get_*``);
unrelated helpers like ``client.get_bool("a", "b")`` are skipped.
Class-name match is case-insensitive."""


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


def _is_resolver_receiver(value: ast.expr) -> bool:
    """True iff *value* looks like a ``ConfigResolver`` reference.

    Recognised shapes:

    - ``ConfigResolver.get_*(...)`` -- ``Name(id="ConfigResolver")``.
    - ``self.config_resolver.get_*(...)`` -- ``Attribute(attr="config_resolver")``
      (or any of the names in :data:`_RESOLVER_RECEIVER_NAMES`).
    - ``app_state.config_resolver.get_*(...)`` -- nested attribute,
      same terminal-attr check.
    - Bare ``Name(id="config_resolver")`` -- module-level or
      function-parameter binding by convention.

    The check is intentionally permissive on the receiver chain
    (any depth) but strict on the terminal identifier so that
    unrelated helpers (``client.get_bool(...)``, ``store.get(...)``)
    don't get treated as ConfigResolver reads.
    """
    if isinstance(value, ast.Name):
        return value.id.lower() in _RESOLVER_RECEIVER_NAMES
    if isinstance(value, ast.Attribute):
        return value.attr.lower() in _RESOLVER_RECEIVER_NAMES
    return False


def _find_resolver_consumers_in_file(path: Path) -> list[tuple[str, str]]:
    """Return every ``ConfigResolver.get_*("<ns>", "<key>")`` (ns, key) pair.

    Walks the file's AST for any ``Call(Attribute(attr=∈ get_methods))``
    whose receiver is recognised as a ConfigResolver shape (see
    :func:`_is_resolver_receiver`) and whose first two positional
    args resolve to string values via :func:`_resolve_resolver_arg`.
    Calls with dynamic args, missing args, non-method shapes, or
    receivers that don't look like a ConfigResolver are skipped.

    Receiver validation is required because the gate blocks pushes:
    a permissive method-name match would treat unrelated helpers
    (``client.get_bool("api", "x")``) as config reads and turn
    arbitrary strings into ghost-wired-setting flags.
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
        if not _is_resolver_receiver(node.func.value):
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


# ── Factory-gated + hardcoded-None violation builders ──────────


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
    in the same namespace; if not present, the ghost is out of scope
    (no registered policy to enforce against).
    """
    if ghost.gating_namespace is None:
        return None
    if setting.namespace != ghost.gating_namespace:
        return None
    gating_yaml = f"{ghost.gating_namespace}.enabled"
    gating_setting = settings_by_yaml.get(gating_yaml)
    # Only flag when there's a REGISTERED gating setting whose default
    # explicitly disables the service. Missing-from-registry is treated
    # as "out of scope" rather than "default-disabled" -- without an
    # entry, the lint has no policy contract to enforce on that flag.
    if gating_setting is None or _is_default_enabled(gating_setting.default):
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

    Exception handler below uses the unparenthesized ``except A, B:``
    form (PEP 758, https://peps.python.org/pep-0758/, Python 3.14).
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

    1. Factory-gated namespace match.
    2. Hardcoded-None class-file containment.
    3. Pattern A direct ConfigResolver consumption -- runs after the
       more-specific matchers so each setting maps to exactly one
       violation in the baseline.

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


# ── Baseline I/O ───────────────────────────────────────────────


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


def write_baseline(violations: list[Violation], path: Path) -> None:
    """Overwrite the baseline file with sorted current-violation keys."""
    body = _BASELINE_HEADER + "\n".join(v.baseline_key() for v in violations) + "\n"
    path.write_text(body, encoding="utf-8")
