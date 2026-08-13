#!/usr/bin/env python
"""Gate: an atomic write states its delivered mode; a workspace dir is shared.

``tempfile.mkstemp`` creates its file owner-only, because it is a
private-temp-file primitive. An atomic write that renames that file over a
target therefore *delivers* ``0o600`` unless it says otherwise, and the
backend's umask cannot help: a mode nobody set is the mode ``mkstemp``
chose. Two writers shipped that way, so every file an agent authored was
invisible to the sandbox that had to test it, and the build/test oracle
never once saw a passing run.

The same hazard on directories has the opposite cause: ``mkdir``'s *mode*
argument IS masked by the umask, so ``022`` drops exactly the group-write
bit the sandbox needs, and a workspace root created that way is one the
sandbox can traverse and never write.

Two checks, both AST-decidable:

* a function calling ``tempfile.mkstemp`` must also call ``fchmod`` or
  ``chmod`` (any receiver): the mode has to be stated somewhere in the same
  function, whatever it is;
* a module under the workspace tree must not call ``.mkdir(`` directly; it
  goes through ``core.workspace_sharing.ensure_shared_dir``, which applies
  the shared mode after creation.

Neither says which mode is right, only that one was chosen deliberately.
Opt out per-function or per-call with
``# lint-allow: workspace-share-mode -- <reason>``.
"""

import ast
import sys
from pathlib import Path
from typing import Final

_ALLOW_MARKER: Final[str] = "lint-allow: workspace-share-mode"
_MODE_SETTERS: Final[frozenset[str]] = frozenset({"fchmod", "chmod"})
_SRC_ROOT: Final[str] = "src/synthorg"
_WORKSPACE_DIRS: Final[tuple[str, ...]] = (
    "src/synthorg/engine/workspace",
    "src/synthorg/tools/file_system",
)


def _call_name(node: ast.Call) -> str:
    """Return the trailing attribute or name of *node*'s callee."""
    func = node.func
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return ""


def _marked(lines: list[str], node: ast.AST) -> bool:
    """Return whether an opt-out marker covers *node*.

    The marker may sit inside the node's own span or in the contiguous
    comment block directly above it, because a function-level exemption is
    written where a reader meets the function rather than buried in its body.
    """
    start = getattr(node, "lineno", 0)
    if not start:
        return False
    end = getattr(node, "end_lineno", start) or start
    if any(_ALLOW_MARKER in line for line in lines[start - 1 : end]):
        return True
    index = start - 2
    while index >= 0 and lines[index].lstrip().startswith("#"):
        if _ALLOW_MARKER in lines[index]:
            return True
        index -= 1
    return False


def _mkstemp_without_mode(tree: ast.AST, lines: list[str]) -> list[tuple[int, str]]:
    """Return every function that creates a temp file and never states its mode."""
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        calls = [c for c in ast.walk(node) if isinstance(c, ast.Call)]
        names = {_call_name(call) for call in calls}
        if "mkstemp" not in names or names & _MODE_SETTERS:
            continue
        if _marked(lines, node):
            continue
        message = (
            f"{node.name}() renames a mkstemp file into place without "
            "setting its mode, so it delivers mkstemp's owner-only bits. "
            "Apply core.workspace_sharing.delivered_file_mode via fchmod."
        )
        found.append((node.lineno, message))
    return found


def _raw_mkdir(tree: ast.AST, lines: list[str]) -> list[tuple[int, str]]:
    """Return every direct ``mkdir`` in a module that owns workspace layout."""
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or _call_name(node) != "mkdir":
            continue
        if _marked(lines, node):
            continue
        message = (
            "mkdir() in the workspace tree: its mode argument is masked "
            "by the process umask, which drops the group-write bit the "
            "sandbox needs. Use core.workspace_sharing.ensure_shared_dir."
        )
        found.append((node.lineno, message))
    return found


def main() -> int:
    """Report every unstated delivery mode and raw workspace mkdir.

    Returns:
        Process exit status: 0 when the sharing contract is stated everywhere.
    """
    root = Path(__file__).resolve().parent.parent
    failures: list[str] = []
    for path in sorted((root / _SRC_ROOT).rglob("*.py")):
        rel = path.relative_to(root).as_posix()
        source = path.read_text(encoding="utf-8")
        lines = source.splitlines()
        tree = ast.parse(source, filename=str(path))
        problems = _mkstemp_without_mode(tree, lines)
        if rel.startswith(_WORKSPACE_DIRS):
            problems += _raw_mkdir(tree, lines)
        failures.extend(f"{rel}:{line}: {message}" for line, message in problems)
    if not failures:
        return 0
    for failure in sorted(failures):
        sys.stderr.write(f"{failure}\n")
    sys.stderr.write(
        "\nWorkspace sharing gate failed. The backend and the sandbox are "
        "different uids sharing one group; a mode nobody states is a file or "
        f"directory the sandbox cannot reach. Opt out with `# {_ALLOW_MARKER} "
        "-- <reason>`.\n"
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
