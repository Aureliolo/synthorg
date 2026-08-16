"""Unit tests for ``scripts/check_web_no_id_render.py``.

The flagged shapes are the ones a live run put in front of an operator: a
cockpit row headed by an agent UUID, an audit table whose actor column was a
key, a plan card owned by ``agent-7f3c...``. The unflagged shapes are the ways
an id is legitimately used, and the gate is worth nothing if it cannot tell
them apart: a React ``key``, a route parameter, a ``value`` on an option.

Tests load the script via :mod:`importlib` and drive it over a tmp tree,
matching ``test_check_no_ghost_attribute_read.py``.
"""

import importlib.util
import sys
from pathlib import Path
from typing import Protocol, cast

import pytest

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_SCRIPT_PATH = _REPO_ROOT / "scripts" / "check_web_no_id_render.py"


class _ScriptModule(Protocol):
    """Subset of the script's surface the tests exercise."""

    @staticmethod
    def _violations(path: Path, source: str) -> list[str]: ...
    @staticmethod
    def _scan(root: Path) -> list[str]: ...


def _load_script() -> _ScriptModule:
    saved = sys.path[:]
    try:
        spec = importlib.util.spec_from_file_location(
            "_check_web_no_id_render", _SCRIPT_PATH
        )
        assert spec is not None
        assert spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return cast(_ScriptModule, module)
    finally:
        sys.path[:] = saved


_MODULE = _load_script()


def _hits(source: str) -> list[str]:
    return _MODULE._violations(Path("web/src/pages/Sample.tsx"), source)


class WriteTsx(Protocol):
    """Callable signature of the ``write_tsx`` fixture."""

    def __call__(self, rel: str, content: str) -> Path: ...


@pytest.fixture
def write_tsx(tmp_path: Path) -> WriteTsx:
    """Write a ``.tsx`` source under a throwaway ``web/src`` tree."""

    def _write(rel: str, content: str) -> Path:
        path = tmp_path / "web" / "src" / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    return _write


class TestFlaggedShapes:
    def test_a_bare_agent_id_as_a_text_child(self) -> None:
        source = '<span className="truncate">{activity.agent_id}</span>'
        assert len(_hits(source)) == 1
        assert "agent_id" in _hits(source)[0]

    def test_an_id_with_a_fallback_is_still_an_id(self) -> None:
        # The fallback is what makes it look safe: it prints the key whenever
        # the key is there, which is almost always.
        source = "<td>{record.agent_id ?? 'system'}</td>"
        assert len(_hits(source)) == 1

    def test_an_optional_chain_reaches_the_same_field(self) -> None:
        source = "<span>{approval.agent?.requested_by}</span>"
        assert len(_hits(source)) == 1

    def test_every_declared_reference_is_caught(self) -> None:
        for field in ("assigned_to", "owner", "lead", "task_id", "plan_id"):
            assert _hits(f"<span>{{item.{field}}}</span>"), field

    def test_two_adjacent_containers_are_both_read(self) -> None:
        # They share the brace between them, so consuming the trailing
        # delimiter would report the first and walk past the second.
        source = "<span>{task.task_id}{task.assigned_to}</span>"
        hits = _hits(source)

        assert len(hits) == 2
        assert "task_id" in hits[0]
        assert "assigned_to" in hits[1]

    def test_the_line_number_points_at_the_render(self) -> None:
        source = "<div>\n  <p>fine</p>\n  <span>{task.assigned_to}</span>\n</div>"
        assert ":3:" in _hits(source)[0]


class TestUnflaggedShapes:
    def test_a_react_key_is_not_a_render(self) -> None:
        assert _hits("<Row key={task.task_id} task={task} />") == []

    def test_a_route_parameter_is_not_a_render(self) -> None:
        source = "<Link to={ROUTES.TASK.replace(':id', task.task_id)}>Open</Link>"
        assert _hits(source) == []

    def test_an_option_value_is_not_a_render(self) -> None:
        assert _hits("<option value={agent.agent_id}>{agent.name}</option>") == []

    def test_the_resolved_name_beside_it_is_the_point(self) -> None:
        assert _hits("<span>{task.assigned_to_name ?? 'Unassigned'}</span>") == []

    def test_a_reference_with_no_human_name_is_not_declared(self) -> None:
        # A model id IS what an operator picks a model by; a backup and a
        # workflow node have no name at all, so "unknown" would lose the only
        # handle there is.
        for field in ("model_id", "backup_id", "node_id", "execution_id"):
            assert _hits(f"<span>{{row.{field}}}</span>") == [], field

    def test_a_call_result_is_not_a_bare_value(self) -> None:
        assert _hits("<span>{formatTask(task.task_id)}</span>") == []


class TestSuppression:
    def test_a_justified_marker_silences_the_line(self) -> None:
        source = "<span>{task.task_id}</span> {/* lint-allow: no-id-render -- x */}"
        assert _hits(source) == []

    def test_a_marker_without_a_reason_does_not(self) -> None:
        source = "<span>{task.task_id}</span> {/* lint-allow: no-id-render */}"
        assert len(_hits(source)) == 1


class TestScan:
    def test_a_clean_tree_passes(self, write_tsx: WriteTsx, tmp_path: Path) -> None:
        write_tsx("pages/Clean.tsx", "<span>{task.assigned_to_name}</span>")
        assert _MODULE._scan(tmp_path) == []

    def test_a_violation_names_its_file(
        self, write_tsx: WriteTsx, tmp_path: Path
    ) -> None:
        write_tsx("pages/Dirty.tsx", "<span>{task.assigned_to}</span>")
        messages = _MODULE._scan(tmp_path)
        assert len(messages) == 1
        assert "web/src/pages/Dirty.tsx" in messages[0]

    def test_tests_and_stories_are_exempt(
        self, write_tsx: WriteTsx, tmp_path: Path
    ) -> None:
        # Their fixtures are authored values, and a story that deliberately
        # shows the unresolved state is a legitimate thing to build.
        write_tsx("pages/Card.stories.tsx", "<span>{task.assigned_to}</span>")
        write_tsx("__tests__/Card.test.tsx", "<span>{task.assigned_to}</span>")
        assert _MODULE._scan(tmp_path) == []

    def test_a_missing_web_tree_fails_rather_than_passing(self, tmp_path: Path) -> None:
        # Silence from a scan that found nothing to scan is the failure mode
        # every whole-tree gate has to refuse.
        messages = _MODULE._scan(tmp_path)
        assert len(messages) == 1
        assert "missing" in messages[0]
