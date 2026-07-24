"""Gate: inbound chat human text never reaches a prompt unfenced (SEC-1).

An inbound Slack Socket-Mode reply is attacker-controlled human input. It
must never be concatenated into an LLM prompt from the inbound package:
the router forwards it ONLY as a resume ``decision_reason``, and the resume
machinery fences it with ``wrap_untrusted(TAG_TASK_DATA, ...)`` (via
``build_resume_message``) before any prompt boundary -- the same path the
dashboard approval comment takes.

This gate enforces that contract structurally over
``src/synthorg/integrations/chat_api/inbound/``:

1. **No prompt sink.** No module in the package may call an LLM-completion
   chokepoint (``complete_text`` / ``complete_structured_text`` /
   ``cost_recording_scope`` / a bare ``.complete(``), so raw inbound text
   cannot be turned into a prompt inside the package.
2. **Fenced hand-off present.** ``InboundResumeRouter.route`` must forward
   inbound text through its ``self._dispatcher.resume(...)`` call's
   ``decision_reason=`` keyword (the fenced downstream sink), so the escape
   path is the documented one, not an ad-hoc string.

Opt a genuine exception out with a trailing
``# lint-allow: chat-inbound-fenced -- <reason>`` comment on the offending
call line.

Usage:
    uv run python scripts/check_chat_inbound_fenced.py

Exit codes:
    0 -- inbound text stays fenced.
    1 -- a prompt sink was found, or the fenced hand-off is missing.
    2 -- configuration error (bad ``--repo-root`` or an unreadable source).
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
        reachable_statements,
        read_and_parse,
        statement_expressions,
    )
else:
    from scripts._gate_source import (
        GateSourceError,
        reachable_statements,
        read_and_parse,
        statement_expressions,
    )

_INBOUND_REL: Final[str] = "src/synthorg/integrations/chat_api/inbound"
_ROUTER_REL: Final[str] = "src/synthorg/integrations/chat_api/inbound/router.py"
_PROMPT_SINKS: Final[frozenset[str]] = frozenset(
    {
        "complete_text",
        "complete_structured_text",
        "cost_recording_scope",
        "complete",
    },
)
_FENCED_KWARG: Final[str] = "decision_reason"
# The router's hand-off to the resume dispatcher. The fenced keyword must
# ride on THIS call: ``self._dispatcher.resume(...)`` inside the router
# class's entrypoint, not merely on some ``resume(...)`` elsewhere in the
# module.
_RESUME_CALL: Final[str] = "resume"
_ROUTER_CLASS: Final[str] = "InboundResumeRouter"
_ROUTER_ENTRYPOINT: Final[str] = "route"
_DISPATCHER_ATTR: Final[str] = "_dispatcher"
_MARKER: Final[str] = "lint-allow: chat-inbound-fenced"
_ALLOW_RE: Final[re.Pattern[str]] = re.compile(
    r"#.*" + re.escape(_MARKER) + r"\s*--\s*\S"
)


def _called_name(call: ast.Call) -> str | None:
    """Return the simple/attribute name being called, if any.

    Returns:
        The function/attribute name, or ``None`` for an unusual callee.
    """
    func = call.func
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return None


def _line_has_marker(source: str, lineno: int) -> bool:
    """Whether the source line carries the opt-out marker.

    Returns:
        ``True`` when a valid ``# lint-allow: chat-inbound-fenced -- ...``
        comment is on the line.
    """
    lines = source.splitlines()
    if not (1 <= lineno <= len(lines)):
        return False
    return bool(_ALLOW_RE.search(lines[lineno - 1]))


def _prompt_sink_violations(repo_root: Path) -> list[str]:
    """Find LLM-completion sink calls in the inbound package.

    Returns:
        A list of violation messages (empty when clean).
    """
    violations: list[str] = []
    inbound_dir = repo_root / _INBOUND_REL
    for path in sorted(inbound_dir.rglob("*.py")):
        source, tree = read_and_parse(path)
        rel = path.relative_to(repo_root).as_posix()
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = _called_name(node)
            if name in _PROMPT_SINKS and not _line_has_marker(source, node.lineno):
                violations.append(
                    f"{rel}:{node.lineno}: inbound package calls prompt sink "
                    f"{name!r}; raw human text must not reach a prompt here"
                )
    return violations


def _is_dispatcher_resume(call: ast.Call) -> bool:
    """Whether *call* is the ``self._dispatcher.resume(...)`` hand-off.

    Returns:
        ``True`` for a call on the router's own dispatcher attribute.
    """
    func = call.func
    if not isinstance(func, ast.Attribute) or func.attr != _RESUME_CALL:
        return False
    owner = func.value
    return (
        isinstance(owner, ast.Attribute)
        and owner.attr == _DISPATCHER_ATTR
        and isinstance(owner.value, ast.Name)
        and owner.value.id == "self"
    )


def _router_entrypoint(
    tree: ast.Module,
) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    """Return the router class's ``route`` method, if it exists.

    Returns:
        The entrypoint method node, or ``None`` when the class or the
        method is absent.
    """
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef) or node.name != _ROUTER_CLASS:
            continue
        for stmt in node.body:
            if (
                isinstance(stmt, ast.FunctionDef | ast.AsyncFunctionDef)
                and stmt.name == _ROUTER_ENTRYPOINT
            ):
                return stmt
    return None


def _fenced_handoff_present(repo_root: Path) -> bool:
    """Whether the router's resume dispatch forwards ``decision_reason=``.

    Bound to the one call that actually carries inbound text out of the
    package: a *reachable* ``self._dispatcher.resume(...)`` inside
    ``InboundResumeRouter.route``. Accepting any ``resume(...)`` anywhere
    in the module would let a helper nobody calls -- or a second, unused
    dispatch kept only to satisfy the gate -- vouch for a real entrypoint
    that had quietly dropped the fenced keyword.

    Returns:
        ``True`` when the router entrypoint's dispatcher hand-off passes
        the ``decision_reason=`` keyword.
    """
    _source, tree = read_and_parse(repo_root / _ROUTER_REL)
    entrypoint = _router_entrypoint(tree)
    if entrypoint is None:
        return False
    for stmt in reachable_statements(entrypoint.body):
        for node in statement_expressions(stmt):
            if not isinstance(node, ast.Call) or not _is_dispatcher_resume(node):
                continue
            if any(kw.arg == _FENCED_KWARG for kw in node.keywords):
                return True
    return False


def _check(repo_root: Path) -> list[str]:
    """Run both structural checks over the inbound package.

    Returns:
        A list of human-readable violation messages (empty when clean).
    """
    violations = _prompt_sink_violations(repo_root)
    if not _fenced_handoff_present(repo_root):
        violations.append(
            f"{_ROUTER_REL}: the router's {_RESUME_CALL}(...) dispatch must "
            f"forward inbound text via a {_FENCED_KWARG}= keyword (the "
            f"fenced resume sink)"
        )
    return violations


def main() -> int:
    """Run the chat-inbound fencing gate.

    Returns:
        The process exit code (0 clean, 1 violations, 2 config error).
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    args = parser.parse_args()
    try:
        violations = _check(args.repo_root)
    except GateSourceError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if violations:
        print("Chat-inbound fencing check failed:", file=sys.stderr)
        for violation in violations:
            print(f"  {violation}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
