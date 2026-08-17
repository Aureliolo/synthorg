"""Gate: every bundled MCP catalog entry can be launched by a shipped image.

The shipped stack could not run the shipped catalog, and never could. The one
bundled entry an operator can install is an npm package launched over stdio,
the backend image is hardened (no shell, no node, no ``npx``), and the
containerising wrapper that existed to solve that shelled out to a ``docker``
CLI the image does not ship either. Every boot logged one
``FileNotFoundError``, reported ``tool_count=0``, and moved on.

Install-time refusal is the runtime half of the fix. This is the build half,
and it asks the two questions a runtime check cannot:

1. Does every program declared launchable actually exist in the image? The
   declaration in ``tools/mcp/runtime_provision.py`` names the apko package
   that installs each one, so dropping ``npm`` from ``docker/sandbox/apko.yaml``
   fails here rather than at the next reconnect.
2. Does every bundled entry name a launchable program? An entry is data, so
   nothing else in the build would notice one naming ``uvx``.

Fail-closed: an unreadable declaration, an unreadable apko file, or an empty
programs mapping is exit 2, because a gate looking at nothing must not report
success.

Usage:
    uv run python scripts/check_mcp_catalog_launchable.py

Exit codes:
    0 -- every declared program is in the image and every entry is launchable.
    1 -- a declared program is absent, or an entry cannot be launched.
    2 -- configuration error (bad ``--repo-root`` or an unreadable source).
"""

import argparse
import ast
import json
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

_PROVISION_REL: Final[str] = "src/synthorg/tools/mcp/runtime_provision.py"
_APKO_REL: Final[str] = "docker/sandbox/apko.yaml"
_CATALOG_REL: Final[str] = "src/synthorg/integrations/mcp_catalog/bundled.json"
_INSTALL_REL: Final[str] = "src/synthorg/integrations/mcp_catalog/install.py"

_DECLARATION: Final[str] = "RUNTIME_PROGRAMS"
_LAUNCHER_CONSTANT: Final[str] = "_NPM_LAUNCHER"

_REPO_ROOT: Final[Path] = Path(__file__).resolve().parent.parent

#: The apko packages block ends at the next top-level key, so the scan is
#: bounded by indentation rather than by parsing the whole document (the gate
#: must not need a YAML dependency to run).
_PACKAGES_KEY: Final[str] = "packages:"

#: Read-only wrapper the declaration is published through, stripped before
#: the literal is read.
_READONLY_WRAPPER: Final[str] = "MappingProxyType"


def _unwrapped(value: ast.expr) -> ast.expr:
    """Strip a read-only wrapper from a declaration, leaving the literal.

    The declaration is published as a ``Mapping``, so it is wrapped in
    ``MappingProxyType`` to be one in fact and not only in annotation.
    ``literal_eval`` cannot see through a call, so the wrapper would read
    as "not a literal mapping" and fail the gate closed on a declaration
    that is perfectly well formed.

    Args:
        value: The declaration's assigned expression.

    Returns:
        The wrapped literal, or *value* unchanged when nothing wraps it.
    """
    if (
        isinstance(value, ast.Call)
        and isinstance(value.func, ast.Name)
        and value.func.id == _READONLY_WRAPPER
        and len(value.args) == 1
    ):
        return value.args[0]
    return value


def _declared_programs(repo_root: Path) -> dict[str, str]:
    """Read the program-to-package declaration from the source.

    Returns:
        Each launchable program mapped to the apko package providing it.

    Raises:
        GateSourceError: The declaration is absent or not a literal mapping.
    """
    path = repo_root / _PROVISION_REL
    _source, tree = read_and_parse(path)
    for node in ast.walk(tree):
        if not isinstance(node, ast.AnnAssign | ast.Assign):
            continue
        targets = [node.target] if isinstance(node, ast.AnnAssign) else node.targets
        names = {t.id for t in targets if isinstance(t, ast.Name)}
        if _DECLARATION not in names or node.value is None:
            continue
        try:
            mapping = ast.literal_eval(_unwrapped(node.value))
        except ValueError as exc:
            msg = f"{_PROVISION_REL}: {_DECLARATION} is not a literal mapping"
            raise GateSourceError(msg) from exc
        if not isinstance(mapping, dict) or not mapping:
            msg = f"{_PROVISION_REL}: {_DECLARATION} is empty; nothing to enforce"
            raise GateSourceError(msg)
        return {str(program): str(package) for program, package in mapping.items()}
    msg = f"{_PROVISION_REL}: {_DECLARATION} not found"
    raise GateSourceError(msg)


def _image_packages(repo_root: Path) -> set[str]:
    """Read the packages the sandbox image installs.

    Returns:
        Every package name listed under the apko ``packages:`` key.

    Raises:
        GateSourceError: The file is unreadable or declares no packages.
    """
    path = repo_root / _APKO_REL
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        msg = f"{_APKO_REL}: unreadable ({exc.strerror})"
        raise GateSourceError(msg) from exc
    packages: set[str] = set()
    indent: int | None = None
    for line in lines:
        stripped = line.strip()
        if indent is None:
            if stripped == _PACKAGES_KEY:
                indent = len(line) - len(line.lstrip())
            continue
        if not stripped or stripped.startswith("#"):
            continue
        current = len(line) - len(line.lstrip())
        if current <= indent:
            break
        if stripped.startswith("- "):
            packages.add(stripped.removeprefix("- ").strip())
    if not packages:
        msg = f"{_APKO_REL}: no packages found under {_PACKAGES_KEY}"
        raise GateSourceError(msg)
    return packages


def _entry_launchers(repo_root: Path) -> dict[str, str]:
    """Work out which program each bundled entry would be launched through.

    Returns:
        Entry id mapped to the program its launch names. An entry needing no
        local runtime (a remote transport) is absent.

    Raises:
        GateSourceError: The catalog or the installer cannot be read.
    """
    npm_launcher = _npm_launcher(repo_root)
    path = repo_root / _CATALOG_REL
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        msg = f"{_CATALOG_REL}: unreadable ({exc})"
        raise GateSourceError(msg) from exc
    servers = raw.get("servers") if isinstance(raw, dict) else None
    if not isinstance(servers, list) or not servers:
        msg = f"{_CATALOG_REL}: no catalog entries found"
        raise GateSourceError(msg)
    launchers: dict[str, str] = {}
    for entry in servers:
        if not isinstance(entry, dict):
            msg = f"{_CATALOG_REL}: an entry is not an object"
            raise GateSourceError(msg)
        if entry.get("transport", "stdio") != "stdio":
            continue
        launchers[str(entry.get("id", "<unnamed>"))] = npm_launcher
    return launchers


def _npm_launcher(repo_root: Path) -> str:
    """Read the program the installer launches an npm-packaged entry through.

    Read rather than assumed: hardcoding ``npx`` here would check the gate's
    own assumption instead of the installer's behaviour.

    Returns:
        The launcher program name.

    Raises:
        GateSourceError: The installer no longer declares one.
    """
    path = repo_root / _INSTALL_REL
    _source, tree = read_and_parse(path)
    for node in ast.walk(tree):
        if not isinstance(node, ast.AnnAssign) or node.value is None:
            continue
        if isinstance(node.target, ast.Name) and node.target.id == _LAUNCHER_CONSTANT:
            try:
                value = ast.literal_eval(node.value)
            except ValueError as exc:
                # A computed launcher is a source the gate cannot read, which
                # is the same configuration error as an absent one and takes
                # the same exit code rather than a traceback.
                msg = (
                    f"{_INSTALL_REL}: {_LAUNCHER_CONSTANT} is not a literal; "
                    "the gate cannot read the launcher it enforces"
                )
                raise GateSourceError(msg) from exc
            return str(value)
    msg = f"{_INSTALL_REL}: {_LAUNCHER_CONSTANT} not found"
    raise GateSourceError(msg)


class ProjectRootError(Exception):
    """Raised when ``--repo-root`` cannot be resolved to a usable directory."""


def _resolve_project_root(repo_root: Path | None) -> Path:
    """Resolve the project root from CLI arguments.

    Returns:
        The resolved project-root directory.

    Raises:
        ProjectRootError: If *repo_root* cannot be resolved to an existing
            directory.
    """
    if repo_root is None:
        return _REPO_ROOT
    try:
        resolved = repo_root.resolve(strict=True)
    except OSError as exc:
        msg = f"--repo-root not accessible: {repo_root} ({exc})"
        raise ProjectRootError(msg) from exc
    if not resolved.is_dir():
        msg = f"--repo-root must be a directory: {resolved}"
        raise ProjectRootError(msg)
    return resolved


def _check(repo_root: Path) -> list[str]:
    """Hold the launchability declaration to the image and the catalog.

    Returns:
        A list of violation messages (empty when everything is launchable).

    Raises:
        GateSourceError: A source the gate reads is missing or unreadable.
    """
    programs = _declared_programs(repo_root)
    packages = _image_packages(repo_root)
    violations = [
        (
            f"{_PROVISION_REL}: {_DECLARATION} declares {program!r} as launchable "
            f"via package {package!r}, which {_APKO_REL} does not install; an "
            f"entry naming it would fail at connect on every boot"
        )
        for program, package in sorted(programs.items())
        if package not in packages
    ]
    violations.extend(
        f"{_CATALOG_REL}: entry {entry_id!r} is launched through {launcher!r}, "
        f"which {_DECLARATION} does not declare; no shipped image provides it"
        for entry_id, launcher in sorted(_entry_launchers(repo_root).items())
        if launcher not in programs
    )
    return violations


def main(argv: list[str] | None = None) -> int:
    """Run the MCP catalog launchability gate.

    Returns:
        The process exit code (0 clean, 1 violations, 2 config error).
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=None)
    args = parser.parse_args(argv)
    try:
        project_root = _resolve_project_root(args.repo_root)
    except ProjectRootError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    try:
        violations = _check(project_root)
    except GateSourceError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if violations:
        print("MCP catalog launchability check failed:", file=sys.stderr)
        for violation in violations:
            print(f"  {violation}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
