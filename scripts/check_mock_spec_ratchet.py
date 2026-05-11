#!/usr/bin/env python3
"""PreToolUse hook: ratchet the mock-spec gate towards zero.

Two protections, one process:

1. Edits to ``tests/*.py`` MUST NOT increase the gate's CATCH count
   in the touched file. The hook scans the BEFORE state from disk
   and the AFTER state by applying the Edit / Write payload
   in-memory, runs ``check_mock_spec._scan_file`` on each, and
   blocks when the count grows. This is the "drive-by tightening"
   ratchet: every edit either reduces or holds the residual,
   pushing the global count to zero over time.

2. Edits to ``scripts/check_mock_spec.py`` MUST NOT reduce the
   number of distinct ``return _Verdict.CATCH`` branches. Removing
   a CATCH branch would weaken the gate. The hook counts the
   literal substring ``_Verdict.CATCH`` before and after; AFTER
   < BEFORE blocks.

Skips:
  * Files outside ``tests/`` and not the gate itself.
  * Files under ``tests/_shared/`` (helpers, not subject to the gate).
  * Non-``.py`` files.
  * Edits that leave content unchanged (idempotent).

The hook fails open on parse / import errors so a transiently
broken gate cannot wedge all editing.
"""

import ast
import contextlib
import importlib.util
import json
import sys
import tempfile
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parent.parent
_TESTS_ROOT = _REPO_ROOT / "tests"
_GATE_PATH = _REPO_ROOT / "scripts" / "check_mock_spec.py"
_VERDICT_NAME = "_Verdict"
_CATCH_ATTR = "CATCH"


def _count_catch_returns(source: str) -> int:
    """Count ``return _Verdict.CATCH`` statements in *source*.

    Walks the AST once and counts every ``ast.Return`` whose value is
    the ``_Verdict.CATCH`` attribute access. Counting the parsed
    statement rather than the literal substring keeps docstrings,
    inline comments, and error-message wording out of the ratchet.

    Falls back to the cheaper substring count when the source fails
    to parse so a transient syntax error during an interactive edit
    cannot wedge the gate.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return source.count(f"{_VERDICT_NAME}.{_CATCH_ATTR}")
    count = 0
    for node in ast.walk(tree):
        if not isinstance(node, ast.Return):
            continue
        value = node.value
        if (
            isinstance(value, ast.Attribute)
            and value.attr == _CATCH_ATTR
            and isinstance(value.value, ast.Name)
            and value.value.id == _VERDICT_NAME
        ):
            count += 1
    return count


def _load_gate() -> Any:
    """Dynamically import the gate module so the hook always sees live source."""
    spec = importlib.util.spec_from_file_location(
        "_ratchet_check_mock_spec",
        _GATE_PATH,
    )
    if spec is None or spec.loader is None:
        msg = f"could not load module spec for {_GATE_PATH}"
        raise RuntimeError(msg)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _scan_text(gate: Any, text: str, suffix: str = ".py") -> int:
    """Return CATCH count for *text* by scanning a tmp copy."""
    with tempfile.NamedTemporaryFile(
        mode="w",
        suffix=suffix,
        delete=False,
        encoding="utf-8",
    ) as fp:
        fp.write(text)
        tmp_path = Path(fp.name)
    try:
        return len(gate._scan_file(tmp_path))  # noqa: SLF001 -- consume gate API
    except Exception as exc:
        print(
            f"check_mock_spec_ratchet: gate scan failed, allowing edit: "
            f"{type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        return 0
    finally:
        with contextlib.suppress(OSError):
            tmp_path.unlink()


def _compute_after(  # noqa: PLR0911 -- shape mirrors the tool envelope
    tool_name: str,
    tool_input: dict[str, Any],
    before: str,
) -> str | None:
    """Return the post-edit content, or None if the edit is a no-op."""
    if tool_name == "Edit":
        old = tool_input.get("old_string", "")
        new = tool_input.get("new_string", "")
        if old == new:
            return None
        replace_all = bool(tool_input.get("replace_all", False))
        if old not in before:
            return None
        if replace_all:
            return before.replace(old, new)
        return before.replace(old, new, 1)
    if tool_name == "Write":
        content = tool_input.get("content", "")
        if not isinstance(content, str) or content == before:
            return None
        return content
    return None


def _check_test_file(path: Path, before: str, after: str) -> int:
    """Block if AFTER count > BEFORE count."""
    try:
        gate = _load_gate()
    except Exception as exc:
        print(
            f"check_mock_spec_ratchet: gate load failed, allowing edit to "
            f"{path.name}: {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        return 0
    before_count = _scan_text(gate, before) if before else 0
    after_count = _scan_text(gate, after)
    if after_count > before_count:
        print(
            f"BLOCKED: edit would raise the mock-spec violation count in "
            f"{path.name} from {before_count} to {after_count}. The gate "
            f"is a ratchet: every edit must reduce or hold the count. "
            f"Add ``spec=ConcreteType`` to the new bare Mock(), or fix "
            f"existing residual violations in the same file before "
            f"introducing new ones. See "
            f"docs/reference/conventions.md section 12.1.",
            file=sys.stderr,
        )
        return 2
    return 0


def _check_gate_file(before: str, after: str) -> int:
    """Block if AFTER has fewer ``_Verdict.CATCH`` branches than BEFORE."""
    before_count = _count_catch_returns(before)
    after_count = _count_catch_returns(after)
    if after_count < before_count:
        print(
            f"BLOCKED: edit removes ``_Verdict.CATCH`` branches from "
            f"scripts/check_mock_spec.py ({before_count} -> "
            f"{after_count}). The gate cannot be weakened: every change "
            f"to the gate must preserve or extend its CATCH coverage. "
            f"If a branch is genuinely wrong, replace it with an "
            f"alternative CATCH path rather than dropping it.",
            file=sys.stderr,
        )
        return 2
    return 0


def main() -> int:  # noqa: C901, PLR0911 -- guard cascade is flat by design
    """Read the PreToolUse JSON envelope from stdin and return an exit code."""
    raw = sys.stdin.read()
    if not raw.strip():
        return 0
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return 0

    tool_name = payload.get("tool_name", "")
    tool_input = payload.get("tool_input") or {}
    if tool_name not in ("Edit", "Write"):
        return 0

    file_path = tool_input.get("file_path")
    if not file_path:
        return 0

    gate_path = _GATE_PATH.resolve()
    tests_root = _TESTS_ROOT.resolve()
    shared_dir = (tests_root / "_shared").resolve()

    # Resolve the caller-supplied path, then enforce the allowlist via
    # ``Path.relative_to``. ``relative_to`` raises ``ValueError`` when
    # the candidate is outside the trusted root, which CodeQL's
    # ``py/path-injection`` query recognises as a sanitiser (the
    # ``is_relative_to`` boolean form is not consistently picked up
    # when combined with adjacent conjuncts). The early ``return 0``
    # paths below ensure no filesystem read happens on an unvalidated
    # path.
    try:
        path = Path(file_path).resolve()
    except OSError, ValueError:
        return 0

    if path.suffix != ".py":
        return 0

    is_gate = path == gate_path
    if not is_gate:
        try:
            path.relative_to(tests_root)
        except ValueError:
            return 0
        if shared_dir in path.parents:
            return 0

    try:
        before = path.read_text(encoding="utf-8") if path.exists() else ""
    except OSError, UnicodeDecodeError:
        return 0

    after = _compute_after(tool_name, tool_input, before)
    if after is None or after == before:
        return 0

    if is_gate:
        return _check_gate_file(before, after)
    return _check_test_file(path, before, after)


if __name__ == "__main__":
    sys.exit(main())
