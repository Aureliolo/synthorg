"""Unit tests for ``scripts/check_dual_backend_test_parity.py``.

Two passes need coverage:

1. **Signature** -- every test under ``tests/conformance/persistence/``
   must accept a ``backend`` parameter and must NOT type-annotate any
   parameter as a concrete driver / backend (``aiosqlite.Connection``,
   ``psycopg.AsyncConnection``, ``psycopg_pool.AsyncConnectionPool``,
   ``SQLitePersistenceBackend``, ``PostgresPersistenceBackend``,
   ``SQLiteConfig``, ``PostgresConfig``).
2. **Coverage** -- every repository protocol exposed on
   ``PersistenceBackend`` must be exercised by at least one
   ``backend.<accessor>`` access in the conformance suite.

Plus the per-line opt-out marker (``# lint-allow: dual-backend-parity
-- <reason>``) and the baseline mechanism (new fails, stale warns,
``--update-baseline`` rewrites).

Tests load the script via ``importlib`` (mirroring
``test_check_persistence_boundary.py``) so private helpers are callable
directly without spawning subprocesses.
"""

import importlib.util
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_SCRIPT_PATH = _REPO_ROOT / "scripts" / "check_dual_backend_test_parity.py"


def _load_script_module() -> object:
    """Import the script as a module so its private helpers are callable."""
    spec = importlib.util.spec_from_file_location(
        "_check_dual_backend_test_parity",
        _SCRIPT_PATH,
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_MODULE = _load_script_module()


def _make_conformance_file(tmp_path: Path, name: str, source: str) -> Path:
    """Write *source* to ``tmp_path/conformance/<name>`` and return the path."""
    conformance_dir = tmp_path / "conformance"
    conformance_dir.mkdir(exist_ok=True)
    target = conformance_dir / name
    target.write_text(source, encoding="utf-8")
    return target


def _make_protocol_file(tmp_path: Path, name: str, source: str) -> Path:
    """Write *source* to ``tmp_path/protocols/<name>`` and return the path."""
    protocols_dir = tmp_path / "protocols"
    protocols_dir.mkdir(exist_ok=True)
    target = protocols_dir / name
    target.write_text(source, encoding="utf-8")
    return target


# ── signature pass: backend param required ──────────────────────


def test_canonical_signature_passes(tmp_path: Path) -> None:
    """``async def test_x(self, backend: PersistenceBackend)`` is the happy path."""
    target = _make_conformance_file(
        tmp_path,
        "test_user_repository.py",
        "import pytest\n"
        "from synthorg.persistence.protocol import PersistenceBackend\n"
        "class TestUserRepository:\n"
        "    async def test_save_and_get(self, backend: PersistenceBackend) -> None:\n"
        "        pass\n",
    )
    issues = _MODULE._scan_signature_file(  # type: ignore[attr-defined]
        target, "tests/conformance/persistence/test_user_repository.py"
    )
    assert issues == []


def test_module_level_test_function_passes(tmp_path: Path) -> None:
    """Module-level ``async def test_x(backend: PersistenceBackend)`` also passes."""
    target = _make_conformance_file(
        tmp_path,
        "test_module.py",
        "from synthorg.persistence.protocol import PersistenceBackend\n"
        "async def test_smoke(backend: PersistenceBackend) -> None:\n"
        "    pass\n",
    )
    issues = _MODULE._scan_signature_file(  # type: ignore[attr-defined]
        target, "tests/conformance/persistence/test_module.py"
    )
    assert issues == []


def test_missing_backend_param_flagged(tmp_path: Path) -> None:
    """A test method without a ``backend`` parameter is flagged."""
    target = _make_conformance_file(
        tmp_path,
        "test_no_backend.py",
        "class TestSomething:\n    async def test_solo(self) -> None:\n        pass\n",
    )
    issues = _MODULE._scan_signature_file(  # type: ignore[attr-defined]
        target, "tests/conformance/persistence/test_no_backend.py"
    )
    assert any("missing-backend-param" in msg for msg in issues)
    assert any("test_solo" in msg for msg in issues)


def test_non_test_function_not_flagged(tmp_path: Path) -> None:
    """Helper functions (not ``test_*``) are ignored."""
    target = _make_conformance_file(
        tmp_path,
        "test_helpers.py",
        "def _helper(x: int) -> int:\n"
        "    return x\n"
        "async def _builder() -> None:\n"
        "    pass\n",
    )
    issues = _MODULE._scan_signature_file(  # type: ignore[attr-defined]
        target, "tests/conformance/persistence/test_helpers.py"
    )
    assert issues == []


# ── signature pass: forbidden direct backend typing ─────────────


@pytest.mark.parametrize(
    "annotation",
    [
        "aiosqlite.Connection",
        "psycopg.AsyncConnection",
        "psycopg.Connection",
        "psycopg_pool.AsyncConnectionPool",
        "SQLitePersistenceBackend",
        "PostgresPersistenceBackend",
        "SQLiteConfig",
        "PostgresConfig",
    ],
)
def test_direct_backend_typing_flagged(annotation: str, tmp_path: Path) -> None:
    """Each concrete-backend annotation is flagged as parametrisation bypass."""
    target = _make_conformance_file(
        tmp_path,
        "test_typed.py",
        "class TestBypass:\n"
        f"    async def test_x(self, backend: {annotation}) -> None:\n"
        "        pass\n",
    )
    issues = _MODULE._scan_signature_file(  # type: ignore[attr-defined]
        target, "tests/conformance/persistence/test_typed.py"
    )
    assert any("direct-backend-typing" in msg for msg in issues)
    assert any(annotation.rsplit(".", maxsplit=1)[-1] in msg for msg in issues)


def test_direct_typing_on_secondary_param_flagged(tmp_path: Path) -> None:
    """Forbidden type on a non-``backend`` param is still flagged."""
    target = _make_conformance_file(
        tmp_path,
        "test_secondary.py",
        "from synthorg.persistence.protocol import PersistenceBackend\n"
        "class TestX:\n"
        "    async def test_y(\n"
        "        self,\n"
        "        backend: PersistenceBackend,\n"
        "        conn: psycopg.AsyncConnection,\n"
        "    ) -> None:\n"
        "        pass\n",
    )
    issues = _MODULE._scan_signature_file(  # type: ignore[attr-defined]
        target, "tests/conformance/persistence/test_secondary.py"
    )
    assert any("direct-backend-typing" in msg for msg in issues)


def test_optional_backend_typing_flagged(tmp_path: Path) -> None:
    """``backend: PostgresPersistenceBackend | None`` is still a bypass."""
    target = _make_conformance_file(
        tmp_path,
        "test_optional.py",
        "class TestX:\n"
        "    async def test_y(\n"
        "        self,\n"
        "        backend: PostgresPersistenceBackend | None,\n"
        "    ) -> None:\n"
        "        pass\n",
    )
    issues = _MODULE._scan_signature_file(  # type: ignore[attr-defined]
        target, "tests/conformance/persistence/test_optional.py"
    )
    assert any("direct-backend-typing" in msg for msg in issues)


def test_unrelated_typing_passes(tmp_path: Path) -> None:
    """``Connection`` from an unrelated module is not flagged.

    The gate matches dotted forms (``aiosqlite.Connection``,
    ``psycopg.Connection``) and the unique persistence backend / config
    class names; bare ``Connection`` from elsewhere is left alone to
    avoid false positives.
    """
    target = _make_conformance_file(
        tmp_path,
        "test_unrelated.py",
        "from synthorg.persistence.protocol import PersistenceBackend\n"
        "class TestX:\n"
        "    async def test_y(\n"
        "        self,\n"
        "        backend: PersistenceBackend,\n"
        "        opaque: Connection,\n"
        "    ) -> None:\n"
        "        pass\n",
    )
    issues = _MODULE._scan_signature_file(  # type: ignore[attr-defined]
        target, "tests/conformance/persistence/test_unrelated.py"
    )
    assert issues == []


# ── per-line suppression marker ─────────────────────────────────


def test_marker_with_justification_suppresses_missing_param(tmp_path: Path) -> None:
    """Valid marker silences a ``missing-backend-param`` line."""
    target = _make_conformance_file(
        tmp_path,
        "test_allowed.py",
        "class TestX:\n"
        "    async def test_y(self) -> None:  "
        "# lint-allow: dual-backend-parity -- exercises sqlite-only constraint\n"
        "        pass\n",
    )
    issues = _MODULE._scan_signature_file(  # type: ignore[attr-defined]
        target, "tests/conformance/persistence/test_allowed.py"
    )
    assert issues == []


def test_marker_without_justification_still_flags(tmp_path: Path) -> None:
    """Marker requires ``-- <reason>`` with non-empty reason."""
    target = _make_conformance_file(
        tmp_path,
        "test_bad_marker.py",
        "class TestX:\n"
        "    async def test_y(self) -> None:  # lint-allow: dual-backend-parity\n"
        "        pass\n",
    )
    issues = _MODULE._scan_signature_file(  # type: ignore[attr-defined]
        target, "tests/conformance/persistence/test_bad_marker.py"
    )
    assert any("missing-backend-param" in msg for msg in issues)


def test_marker_on_signature_continuation_suppresses(tmp_path: Path) -> None:
    """Multi-line signature: marker on any signature line silences."""
    target = _make_conformance_file(
        tmp_path,
        "test_multiline.py",
        "class TestX:\n"
        "    async def test_y(\n"
        "        self,\n"
        "        backend: aiosqlite.Connection,  "
        "# lint-allow: dual-backend-parity -- legacy fixture, cleanup in #1234\n"
        "    ) -> None:\n"
        "        pass\n",
    )
    issues = _MODULE._scan_signature_file(  # type: ignore[attr-defined]
        target, "tests/conformance/persistence/test_multiline.py"
    )
    assert issues == []


# ── coverage pass: protocol discovery ───────────────────────────


def test_repo_class_discovery_includes_protocol_definitions(tmp_path: Path) -> None:
    """``class XxxRepository(Protocol):`` is collected from a protocol file."""
    target = _make_protocol_file(
        tmp_path,
        "foo_protocol.py",
        "from typing import Protocol\n"
        "class FooRepository(Protocol):\n"
        "    async def get(self, key: str) -> str | None:\n"
        "        ...\n"
        "class _PrivateHelper:\n"
        "    pass\n"
        "class FooEvent:\n"
        "    pass\n",
    )
    found = _MODULE._discover_repo_classes(target.parent)  # type: ignore[attr-defined]
    assert "FooRepository" in found
    assert "_PrivateHelper" not in found
    assert "FooEvent" not in found


def test_repo_class_discovery_includes_short_repo_suffix(tmp_path: Path) -> None:
    """``class XxxRepo(Protocol):`` (short suffix) is also collected."""
    target = _make_protocol_file(
        tmp_path,
        "preset_override_protocol.py",
        "from typing import Protocol\nclass PresetOverrideRepo(Protocol):\n    pass\n",
    )
    found = _MODULE._discover_repo_classes(target.parent)  # type: ignore[attr-defined]
    assert "PresetOverrideRepo" in found


def test_repo_class_discovery_handles_re_exports(tmp_path: Path) -> None:
    """``from X import YRepository as YRepository`` counts as a protocol."""
    _make_protocol_file(
        tmp_path,
        "escalation_protocol.py",
        "from somewhere.elsewhere import (\n"
        "    EscalationQueueStore as EscalationQueueRepository,\n"
        ")\n"
        "__all__ = ['EscalationQueueRepository']\n",
    )
    found = _MODULE._discover_repo_classes(tmp_path / "protocols")  # type: ignore[attr-defined]
    assert "EscalationQueueRepository" in found


def test_repo_class_discovery_skips_non_protocol_classes(tmp_path: Path) -> None:
    """``class XxxRepository(SomethingElse):`` is skipped (not a protocol)."""
    target = _make_protocol_file(
        tmp_path,
        "not_protocol.py",
        "class FakeRepository:\n"  # No Protocol base
        "    pass\n",
    )
    found = _MODULE._discover_repo_classes(target.parent)  # type: ignore[attr-defined]
    assert "FakeRepository" not in found


# ── coverage pass: PersistenceBackend accessor mapping ──────────


def test_accessor_map_includes_property_returns(tmp_path: Path) -> None:
    """``@property def users(self) -> UserRepository`` maps users->UserRepository."""
    backend_path = tmp_path / "protocol.py"
    backend_path.write_text(
        "from typing import Protocol\n"
        "class PersistenceBackend(Protocol):\n"
        "    @property\n"
        "    def users(self) -> 'UserRepository':\n"
        "        ...\n"
        "    @property\n"
        "    def connections(self) -> 'ConnectionRepository':\n"
        "        ...\n",
        encoding="utf-8",
    )
    accessor_for = _MODULE._discover_backend_accessors(backend_path)  # type: ignore[attr-defined]
    assert accessor_for["UserRepository"] == "users"
    assert accessor_for["ConnectionRepository"] == "connections"


def test_accessor_map_includes_method_returns(tmp_path: Path) -> None:
    """Method-based accessors (e.g. ``build_escalations``) also map."""
    backend_path = tmp_path / "protocol.py"
    backend_path.write_text(
        "from typing import Protocol\n"
        "class PersistenceBackend(Protocol):\n"
        "    def build_escalations(self, cfg: object) -> 'EscalationQueueRepository':\n"
        "        ...\n",
        encoding="utf-8",
    )
    accessor_for = _MODULE._discover_backend_accessors(backend_path)  # type: ignore[attr-defined]
    assert accessor_for["EscalationQueueRepository"] == "build_escalations"


def test_accessor_map_skips_non_repo_returns(tmp_path: Path) -> None:
    """Non-repository return types are not in the accessor map."""
    backend_path = tmp_path / "protocol.py"
    backend_path.write_text(
        "from typing import Protocol\n"
        "class PersistenceBackend(Protocol):\n"
        "    @property\n"
        "    def is_connected(self) -> bool:\n"
        "        ...\n"
        "    @property\n"
        "    def backend_name(self) -> str:\n"
        "        ...\n",
        encoding="utf-8",
    )
    accessor_for = _MODULE._discover_backend_accessors(backend_path)  # type: ignore[attr-defined]
    assert accessor_for == {}


# ── coverage pass: backend.<accessor> usage detection ───────────


def test_accessor_usage_detected_in_test_body(tmp_path: Path) -> None:
    """``await backend.users.save(...)`` registers usage of ``users``."""
    target = _make_conformance_file(
        tmp_path,
        "test_user_repository.py",
        "from synthorg.persistence.protocol import PersistenceBackend\n"
        "class TestUserRepository:\n"
        "    async def test_save(self, backend: PersistenceBackend) -> None:\n"
        "        await backend.users.save(user)\n",
    )
    used = _MODULE._collect_backend_accessor_usage(target.parent)  # type: ignore[attr-defined]
    assert "users" in used


def test_accessor_usage_collects_all_files(tmp_path: Path) -> None:
    """``backend.<x>`` usages aggregate across all conformance test files."""
    _make_conformance_file(
        tmp_path,
        "test_a.py",
        "async def test_x(backend) -> None:\n    backend.users.save(x)\n",
    )
    _make_conformance_file(
        tmp_path,
        "test_b.py",
        "async def test_y(backend) -> None:\n"
        "    backend.connections.save(c)\n"
        "    backend.oauth_states.save(s)\n",
    )
    used = _MODULE._collect_backend_accessor_usage(tmp_path / "conformance")  # type: ignore[attr-defined]
    assert {"users", "connections", "oauth_states"} <= used


# ── coverage pass: end-to-end ───────────────────────────────────


def test_coverage_pass_no_violation_when_repo_used() -> None:
    """Repo with a matching ``backend.<accessor>`` use is covered."""
    repo_classes = {"FooRepository"}
    accessor_for = {"FooRepository": "foos"}
    used_accessors = {"foos"}
    issues = _MODULE._collect_coverage_violations(  # type: ignore[attr-defined]
        repo_classes, accessor_for, used_accessors
    )
    assert issues == []


def test_coverage_pass_violation_when_repo_unused() -> None:
    """Repo whose accessor never appears in tests is a violation."""
    repo_classes = {"FooRepository"}
    accessor_for = {"FooRepository": "foos"}
    used_accessors: set[str] = set()
    issues = _MODULE._collect_coverage_violations(  # type: ignore[attr-defined]
        repo_classes, accessor_for, used_accessors
    )
    assert any("missing-test-coverage" in msg for msg in issues)
    assert any("FooRepository" in msg for msg in issues)


def test_coverage_pass_skips_repos_without_backend_accessor() -> None:
    """Repo not exposed on PersistenceBackend is silently out of scope."""
    repo_classes = {"OrphanRepository"}
    accessor_for: dict[str, str] = {}
    used_accessors: set[str] = set()
    issues = _MODULE._collect_coverage_violations(  # type: ignore[attr-defined]
        repo_classes, accessor_for, used_accessors
    )
    assert issues == []


# ── baseline mechanism ──────────────────────────────────────────


def test_baseline_load_parses_entries(tmp_path: Path) -> None:
    """Baseline file with comment + entries loads to the entry set."""
    baseline = tmp_path / "baseline.txt"
    baseline.write_text(
        "# header comment\n"
        "\n"
        "missing-test-coverage:FooRepository\n"
        "direct-backend-typing:tests/conformance/persistence/test_x.py:42:test_y\n",
        encoding="utf-8",
    )
    entries = _MODULE._load_baseline(baseline)  # type: ignore[attr-defined]
    assert entries == {
        "missing-test-coverage:FooRepository",
        "direct-backend-typing:tests/conformance/persistence/test_x.py:42:test_y",
    }


def test_baseline_missing_file_returns_empty(tmp_path: Path) -> None:
    """Absent baseline file is treated as empty (not an error)."""
    entries = _MODULE._load_baseline(tmp_path / "does_not_exist.txt")  # type: ignore[attr-defined]
    assert entries == set()


def test_baseline_apply_marks_new_and_stale() -> None:
    """``new = current - baseline``; ``stale = baseline - current``."""
    current = {"a", "b", "c"}
    baseline = {"b", "c", "d"}
    new, stale = _MODULE._apply_baseline(current, baseline)  # type: ignore[attr-defined]
    assert set(new) == {"a"}
    assert set(stale) == {"d"}


def test_baseline_write_sorts_entries(tmp_path: Path) -> None:
    """``_write_baseline`` writes header + sorted entries."""
    baseline = tmp_path / "baseline.txt"
    _MODULE._write_baseline(  # type: ignore[attr-defined]
        baseline,
        {
            "missing-test-coverage:Z",
            "missing-test-coverage:A",
            "missing-test-coverage:M",
        },
    )
    text = baseline.read_text(encoding="utf-8")
    body_lines = [
        line for line in text.splitlines() if not line.startswith("#") and line
    ]
    assert body_lines == [
        "missing-test-coverage:A",
        "missing-test-coverage:M",
        "missing-test-coverage:Z",
    ]


# ── --update-baseline + main exit codes ─────────────────────────


def test_main_exit_zero_when_no_violations() -> None:
    """A clean tree exits 0."""
    rc = _MODULE.main(["--repo-root", str(_REPO_ROOT)])  # type: ignore[attr-defined]
    assert rc == 0


def test_main_exit_two_for_invalid_repo_root(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Bad ``--repo-root`` exits 2 with a stderr error."""
    missing = tmp_path / "does-not-exist"
    rc = _MODULE.main(["--repo-root", str(missing)])  # type: ignore[attr-defined]
    assert rc == 2
    err = capsys.readouterr().err
    assert "not accessible" in err or "must be a directory" in err


# ── full-tree smoke (regression guard for the empty-baseline ship) ─


def test_real_tree_passes_with_empty_baseline() -> None:
    """The current repo tree must pass the gate (baseline ships empty).

    If this fails, either (a) a real new violation landed and must be
    fixed, or (b) the gate's heuristics are too strict and need
    relaxing. Do NOT add the violation to the baseline as a workaround
    -- the audit-cited 9 repos are all covered today; baseline ships
    empty deliberately.
    """
    rc = _MODULE.main(["--repo-root", str(_REPO_ROOT)])  # type: ignore[attr-defined]
    assert rc == 0, (
        "Real-tree smoke failed: a dual-backend parity violation has "
        "landed on this branch. Inspect the gate output above; do NOT "
        "add the violation to the baseline as a workaround."
    )
