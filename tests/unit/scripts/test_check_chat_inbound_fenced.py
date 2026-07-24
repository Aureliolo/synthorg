"""Tests for the chat-inbound fencing gate.

The fenced hand-off check is bound to the router's actual resume
dispatch: a ``decision_reason=`` keyword sitting on some other call is
dead code, and accepting it would let the fencing contract be dropped
from the real dispatch while the gate stayed green.
"""

import importlib.util
from pathlib import Path
from typing import Protocol, cast

import pytest

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parents[3]
_INBOUND_REL = "src/synthorg/integrations/chat_api/inbound"


class _GateModule(Protocol):
    """Subset of ``scripts/check_chat_inbound_fenced.py`` the tests exercise."""

    @staticmethod
    def _check(repo_root: Path) -> list[str]: ...


def _load_module() -> _GateModule:
    script_path = _REPO_ROOT / "scripts" / "check_chat_inbound_fenced.py"
    spec = importlib.util.spec_from_file_location(
        "check_chat_inbound_fenced",
        script_path,
    )
    if spec is None or spec.loader is None:
        msg = f"could not load module spec for {script_path}"
        raise RuntimeError(msg)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return cast(_GateModule, module)


_MODULE = _load_module()


def _make_tree(tmp_path: Path, files: dict[str, str]) -> Path:
    """Materialise a synthetic inbound package under *tmp_path*.

    Returns:
        The synthetic repository root.
    """
    inbound_dir = tmp_path / _INBOUND_REL
    inbound_dir.mkdir(parents=True)
    for name, body in files.items():
        (inbound_dir / name).write_text(body, encoding="utf-8")
    return tmp_path


_ROUTER_OK = """\
async def route(event, dispatcher):
    return await dispatcher.resume(
        approval_id=event.approval_id,
        approved=True,
        decided_by=event.user,
        decision_reason=event.text,
    )
"""


def test_canonical_router_passes(tmp_path: Path) -> None:
    root = _make_tree(tmp_path, {"router.py": _ROUTER_OK})
    assert _MODULE._check(root) == []


def test_decision_reason_on_unrelated_call_flagged(tmp_path: Path) -> None:
    """A ``decision_reason=`` keyword away from the resume is not a hand-off.

    The token is present, so a bare text search passes, but the resume
    dispatch itself forwards nothing and the fencing contract is gone.
    """
    router = """\
async def route(event, dispatcher):
    _audit(decision_reason=event.text)
    return await dispatcher.resume(
        approval_id=event.approval_id,
        approved=True,
        decided_by=event.user,
    )
"""
    root = _make_tree(tmp_path, {"router.py": router})
    violations = _MODULE._check(root)
    assert len(violations) == 1
    assert "decision_reason" in violations[0]


def test_dead_resume_call_does_not_satisfy_the_gate(tmp_path: Path) -> None:
    """A fenced dispatch parked after an early return never runs."""
    router = """\
async def route(event, dispatcher):
    return await dispatcher.publish(event)
    return await dispatcher.resume(
        approval_id=event.approval_id,
        approved=True,
        decided_by=event.user,
        decision_reason=event.text,
    )
"""
    root = _make_tree(tmp_path, {"router.py": router})
    assert len(_MODULE._check(root)) == 1


def test_prompt_sink_in_a_nested_module_flagged(tmp_path: Path) -> None:
    """The package's subdirectories are part of the package."""
    root = _make_tree(tmp_path, {"router.py": _ROUTER_OK})
    nested = root / _INBOUND_REL / "handlers"
    nested.mkdir()
    (nested / "summarise.py").write_text(
        "async def run(text, provider):\n"
        "    return await complete_text(provider, text)\n",
        encoding="utf-8",
    )
    violations = _MODULE._check(root)
    assert len(violations) == 1
    assert "handlers/summarise.py" in violations[0]


def test_router_without_resume_dispatch_flagged(tmp_path: Path) -> None:
    router = """\
async def route(event, dispatcher):
    return await dispatcher.publish(event)
"""
    root = _make_tree(tmp_path, {"router.py": router})
    assert len(_MODULE._check(root)) == 1


def test_prompt_sink_in_inbound_package_flagged(tmp_path: Path) -> None:
    """Raw human text must not reach an LLM completion inside the package."""
    sink = """\
async def summarise(text, provider):
    return await complete_text(provider, text)
"""
    root = _make_tree(tmp_path, {"router.py": _ROUTER_OK, "summary.py": sink})
    violations = _MODULE._check(root)
    assert len(violations) == 1
    assert "complete_text" in violations[0]


def test_prompt_sink_with_annotated_opt_out_passes(tmp_path: Path) -> None:
    sink = """\
async def summarise(text, provider):
    return await complete_text(  # lint-allow: chat-inbound-fenced -- fenced above
        provider, text
    )
"""
    root = _make_tree(tmp_path, {"router.py": _ROUTER_OK, "summary.py": sink})
    assert _MODULE._check(root) == []


def test_real_tree_is_clean() -> None:
    assert _MODULE._check(_REPO_ROOT) == []
