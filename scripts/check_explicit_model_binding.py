"""Gate: a shipped model is always an operator-chosen ``(provider, model)`` pair.

A provider in SynthOrg is a registered *connection*, carrying its own
credentials, endpoint and quota, so the same model id reached through two of
them is two different calls, billed and rate-limited separately. A bare model
id therefore names no dispatch target, and neither does a placeholder one.

Two rules, both AST-checked over ``src/synthorg/``:

1. **No placeholder value ships.** The vendor-neutral placeholder vocabulary
   -- ``example-`` or ``test-`` followed by ``provider``, ``model``,
   ``embedding``, ``large``, ``medium`` or ``small``, with any suffix -- exists
   to write documentation and tests with. Those tails are the whole list, not
   an illustration: the bare prefixes are ordinary English elsewhere in the
   tree, so a placeholder minted outside the list is invisible here and must
   be added to ``_PLACEHOLDER_RE`` in the change that introduces it. A
   placeholder must never become a *value* the product runs on: a field
   default, a dict entry, a returned constant, a lambda body. Only
   documentation positions may name one -- a docstring, or a ``description`` /
   ``examples`` / ``note`` / ``title`` / ``help`` keyword whose whole job is
   to show the reader the shape.

2. **No bare model default.** A ``model`` / ``*_model`` / ``*_model_id``
   Pydantic field, or a ``SettingDefinition`` under
   ``src/synthorg/settings/definitions/`` whose key is model-shaped, must
   default to blank. A non-blank string default is half a dispatch target: it
   names an id with no connection, so it resolves at runtime against whichever
   client the caller happened to hold.

There is deliberately no per-line opt-out and no baseline. Both rules describe
what the product may contain, not a style preference, and an escape hatch is
exactly how the placeholders reached nine production call sites the first time.
A genuine exception is expressed by changing the shape -- read the pair from a
``MODEL_REF`` setting -- not by silencing the check.

Usage:
    uv run python scripts/check_explicit_model_binding.py

Exit codes:
    0 -- clean.
    1 -- a placeholder value or a bare model default was found.
    2 -- configuration error (bad ``--repo-root`` or an unreadable source file).
"""

import argparse
import ast
import re
import sys
from pathlib import Path
from typing import Final

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from _gate_source import (  # type: ignore[import-not-found]
        GateSourceError,
        read_and_parse,
    )
else:
    from scripts._gate_source import GateSourceError, read_and_parse

_SRC_REL: Final[str] = "src/synthorg"
_DEFINITIONS_REL: Final[str] = "src/synthorg/settings/definitions"

#: The vendor-neutral placeholder vocabulary, matched anywhere in a string
#: (a placeholder embedded in ``"example-provider/model"`` counts) and with
#: any suffix (``example-expert-001``). The tails are enumerated rather than
#: taken as "anything after ``example-`` / ``test-``", because that prefix is
#: also ordinary English: ``test-strategy``, ``build-test-validation`` and
#: ``https://api.example-deploy.com`` all appear in the tree as real values,
#: and a pattern that failed the build on those would be removed rather than
#: obeyed. The list is therefore the contract: a placeholder outside it is
#: invisible to this gate, so a new one goes here in the same change that
#: introduces it.
_PLACEHOLDER_RE: Final[re.Pattern[str]] = re.compile(
    r"\b(?:example|test)-(?:provider|model|embedding|large|medium|small)\b"
)

#: Keyword arguments whose whole purpose is to show a reader the shape of a
#: value. A placeholder is the correct thing to write in one.
_DOC_KEYWORDS: Final[frozenset[str]] = frozenset(
    {"description", "examples", "note", "title", "help"}
)


def _is_model_name(name: str) -> bool:
    """Whether *name* is a model-shaped field/key name.

    Returns:
        ``True`` when the name is ``model`` or ends in ``_model`` /
        ``_model_id``.
    """
    return name == "model" or name.endswith(("_model", "_model_id"))


def _literal_str(value: ast.expr) -> str | None:
    """Return the string literal *value* names, unwrapping a wrapper call.

    Handles a bare ``"..."`` and a single-arg wrapper such as
    ``NotBlankStr("...")``.

    Returns:
        The literal string, or ``None`` when *value* is not one.
    """
    if isinstance(value, ast.Constant):
        return value.value if isinstance(value.value, str) else None
    if (
        isinstance(value, ast.Call)
        and value.args
        and isinstance(value.args[0], ast.Constant)
        and isinstance(value.args[0].value, str)
    ):
        return value.args[0].value
    return None


def _string_default(value: ast.expr) -> str | None:
    """Return the non-blank string default of *value*, or ``None``.

    Handles a bare literal, a wrapper (``NotBlankStr("...")``), and a
    ``Field(default=...)`` / ``SettingDefinition(default=...)`` /
    ``Field("...")`` call (with the default itself possibly wrapped).

    A ``default_factory=lambda: "..."`` is the same default reached through
    a callable, so it is read the same way: leaving it out would let a
    model-shaped field ship a baked id past a gate with no opt-out and no
    baseline, where a silent miss is the only failure mode.

    Returns:
        The non-blank default string, or ``None``.
    """
    literal = _literal_str(value)
    if literal is not None:
        return literal or None
    if isinstance(value, ast.Call):
        for kw in value.keywords:
            if kw.arg == "default":
                inner = _literal_str(kw.value)
                if inner is not None:
                    return inner or None
            if kw.arg == "default_factory" and isinstance(kw.value, ast.Lambda):
                produced = _literal_str(kw.value.body)
                if produced is not None:
                    return produced or None
        if value.args:
            inner = _literal_str(value.args[0])
            if inner is not None:
                return inner or None
    return None


def _documentation_constants(tree: ast.Module) -> set[int]:
    """Return ``id()``s of every string constant that is documentation.

    Docstrings and the values of documentation keywords (``description=``,
    ``examples=[...]``, ...). Identity is the right key: two equal placeholder
    strings in different positions must be judged separately.

    Returns:
        The set of object ids for constants that may name a placeholder.
    """
    allowed: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(
            node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef
        ):
            doc = ast.get_docstring(node, clean=False)
            if doc is not None and node.body:
                first = node.body[0]
                if isinstance(first, ast.Expr) and isinstance(
                    first.value, ast.Constant
                ):
                    allowed.add(id(first.value))
        if isinstance(node, ast.Call):
            for kw in node.keywords:
                if kw.arg in _DOC_KEYWORDS:
                    allowed.update(
                        id(inner)
                        for inner in ast.walk(kw.value)
                        if isinstance(inner, ast.Constant)
                    )
    return allowed


def _scan_placeholders(tree: ast.Module, relpath: str) -> list[str]:
    """Flag every placeholder identifier that is a value rather than prose.

    Returns:
        One finding per placeholder-carrying constant outside documentation.
    """
    allowed = _documentation_constants(tree)
    return [
        f"{relpath}:{node.lineno}: placeholder {node.value!r}"
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in allowed
        and _PLACEHOLDER_RE.search(node.value)
    ]


def _scan_setting_definitions(tree: ast.Module, relpath: str) -> list[str]:
    """Flag ``SettingDefinition(key="..._model", default="<non-blank>")``.

    Returns:
        One finding per model-shaped setting carrying a non-blank default.
    """
    findings: list[str] = []
    for node in ast.walk(tree):
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "SettingDefinition"
        ):
            continue
        kwargs = {kw.arg: kw.value for kw in node.keywords if kw.arg}
        key_node = kwargs.get("key")
        if not (isinstance(key_node, ast.Constant) and isinstance(key_node.value, str)):
            continue
        key = key_node.value
        default_node = kwargs.get("default")
        if not _is_model_name(key) or default_node is None:
            continue
        if _string_default(default_node):
            findings.append(
                f"{relpath}:{node.lineno}: setting {key!r} has a non-blank default"
            )
    return findings


def _scan_config_fields(tree: ast.Module, relpath: str) -> list[str]:
    """Flag a class field named model/*_model with a non-blank string default.

    Returns:
        One finding per model-shaped field carrying a bare id as its default.
    """
    findings: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        for stmt in node.body:
            target: str | None = None
            value: ast.expr | None = None
            if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
                target = stmt.target.id
                value = stmt.value
            elif (
                isinstance(stmt, ast.Assign)
                and len(stmt.targets) == 1
                and isinstance(stmt.targets[0], ast.Name)
            ):
                target = stmt.targets[0].id
                value = stmt.value
            if target is None or value is None or not _is_model_name(target):
                continue
            if _string_default(value):
                findings.append(
                    f"{relpath}:{stmt.lineno}: {node.name}.{target} defaults to a"
                    " bare model id (use a MODEL_REF setting)"
                )
    return findings


def _scan(root: Path) -> list[str]:
    """Return every current violation under *root*.

    Returns:
        Every finding, sorted by the order the files were walked.

    Raises:
        GateSourceError: When either expected tree is missing under *root*,
            so a misconfigured ``--repo-root`` (or a moved tree) fails closed
            rather than silently scanning zero files and reporting no
            violations.
    """
    findings: list[str] = []
    definitions_dir = root / _DEFINITIONS_REL
    src_dir = root / _SRC_REL
    if not src_dir.is_dir():
        msg = f"expected source tree not found: {src_dir}"
        raise GateSourceError(msg)
    if not definitions_dir.is_dir():
        # Without this the `SettingDefinition` arm scans zero files whenever
        # the tree moves, and a gate that inspected nothing still exits 0.
        msg = f"expected settings-definitions tree not found: {definitions_dir}"
        raise GateSourceError(msg)
    for path in sorted(src_dir.rglob("*.py")):
        relpath = path.relative_to(root).as_posix()
        _text, tree = read_and_parse(path)
        findings.extend(_scan_placeholders(tree, relpath))
        findings.extend(_scan_config_fields(tree, relpath))
        if definitions_dir in path.parents:
            findings.extend(_scan_setting_definitions(tree, relpath))
    return findings


def main(argv: list[str] | None = None) -> int:
    """Scan for unbound / placeholder models and return the gate exit code.

    Returns:
        ``0`` clean, ``1`` on a violation, ``2`` on a configuration error.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", default=".", help="Repository root.")
    args = parser.parse_args(argv)

    root = Path(args.repo_root).resolve()
    if not root.is_dir():
        print(f"error: --repo-root is not a directory: {root}", file=sys.stderr)
        return 2

    try:
        findings = _scan(root)
    except GateSourceError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if findings:
        print(
            "error: a model must be an operator-chosen (provider, model) pair; "
            "placeholders and bare model defaults never ship:",
            file=sys.stderr,
        )
        for finding in findings:
            print(f"  {finding}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
