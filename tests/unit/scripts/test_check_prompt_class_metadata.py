"""Unit tests for ``scripts/check_prompt_class_metadata.py``.

Exercises the AST detection (a class that tags an LLM chokepoint with a non-None
``purpose=`` but exposes no ``metadata`` property), the in-scope/out-of-scope
paths (``purpose=None``, a non-chokepoint ``purpose=`` keyword, an abstract
property, a base-only chokepoint), the ``# lint-allow: prompt-class-metadata``
suppression marker, and a regression guard that the live source tree is clean.

Tests load the script via :mod:`importlib` and call its private helpers directly,
matching ``test_check_cost_scope_purpose.py``.
"""

import importlib.util
import os
import sys
from pathlib import Path
from typing import Protocol, cast

import pytest

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _isolate_git_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Strip every ``GIT_*`` env var so the scan cannot escape ``tmp_path``.

    The gate's ``git ls-files`` subprocess inherits this process's environment;
    under a pre-push hook ``GIT_DIR`` etc. point at the real repo. Cleared, git
    resolves via ``cwd`` (a non-repo ``tmp_path``) so the only filesystem access
    is the rglob fallback over the sandbox.
    """
    for key in [k for k in os.environ if k.startswith("GIT_")]:
        monkeypatch.delenv(key, raising=False)


_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_SCRIPT_PATH = _REPO_ROOT / "scripts" / "check_prompt_class_metadata.py"


class _HitView(Protocol):
    """Structural view of the script's private ``_Hit`` class."""

    rel: str
    lineno: int
    name: str

    def message(self) -> str: ...


class _ScriptModule(Protocol):
    """Subset of the script's surface the tests exercise."""

    @staticmethod
    def _scan_file(path: Path, rel: str) -> list[_HitView]: ...
    @staticmethod
    def _is_valid_marker(comment_token: str) -> bool: ...
    @staticmethod
    def cmd_scan_all(project_root: Path | None = None) -> int: ...
    @staticmethod
    def main(argv: list[str] | None = None) -> int: ...


def _load_script() -> _ScriptModule:
    saved = sys.path[:]
    try:
        spec = importlib.util.spec_from_file_location(
            "_check_prompt_class_metadata",
            _SCRIPT_PATH,
        )
        assert spec is not None
        assert spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return cast(_ScriptModule, module)
    finally:
        sys.path[:] = saved


_MODULE = _load_script()


class WritePy(Protocol):
    """Callable signature of the ``write_py`` fixture."""

    def __call__(self, content: str, name: str = ...) -> Path: ...


@pytest.fixture
def write_py(tmp_path: Path) -> WritePy:
    """Helper that writes a Python source string to ``tmp_path/<name>``."""

    def _write(content: str, name: str = "sample.py") -> Path:
        path = tmp_path / name
        path.write_text(content, encoding="utf-8")
        return path

    return _write


_MISSING_METADATA = """\
class Proposer:
    _PURPOSE_ID = PromptPurposeId.PROCEDURAL_PROPOSE

    async def run(self) -> None:
        async with cost_recording_scope(
            cost_tracker=self._cost_tracker,
            agent_id=agent_id,
            task_id=task_id,
            purpose=self._PURPOSE_ID,
            call_category=category,
        ):
            await provider.complete(messages, model)
"""

_WITH_METADATA = """\
class Proposer:
    _PURPOSE_ID = PromptPurposeId.PROCEDURAL_PROPOSE

    @property
    def metadata(self) -> ModelPinMetadata:
        return pin_for(self._PURPOSE_ID)

    async def run(self) -> None:
        async with cost_recording_scope(
            cost_tracker=self._cost_tracker,
            agent_id=agent_id,
            task_id=task_id,
            purpose=self.metadata.prompt_class_id,
            call_category=category,
        ):
            await provider.complete(messages, model)
"""

_ABSTRACT_METADATA = """\
class Base:
    @property
    @abstractmethod
    def metadata(self) -> ModelPinMetadata: ...

    async def run(self) -> None:
        async with cost_recording_scope(
            cost_tracker=self._cost_tracker,
            agent_id=agent_id,
            task_id=task_id,
            purpose=self.metadata.prompt_class_id,
            call_category=category,
        ):
            await provider.complete(messages, model)
"""

_PURPOSE_NONE = """\
class PerTask:
    async def run(self) -> None:
        async with cost_recording_scope(
            cost_tracker=self._cost_tracker,
            agent_id=agent_id,
            task_id=task_id,
            purpose=None,
            call_category=category,
        ):
            await provider.complete(messages, model)
"""

_NON_CHOKEPOINT_PURPOSE = """\
class ReceiptBuilder:
    def build(self) -> None:
        record_execution(
            workspace=workspace,
            purpose=CodeExecutionPurpose.TESTS,
        )
"""

_COMPLETE_TEXT_HELPER = """\
class Triage:
    _PURPOSE_ID = PromptPurposeId.RESEARCH_TRIAGE

    async def run(self) -> None:
        content, cost = await complete_text(
            self._provider,
            self._model,
            system=system,
            user=user,
            cost_tracker=self._cost_tracker,
            task_id=task_id,
            purpose=PromptPurposeId.RESEARCH_TRIAGE,
        )
"""

_MARKER = """\
class LegacyProposer:
    # lint-allow: prompt-class-metadata -- agent-execution class, pin per identity
    async def run(self) -> None:
        async with cost_recording_scope(
            cost_tracker=self._cost_tracker,
            agent_id=agent_id,
            task_id=task_id,
            purpose=self._PURPOSE_ID,
            call_category=category,
        ):
            await provider.complete(messages, model)
"""


def test_missing_metadata_is_flagged(write_py: WritePy) -> None:
    path = write_py(_MISSING_METADATA)
    hits = _MODULE._scan_file(path, "sample.py")
    assert [h.name for h in hits] == ["Proposer"]


def test_concrete_metadata_passes(write_py: WritePy) -> None:
    path = write_py(_WITH_METADATA)
    assert _MODULE._scan_file(path, "sample.py") == []


def test_abstract_metadata_passes(write_py: WritePy) -> None:
    path = write_py(_ABSTRACT_METADATA)
    assert _MODULE._scan_file(path, "sample.py") == []


def test_purpose_none_is_out_of_scope(write_py: WritePy) -> None:
    path = write_py(_PURPOSE_NONE)
    assert _MODULE._scan_file(path, "sample.py") == []


def test_non_chokepoint_purpose_ignored(write_py: WritePy) -> None:
    path = write_py(_NON_CHOKEPOINT_PURPOSE)
    assert _MODULE._scan_file(path, "sample.py") == []


def test_complete_text_helper_in_scope(write_py: WritePy) -> None:
    path = write_py(_COMPLETE_TEXT_HELPER)
    assert [h.name for h in _MODULE._scan_file(path, "sample.py")] == ["Triage"]


def test_lint_allow_marker_suppresses(write_py: WritePy) -> None:
    path = write_py(_MARKER)
    assert _MODULE._scan_file(path, "sample.py") == []


@pytest.mark.parametrize(
    ("comment", "expected"),
    [
        ("# lint-allow: prompt-class-metadata -- agent-exec", True),
        ("# lint-allow: prompt-class-metadata --", False),
        ("# lint-allow: prompt-class-metadata", False),
        ("# lint-allow: cost-scope-purpose -- other gate", False),
    ],
)
def test_marker_validation(comment: str, *, expected: bool) -> None:
    assert _MODULE._is_valid_marker(comment) is expected


def test_main_clean_tree_returns_zero(tmp_path: Path) -> None:
    src = tmp_path / "src" / "synthorg"
    src.mkdir(parents=True)
    (src / "clean.py").write_text(_WITH_METADATA, encoding="utf-8")
    assert _MODULE.main(["--repo-root", str(tmp_path)]) == 0


def test_main_violation_returns_one(tmp_path: Path) -> None:
    src = tmp_path / "src" / "synthorg"
    src.mkdir(parents=True)
    (src / "bad.py").write_text(_MISSING_METADATA, encoding="utf-8")
    assert _MODULE.main(["--repo-root", str(tmp_path)]) == 1


def test_main_bad_repo_root_returns_two() -> None:
    assert _MODULE.main(["--repo-root", "/no/such/path/xyzzy"]) == 2


def test_main_existing_dir_without_scan_root_returns_two(tmp_path: Path) -> None:
    # An existing directory that is not the repo root must fail closed rather
    # than scan zero files and exit 0, silently disabling the gate.
    assert _MODULE.main(["--repo-root", str(tmp_path)]) == 2
