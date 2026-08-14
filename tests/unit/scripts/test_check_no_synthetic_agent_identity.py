"""Unit tests for ``scripts/check_no_synthetic_agent_identity.py``.

The gate guards against an identity built at boot from a catalogued role and
dispatched as though it were an agent, which reads entirely reasonable at its
construction site. The shapes below are that defect and its near neighbours.

Tests load the script via :mod:`importlib` and call its private helpers
directly, matching the pattern in ``test_check_no_synthetic_cost_owner.py``.
"""

import importlib.util
import os
import sys
from collections.abc import Mapping
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
_SCRIPT_PATH = _REPO_ROOT / "scripts" / "check_no_synthetic_agent_identity.py"


class _HitView(Protocol):
    """Structural view of the script's private ``_Hit`` class."""

    rel: str
    lineno: int
    col: int

    def message(self) -> str: ...


class _ScriptModule(Protocol):
    """Subset of the script's surface the tests exercise."""

    _ROSTER_CONSTRUCTION_PATHS: Mapping[str, str]

    @staticmethod
    def _scan_file(path: Path, rel: str) -> tuple[list[_HitView], int]: ...
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
            "_check_no_synthetic_agent_identity",
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


# The defect verbatim: a catalogued role turned into a dispatchable identity
# that no operator can see, staff, or compare against another agent.
_BOOT_SINGLETON = """\
def build_completion_reviewer_identity(role: Role, model: ModelConfig):
    return AgentIdentity(
        id=uuid4(),
        name="Completion Reviewer",
        role=role.name,
        department=role.department,
        model=model,
        hiring_date=date.today(),
    )
"""

_SUPPRESSED = """\
def build_probe_identity(model: ModelConfig):
    return AgentIdentity(  # lint-allow: synthetic-agent-identity -- probe
        id=uuid4(),
        name="Probe",
        role="Probe",
        department="engineering",
        model=model,
        hiring_date=date.today(),
    )
"""

_UNJUSTIFIED_SUPPRESSION = """\
def build_probe_identity(model: ModelConfig):
    return AgentIdentity(  # lint-allow: synthetic-agent-identity
        id=uuid4(),
        name="Probe",
        role="Probe",
        department="engineering",
        model=model,
        hiring_date=date.today(),
    )
"""

_SELECTION_NOT_CONSTRUCTION = """\
async def select(registry, role: str):
    holders = await registry.list_by_role(role)
    return holders[0] if holders else None
"""

_ANNOTATION_ONLY = """\
def dispatch(reviewer: AgentIdentity) -> None:
    del reviewer
"""


class TestScanFile:
    """``_scan_file`` on individual construction shapes."""

    def test_a_boot_singleton_is_flagged(self, write_py: WritePy) -> None:
        path = write_py(_BOOT_SINGLETON)

        hits, count = _MODULE._scan_file(path, "src/synthorg/engine/reviewer.py")

        assert count == 1
        assert len(hits) == 1
        assert "outside the roster" in hits[0].message()

    def test_a_declared_roster_path_is_exempt(self, write_py: WritePy) -> None:
        path = write_py(_BOOT_SINGLETON)
        declared = next(iter(_MODULE._ROSTER_CONSTRUCTION_PATHS))

        hits, count = _MODULE._scan_file(path, declared)

        assert not hits
        # The count still reports: it is what proves the declaration is live.
        assert count == 1

    def test_a_justified_marker_suppresses(self, write_py: WritePy) -> None:
        path = write_py(_SUPPRESSED)

        hits, _ = _MODULE._scan_file(path, "src/synthorg/engine/reviewer.py")

        assert not hits

    def test_a_marker_without_a_reason_does_not_suppress(
        self, write_py: WritePy
    ) -> None:
        # Every legitimate exception is a claim about intent, and the reason is
        # the only place that claim gets written down.
        path = write_py(_UNJUSTIFIED_SUPPRESSION)

        hits, _ = _MODULE._scan_file(path, "src/synthorg/engine/reviewer.py")

        assert len(hits) == 1

    def test_selecting_a_holder_is_not_a_construction(self, write_py: WritePy) -> None:
        path = write_py(_SELECTION_NOT_CONSTRUCTION)

        hits, count = _MODULE._scan_file(path, "src/synthorg/engine/reviewer.py")

        assert not hits
        assert count == 0

    def test_an_annotation_is_not_a_construction(self, write_py: WritePy) -> None:
        path = write_py(_ANNOTATION_ONLY)

        hits, count = _MODULE._scan_file(path, "src/synthorg/engine/reviewer.py")

        assert not hits
        assert count == 0

    @pytest.mark.parametrize(
        "constructor",
        ["model_validate", "model_validate_json", "model_construct"],
    )
    def test_a_class_level_constructor_is_a_construction(
        self, write_py: WritePy, constructor: str
    ) -> None:
        """Pydantic offers three other ways to mint the same object.

        Each takes untyped input and hands back a whole identity, so a gate
        that watched only the class call would be one ``model_construct``
        away from the singleton it exists to prevent.
        """
        path = write_py(
            "from synthorg.core.agent import AgentIdentity\n\n\n"
            "def build() -> AgentIdentity:\n"
            f"    return AgentIdentity.{constructor}(_PAYLOAD)\n"
        )

        hits, count = _MODULE._scan_file(path, "src/synthorg/engine/reviewer.py")

        assert count == 1
        assert len(hits) == 1

    def test_an_aliased_import_is_still_the_class(self, write_py: WritePy) -> None:
        """Renaming the import must not be a way past the gate.

        There is no baseline here, so detection IS the guarantee; a gate
        matched on one spelling is a gate with a rename-shaped hole.
        """
        path = write_py(
            "from synthorg.core.agent import AgentIdentity as Identity\n\n\n"
            "def build() -> Identity:\n"
            "    return Identity(name='Ada', role='Completion Reviewer')\n"
        )

        hits, count = _MODULE._scan_file(path, "src/synthorg/engine/reviewer.py")

        assert count == 1
        assert len(hits) == 1

    def test_a_module_level_alias_is_still_the_class(self, write_py: WritePy) -> None:
        path = write_py(
            "from synthorg.core.agent import AgentIdentity\n\n"
            "Identity = AgentIdentity\n\n\n"
            "def build() -> Identity:\n"
            "    return Identity(name='Ada', role='Red Team')\n"
        )

        hits, count = _MODULE._scan_file(path, "src/synthorg/engine/reviewer.py")

        assert count == 1
        assert len(hits) == 1

    def test_a_module_qualified_call_is_still_the_class(
        self, write_py: WritePy
    ) -> None:
        """Reaching the class through its module mints the same identity.

        ``import ... as agent`` never binds ``AgentIdentity`` as a local
        name, so a gate that only tracked imported names let this shape
        through while the call still produced a roster-less reviewer.
        """
        path = write_py(
            "import synthorg.core.agent as agent\n\n\n"
            "def build() -> agent.AgentIdentity:\n"
            "    return agent.AgentIdentity(name='Ada', role='Red Team')\n"
        )

        hits, count = _MODULE._scan_file(path, "src/synthorg/engine/reviewer.py")

        assert count == 1
        assert len(hits) == 1

    def test_a_module_qualified_class_constructor_is_a_construction(
        self, write_py: WritePy
    ) -> None:
        path = write_py(
            "from synthorg.core import agent\n\n\n"
            "def build(payload: dict[str, str]) -> agent.AgentIdentity:\n"
            "    return agent.AgentIdentity.model_validate(payload)\n"
        )

        hits, count = _MODULE._scan_file(path, "src/synthorg/engine/reviewer.py")

        assert count == 1
        assert len(hits) == 1

    def test_a_fully_dotted_call_is_a_construction(self, write_py: WritePy) -> None:
        """``import synthorg.core.agent`` binds the root, not the module.

        The call site still writes the whole dotted path, so that spelling
        is what the gate has to recognise.
        """
        path = write_py(
            "import synthorg.core.agent\n\n\n"
            "def build(payload: dict[str, str]) -> object:\n"
            "    return synthorg.core.agent.AgentIdentity.model_construct(**payload)\n"
        )

        hits, count = _MODULE._scan_file(path, "src/synthorg/engine/reviewer.py")

        assert count == 1
        assert len(hits) == 1

    def test_a_module_qualified_instance_copy_is_not_a_construction(
        self, write_py: WritePy
    ) -> None:
        """The module spelling must not widen what counts as a mint."""
        path = write_py(
            "import synthorg.core.agent as agent\n\n\n"
            "def confine(reviewer: agent.AgentIdentity) -> agent.AgentIdentity:\n"
            "    return reviewer.model_copy(update={'autonomy_level': 'supervised'})\n"
        )

        hits, count = _MODULE._scan_file(path, "src/synthorg/engine/reviewer.py")

        assert not hits
        assert count == 0

    def test_an_unrelated_module_attribute_is_not_a_construction(
        self, write_py: WritePy
    ) -> None:
        """Only the module that defines the class counts."""
        path = write_py(
            "import some.other.agent as agent\n\n\n"
            "def build() -> object:\n"
            "    return agent.AgentIdentity(name='Ada', role='Red Team')\n"
        )

        hits, count = _MODULE._scan_file(path, "src/synthorg/engine/reviewer.py")

        assert not hits
        assert count == 0

    def test_narrowing_a_selected_agent_is_not_a_construction(
        self, write_py: WritePy
    ) -> None:
        """``model_copy`` derives; it does not mint.

        Whatever it produces started life on the roster, and narrowing the
        agent a gate selected before dispatching it is exactly how the gate
        is meant to work.
        """
        path = write_py(
            "from synthorg.core.agent import AgentIdentity\n\n\n"
            "def confine(reviewer: AgentIdentity) -> AgentIdentity:\n"
            "    return reviewer.model_copy(update={'autonomy_level': 'supervised'})\n"
        )

        hits, count = _MODULE._scan_file(path, "src/synthorg/engine/reviewer.py")

        assert not hits
        assert count == 0


class TestMarkerValidation:
    """``_is_valid_marker`` accepts only a justified marker."""

    @pytest.mark.parametrize(
        ("comment", "expected"),
        [
            ("# lint-allow: synthetic-agent-identity -- probe fixture", True),
            ("# lint-allow: synthetic-agent-identity --", False),
            ("# lint-allow: synthetic-agent-identity", False),
            ("# lint-allow: synthetic-cost-owner -- wrong gate", False),
            ("# noqa", False),
        ],
    )
    def test_marker_shapes(self, comment: str, expected: bool) -> None:
        assert _MODULE._is_valid_marker(comment) is expected


class TestEndToEnd:
    """``main`` against a sandboxed tree."""

    def test_a_clean_tree_passes(self, tmp_path: Path) -> None:
        # Every declared path present and constructing, nothing else does.
        for rel in _MODULE._ROSTER_CONSTRUCTION_PATHS:
            path = tmp_path / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(_BOOT_SINGLETON, encoding="utf-8")

        assert _MODULE.main(["--repo-root", str(tmp_path)]) == 0

    def test_a_new_construction_fails(self, tmp_path: Path) -> None:
        for rel in _MODULE._ROSTER_CONSTRUCTION_PATHS:
            path = tmp_path / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(_BOOT_SINGLETON, encoding="utf-8")
        intruder = tmp_path / "src/synthorg/engine/reviewer_identity.py"
        intruder.parent.mkdir(parents=True, exist_ok=True)
        intruder.write_text(_BOOT_SINGLETON, encoding="utf-8")

        assert _MODULE.main(["--repo-root", str(tmp_path)]) == 1

    def test_a_declaration_that_outlived_its_site_is_a_config_error(
        self, tmp_path: Path
    ) -> None:
        # An exemption nobody uses is one the next construction inherits.
        declared = list(_MODULE._ROSTER_CONSTRUCTION_PATHS)
        for rel in declared[1:]:
            path = tmp_path / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(_BOOT_SINGLETON, encoding="utf-8")
        stale = tmp_path / declared[0]
        stale.parent.mkdir(parents=True, exist_ok=True)
        stale.write_text(_SELECTION_NOT_CONSTRUCTION, encoding="utf-8")

        assert _MODULE.main(["--repo-root", str(tmp_path)]) == 2

    def test_an_unreadable_repo_root_is_a_config_error(self, tmp_path: Path) -> None:
        assert _MODULE.main(["--repo-root", str(tmp_path / "absent")]) == 2
