#!/usr/bin/env python3
"""Pre-push / CI gate: a setting is compose-set or live, and nothing else.

Every setting is either fixed when a process starts or changeable while the
system runs. The first kind carries ``compose_set=True``, which makes the
operator-facing claim "the deployment sets this"; this gate checks that claim
is true by requiring the shipped tooling to actually pass the matching
environment variable -- the compose template for the backend service, or the
worker launch command for the settings only that process reads.

Without the check, ``compose_set=True`` is just a label meaning "we did not
wire this up", attached to a setting the operator has no supported way to
change at all.

Two compose files ship a backend, so a backend setting has to be passed by
BOTH: a value present in one and absent from the other means the settings page
reports what one deployment path does while the other silently runs the code
default, and a bind address or a proxy list is exactly where that diverges
without anything failing.

The env var is the definition's ``env_var_override`` when it declares one, and
``SYNTHORG_{NAMESPACE}_{KEY}`` otherwise -- the same resolution the settings
bootstrap performs, so a name that satisfies this gate is a name that works.

Usage::

    python scripts/check_setting_compose_backed.py
    python scripts/check_setting_compose_backed.py --repo-root /path
"""

import argparse
import ast
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Final

_DEFINITIONS_REL: Final[str] = "src/synthorg/settings/definitions"
# Every compose file that starts a backend. A backend setting has to appear in
# all of them, because each is a deployment path an operator actually runs.
_BACKEND_SOURCE_RELS: Final[tuple[str, ...]] = (
    "cli/internal/compose/compose.yml.tmpl",
    "docker/compose.yml",
)
# The worker is launched by the CLI rather than by compose, so a setting only
# that process reads is backed here instead.
_WORKER_SOURCE_REL: Final[str] = "cli/cmd/worker_start.go"
# The launch command names the variables through Go constants rather than
# literals, so resolve those before searching: a rename that leaves the
# constant referenced is not an unwired setting, and a literal search would
# call it one.
_GO_CONSTANTS_REL: Final[str] = "cli/internal/config/tunables.go"
_GO_CONST_PATTERN: Final[str] = r'^\s*(Env\w+)\s*=\s*"(SYNTHORG_\w+)"'


@dataclass(frozen=True)
class ComposeSetSetting:
    """A compose-set setting and the env var the deployment must supply."""

    setting_key: str
    env_var: str
    source_file: str
    source_line: int


def _resolve_namespace(node: ast.expr | None) -> str | None:
    """Resolve ``SettingNamespace.X`` to its lower-case namespace string.

    Returns:
        The namespace string, or ``None`` when the node is not a namespace
        reference.
    """
    if not isinstance(node, ast.Attribute) or not isinstance(node.value, ast.Name):
        return None
    if node.value.id != "SettingNamespace":
        return None
    return node.attr.lower()


def _string_literal(node: ast.expr | None) -> str | None:
    """Return the string-literal value of *node*, or ``None``.

    Returns:
        The literal, or ``None`` when the node is absent or not a plain string.
    """
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _scan_file(path: Path, rel: str) -> list[ComposeSetSetting]:
    """Extract the compose-set settings declared in *path*.

    Returns:
        One record per ``compose_set=True`` definition.

    Raises:
        ValueError: If the file is unreadable or has invalid Python syntax;
            silently dropping a file would let an unbacked claim slip past.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        msg = f"{rel}: could not read definitions file: {exc}"
        raise ValueError(msg) from exc
    try:
        tree = ast.parse(text, filename=str(path))
    except SyntaxError as exc:
        msg = f"{rel}:{exc.lineno or 0}: syntax error: {exc.msg}"
        raise ValueError(msg) from exc

    records: list[ComposeSetSetting] = []
    for call in ast.walk(tree):
        if not (
            isinstance(call, ast.Call)
            and isinstance(call.func, ast.Name)
            and call.func.id == "SettingDefinition"
        ):
            continue
        kwargs = {kw.arg: kw.value for kw in call.keywords}
        flag = kwargs.get("compose_set")
        if not (isinstance(flag, ast.Constant) and flag.value is True):
            continue
        namespace = _resolve_namespace(kwargs.get("namespace"))
        key = _string_literal(kwargs.get("key"))
        if namespace is None or key is None:
            continue
        override = _string_literal(kwargs.get("env_var_override"))
        records.append(
            ComposeSetSetting(
                setting_key=f"{namespace}.{key}",
                env_var=override or f"SYNTHORG_{namespace.upper()}_{key.upper()}",
                source_file=rel,
                source_line=call.lineno,
            )
        )
    return records


def scan_definitions(repo_root: Path) -> list[ComposeSetSetting]:
    """Return every compose-set setting under ``settings/definitions/``.

    Returns:
        The records, in file then declaration order.
    """
    records: list[ComposeSetSetting] = []
    for path in sorted((repo_root / _DEFINITIONS_REL).glob("*.py")):
        if path.name == "__init__.py":
            continue
        records.extend(_scan_file(path, path.relative_to(repo_root).as_posix()))
    return records


def unbacked(
    records: list[ComposeSetSetting], sources: dict[str, str]
) -> list[tuple[ComposeSetSetting, str]]:
    """Return each record paired with the source that fails to set it.

    A worker-only setting is satisfied by the worker launch command alone;
    anything else has to be passed by every compose file that starts a backend.

    Returns:
        ``(record, missing_source)`` pairs, sorted by setting key.
    """
    failures: list[tuple[ComposeSetSetting, str]] = []
    for record in records:
        if _forwards_env_var(sources[_WORKER_SOURCE_REL], record.env_var):
            continue
        failures.extend(
            (record, rel)
            for rel in _BACKEND_SOURCE_RELS
            if not _assigns_env_var(sources[rel], record.env_var)
        )
    return sorted(failures, key=lambda pair: (pair[0].setting_key, pair[1]))


def _assigns_env_var(source: str, env_var: str) -> bool:
    """Report whether a compose file assigns *env_var* in a service.

    The name has to open a mapping entry or a ``- KEY=value`` list item.
    Merely appearing somewhere in the file is not passing it: these templates
    carry prose about variables they deliberately do NOT set (the ones baked
    into the image ENV, the ones an operator supplies through an env_file),
    and a mention in that prose would back the label the gate exists to check.

    Args:
        source: The compose text to search, comments already stripped.
        env_var: The variable that must be assigned.

    Returns:
        ``True`` when the variable opens an assignment.
    """
    pattern = rf"^\s*(?:-\s+)?{re.escape(env_var)}\s*[:=]"
    return re.search(pattern, source, flags=re.MULTILINE) is not None


def _forwards_env_var(source: str, env_var: str) -> bool:
    """Report whether the worker launcher passes *env_var* to the child.

    A whole-token match rather than an assignment: the launcher forwards a
    variable by name (``docker exec -e NAME``) precisely so the value stays
    out of argv, and the name reaches this function as a resolved constant
    on a line of its own. Whole-token because a plain substring test passes a
    setting whose variable is a strict prefix of one that IS forwarded, so
    ``SYNTHORG_API_SSL`` would read as backed on the strength of
    ``SYNTHORG_API_SSL_CERT_FILE``.

    Args:
        source: The launcher text to search, comments already stripped.
        env_var: The variable that must appear.

    Returns:
        ``True`` when the variable appears as its own token.
    """
    return re.search(rf"(?<!\w){re.escape(env_var)}(?!\w)", source) is not None


def _strip_comments(rel: str, source: str) -> str:
    """Return *source* with its comments removed.

    Args:
        rel: Repository-relative path, which decides the comment syntax.
        source: The file text.

    Returns:
        The text with comment spans replaced by blank space, so line
        structure and therefore the assignment anchors survive.
    """
    if rel.endswith(".go"):
        spans = (r"//[^\n]*", r"/\*.*?\*/")
    else:
        # Go template comments first: they wrap `/* */` prose that a bare
        # `#` rule would leave behind.
        spans = (r"\{\{-?\s*/\*.*?\*/\s*-?\}\}", r"#[^\n]*")
    for span in spans:
        source = re.sub(
            span,
            lambda match: re.sub(r"[^\n]", " ", match.group()),
            source,
            flags=re.DOTALL,
        )
    return source


def _read_sources(repo_root: Path) -> dict[str, str]:
    """Return the text of every place the tooling sets env vars, by path.

    Returns:
        The source text keyed by repository-relative path.

    Raises:
        ValueError: When a source file cannot be read; a missing one would
            silently pass every setting it was meant to back.
    """
    sources: dict[str, str] = {}
    for rel in (*_BACKEND_SOURCE_RELS, _WORKER_SOURCE_REL, _GO_CONSTANTS_REL):
        try:
            text = (repo_root / rel).read_text(encoding="utf-8")
        except OSError as exc:
            msg = f"{rel}: could not read env-var source: {exc}"
            raise ValueError(msg) from exc
        sources[rel] = _strip_comments(rel, text)
    constants = _go_env_constants(sources.pop(_GO_CONSTANTS_REL))
    sources[_WORKER_SOURCE_REL] = _resolve_go_constants(
        sources[_WORKER_SOURCE_REL], constants
    )
    return sources


def _go_env_constants(text: str) -> dict[str, str]:
    """Return the ``EnvName -> SYNTHORG_VALUE`` pairs declared in *text*.

    Returns:
        The constant map, empty when the file declares none.
    """
    return dict(re.findall(_GO_CONST_PATTERN, text, flags=re.MULTILINE))


def _resolve_go_constants(text: str, constants: dict[str, str]) -> str:
    """Append the value of every constant *text* references.

    Returns:
        The source text plus one line per resolved constant, so a search for
        the variable name matches whether the call site spells the literal or
        the constant.
    """
    resolved = [
        value for name, value in constants.items() if _references_token(text, name)
    ]
    return "\n".join([text, *resolved])


def _references_token(source: str, name: str) -> bool:
    """Report whether *source* names *name* as its own identifier.

    Whole-token for the same reason :func:`_sets_env_var` is: with a plain
    substring test a constant whose name is a strict prefix of another
    (``EnvAPIServerHost`` inside ``EnvAPIServerHostname``) resolves off the
    longer one's mention, and its value joins the searched text even though
    the launcher never sets it.

    Args:
        source: The Go source to search.
        name: The constant identifier.

    Returns:
        ``True`` when the identifier appears as its own token.
    """
    return re.search(rf"(?<!\w){re.escape(name)}(?!\w)", source) is not None


def _run(repo_root: Path) -> int:
    """Execute the gate.

    Returns:
        Process exit code (0 pass, 1 fail).
    """
    try:
        records = scan_definitions(repo_root)
        sources = _read_sources(repo_root)
    except ValueError as exc:
        sys.stderr.write(f"{exc}\n")
        return 1

    missing = unbacked(records, sources)
    if missing:
        sys.stderr.write(
            "compose_set settings the shipped tooling never passes. Either add"
            " the env var to the named file, or drop compose_set and make the"
            " setting apply live:\n"
        )
        for record, source_rel in missing:
            sys.stderr.write(
                f"  {record.setting_key} needs {record.env_var} in {source_rel}"
                f" ({record.source_file}:{record.source_line})\n"
            )
        return 1
    sys.stdout.write(
        f"OK: {len(records)} compose-set settings, all passed by the deployment.\n"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    """CLI entry point.

    Returns:
        Process exit code (0 pass, 1 fail).
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=None)
    args = parser.parse_args(argv)
    repo_root = args.repo_root or Path(__file__).resolve().parent.parent
    return _run(repo_root)


if __name__ == "__main__":
    raise SystemExit(main())
