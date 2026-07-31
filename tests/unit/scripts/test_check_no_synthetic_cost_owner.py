"""Unit tests for ``scripts/check_no_synthetic_cost_owner.py``.

The gate exists because three regex sweeps of one tree produced three
different answers about how many fabricated owner ids remained. Each shape
below is one a regex missed: a literal wrapped across lines, an ``or``
fallback, an f-string, a bare identifier that only looks derived. They are
kept verbatim so the gate is tested against the thing it was written for
rather than against a tidied-up paraphrase.

Tests load the script via :mod:`importlib` and call its private helpers
directly, matching the pattern in ``test_check_cost_scope_purpose.py``.
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
    """Strip every ``GIT_*`` env var for the duration of each test.

    The gate's ``git ls-files`` subprocess inherits this process's
    environment. Under a pre-push hook ``GIT_DIR`` / ``GIT_WORK_TREE`` point
    at the real repo, which would let the scan escape the ``tmp_path``
    sandbox and read the live tree. A test must NEVER touch real repo data.
    """
    for key in [k for k in os.environ if k.startswith("GIT_")]:
        monkeypatch.delenv(key, raising=False)


_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_SCRIPT_PATH = _REPO_ROOT / "scripts" / "check_no_synthetic_cost_owner.py"


class _HitView(Protocol):
    """Structural view of the script's private ``_Hit`` class."""

    rel: str
    lineno: int
    col: int
    keyword: str
    call: str

    def message(self) -> str: ...


class _ScriptModule(Protocol):
    """Subset of the script's surface the tests exercise."""

    @staticmethod
    def _scan_file(path: Path, rel: str) -> list[_HitView]: ...
    @staticmethod
    def _is_valid_marker(comment_token: str) -> bool: ...
    @staticmethod
    def main(argv: list[str] | None = None) -> int: ...


def _load_script() -> _ScriptModule:
    # The gate prepends scripts/ to sys.path at import time (to resolve its
    # _gate_source sibling); restore sys.path so the load leaves no global
    # side effect that could shadow an unrelated import.
    saved = sys.path[:]
    try:
        spec = importlib.util.spec_from_file_location(
            "_check_no_synthetic_cost_owner",
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


# Each of these shipped to production and dropped its spend on the floor.
_WRAPPED_ACROSS_LINES = """\
async def run() -> None:
    async with cost_recording_scope(
        cost_tracker=tracker,
        task_id=NotBlankStr(
            f"system:client:requirement_generator:{context.project_id}"
        ),
        purpose=purpose,
    ):
        pass
"""

_SINGLE_LINE_FSTRING = """\
async def run() -> None:
    async with cost_recording_scope(
        cost_tracker=tracker,
        task_id=NotBlankStr(f"compaction:{execution_id}"),
        purpose=purpose,
    ):
        pass
"""

_BARE_FSTRING = """\
async def run() -> None:
    async with cost_recording_scope(
        cost_tracker=tracker,
        task_id=f"evaluate:{lead.id}",
        purpose=purpose,
    ):
        pass
"""

_OR_FALLBACK = """\
async def run() -> None:
    async with cost_recording_scope(
        cost_tracker=tracker,
        agent_id=responder.agent_id or NotBlankStr("system"),
        purpose=purpose,
    ):
        pass
"""

_PLAIN_LITERAL = """\
def build() -> CostRecord:
    return CostRecord(
        agent_id=NotBlankStr("system"),
        provider=NotBlankStr(provider),
    )
"""

_COMPLETE_TEXT = """\
async def run() -> None:
    content, _cost = await complete_text(
        provider,
        model_id,
        task_id=NotBlankStr(f"system:providers:tier_classification:{name}"),
        purpose=purpose,
    )
"""


@pytest.mark.parametrize(
    ("source", "keyword"),
    [
        (_WRAPPED_ACROSS_LINES, "task_id"),
        (_SINGLE_LINE_FSTRING, "task_id"),
        (_BARE_FSTRING, "task_id"),
        (_OR_FALLBACK, "agent_id"),
        (_PLAIN_LITERAL, "agent_id"),
        (_COMPLETE_TEXT, "task_id"),
    ],
)
def test_flags_every_fabricated_shape(
    write_py: WritePy, source: str, keyword: str
) -> None:
    path = write_py(source)
    hits = _MODULE._scan_file(path, "sample.py")
    assert len(hits) == 1
    assert hits[0].keyword == keyword


_DERIVED_VALUES = """\
async def run() -> None:
    async with cost_recording_scope(
        cost_tracker=tracker,
        agent_id=NotBlankStr(str(lead.id)),
        task_id=review_input.task_id,
        purpose=purpose,
    ):
        pass
"""

_UNOWNED = """\
async def run() -> None:
    async with cost_recording_scope(
        cost_tracker=tracker,
        task_id=None,
        purpose=purpose,
    ):
        pass
"""

_OMITTED = """\
async def run() -> None:
    async with cost_recording_scope(
        cost_tracker=tracker,
        purpose=purpose,
    ):
        pass
"""


@pytest.mark.parametrize(
    "source",
    [_DERIVED_VALUES, _UNOWNED, _OMITTED],
    ids=["derived", "explicit-none", "omitted"],
)
def test_accepts_honest_owners(write_py: WritePy, source: str) -> None:
    path = write_py(source)
    assert _MODULE._scan_file(path, "sample.py") == []


_NON_COST_CALL = """\
def build() -> OrgFactAuthor:
    # A synthetic id on a non-cost model is not this gate's business: it
    # writes to no foreign key and drops no spend.
    return OrgFactAuthor(agent_id=NotBlankStr("system-ontology-sync"))
"""


def test_ignores_synthetic_ids_outside_cost_chokepoints(write_py: WritePy) -> None:
    path = write_py(_NON_COST_CALL)
    assert _MODULE._scan_file(path, "sample.py") == []


_SUPPRESSED = """\
def build() -> CostRecord:
    return CostRecord(
        agent_id=NotBlankStr("system"),  # lint-allow: synthetic-cost-owner -- why
        provider=NotBlankStr(provider),
    )
"""


def test_per_line_marker_suppresses(write_py: WritePy) -> None:
    path = write_py(_SUPPRESSED)
    assert _MODULE._scan_file(path, "sample.py") == []


@pytest.mark.parametrize(
    ("comment", "valid"),
    [
        ("# lint-allow: synthetic-cost-owner -- the id is a real task", True),
        ("# lint-allow: synthetic-cost-owner --", False),
        ("# lint-allow: synthetic-cost-owner", False),
        ("# lint-allow: something-else -- reason", False),
    ],
)
def test_marker_requires_a_justification(comment: str, valid: bool) -> None:
    assert _MODULE._is_valid_marker(comment) is valid


def test_live_tree_is_clean() -> None:
    # The gate ships with no baseline, so the tree it lands on must already
    # be clean; a regression here is a real dropped cost record, not a
    # bookkeeping chore.
    assert _MODULE.main([]) == 0
