"""Settings inventory loader for the bootstrap-wiring trace lint.

Walks ``src/synthorg/settings/definitions/`` and extracts every
``_r.register(SettingDefinition(...))`` call as a
:class:`SettingRecord`. Suppression markers on the registration's
closing line are detected via :func:`_line_has_trailing_marker`.

Extracted from :mod:`scripts.check_setting_to_startup_trace` to keep
that module under the 800-line ceiling. Behaviour is unchanged.
"""

import ast
import sys
from pathlib import Path

# Sibling-import dance, mirroring scripts/check_web_design_system.py.
# When this module is loaded standalone (the CLI script imports it as
# a sibling) we extend ``sys.path`` so the ``_setting_to_startup_trace_models``
# import resolves; when imported as part of the ``scripts`` package
# (e.g. by tests that load the CLI shim via importlib), the
# ``scripts.`` prefix is used instead.
if __package__ in {None, ""}:  # standalone invocation
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from _setting_to_startup_trace_models import (  # type: ignore[import-not-found]
        SettingRecord,
        _line_has_trailing_marker,
    )
else:
    from scripts._setting_to_startup_trace_models import (
        SettingRecord,
        _line_has_trailing_marker,
    )


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
    setting_key = f"{namespace}.{key}"
    has_suppression = _detect_register_suppression(
        defn_call,
        file_lines=file_lines,
    )
    return SettingRecord(
        namespace=namespace,
        key=key,
        setting_key=setting_key,
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
    line. Look at end_lineno through end_lineno+3 to land on the
    closing ``)`` of the surrounding ``register(...)`` -- tighter
    than that and unusual formatting trips us up; looser and we'd
    risk picking up an unrelated marker on the next registration.
    """
    end_line = getattr(defn_call, "end_lineno", defn_call.lineno) or defn_call.lineno
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
            "Settings definitions could not be parsed (fix before proceeding):\n"
            + "\n".join(f"  {err}" for err in parse_errors)
        )
        raise ValueError(msg)
    return records
