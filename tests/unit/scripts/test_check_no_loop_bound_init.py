"""Unit tests for ``scripts/check_no_loop_bound_init.py``.

Loads the script as a module so its private helpers are callable
without spawning subprocesses (the script's source-root discovery
is anchored at ``__file__``, so a subprocess call from tests would
scan the real codebase).
"""

import ast
import importlib.util
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_SCRIPT_PATH = _REPO_ROOT / "scripts" / "check_no_loop_bound_init.py"


def _load_script_module() -> object:
    """Import the script as a module so private helpers are callable."""
    spec = importlib.util.spec_from_file_location(
        "_check_no_loop_bound_init",
        _SCRIPT_PATH,
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_MODULE = _load_script_module()


def _scan_source(source: str, tmp_path: Path) -> list[str]:
    """Write *source* to a temp file and return findings from ``_scan_file``."""
    path = tmp_path / "sample.py"
    path.write_text(source, encoding="utf-8")
    findings: list[str] = _MODULE._scan_file(path)  # type: ignore[attr-defined]
    return findings


# ── primitive recognition ────────────────────────────────────────


@pytest.mark.parametrize(
    ("attr", "expected"),
    [
        ("Lock", "Lock"),
        ("Event", "Event"),
        ("Queue", "Queue"),
        ("Semaphore", "Semaphore"),
        ("Condition", "Condition"),
        ("BoundedSemaphore", "BoundedSemaphore"),
    ],
)
def test_recognises_each_loop_bound_primitive(attr: str, expected: str) -> None:
    """Each loop-bound asyncio primitive type is matched by the helper."""
    node = ast.parse(f"asyncio.{attr}()", mode="eval").body
    assert _MODULE._asyncio_primitive_name(node) == expected  # type: ignore[attr-defined]


def test_ignores_non_asyncio_calls() -> None:
    """``threading.Lock()`` is not flagged."""
    node = ast.parse("threading.Lock()", mode="eval").body
    assert _MODULE._asyncio_primitive_name(node) is None  # type: ignore[attr-defined]


def test_ignores_asyncio_non_loop_bound() -> None:
    """``asyncio.create_task(...)`` is not in the loop-bound set."""
    node = ast.parse("asyncio.create_task(coro)", mode="eval").body
    assert _MODULE._asyncio_primitive_name(node) is None  # type: ignore[attr-defined]


def test_flags_direct_imported_primitive(tmp_path: Path) -> None:
    """``from asyncio import Lock; self._lock = Lock()`` is flagged."""
    source = (
        "from asyncio import Lock\n"
        "class Service:\n"
        "    def __init__(self) -> None:\n"
        "        self._lock = Lock()\n"
        "    async def start(self) -> None:\n"
        "        pass\n"
    )
    findings = _scan_source(source, tmp_path)
    assert len(findings) == 1
    assert ":Service:_lock:Lock" in findings[0]


def test_flags_aliased_direct_imported_primitive(tmp_path: Path) -> None:
    """``from asyncio import Lock as L; self._lock = L()`` is flagged."""
    source = (
        "from asyncio import Lock as L\n"
        "class Service:\n"
        "    def __init__(self) -> None:\n"
        "        self._lock = L()\n"
        "    async def start(self) -> None:\n"
        "        pass\n"
    )
    findings = _scan_source(source, tmp_path)
    assert len(findings) == 1
    # Output uses the original primitive name, not the alias.
    assert ":Service:_lock:Lock" in findings[0]


def test_flags_aliased_module(tmp_path: Path) -> None:
    """``import asyncio as aio; self._lock = aio.Lock()`` is flagged."""
    source = (
        "import asyncio as aio\n"
        "class Service:\n"
        "    def __init__(self) -> None:\n"
        "        self._lock = aio.Lock()\n"
        "    async def start(self) -> None:\n"
        "        pass\n"
    )
    findings = _scan_source(source, tmp_path)
    assert len(findings) == 1
    assert ":Service:_lock:Lock" in findings[0]


def test_flags_from_asyncio_locks_submodule(tmp_path: Path) -> None:
    """``from asyncio import locks; self._lock = locks.Lock()`` is flagged."""
    source = (
        "from asyncio import locks\n"
        "class Service:\n"
        "    def __init__(self) -> None:\n"
        "        self._lock = locks.Lock()\n"
        "    async def start(self) -> None:\n"
        "        pass\n"
    )
    findings = _scan_source(source, tmp_path)
    assert len(findings) == 1
    assert ":Service:_lock:Lock" in findings[0]


def test_flags_from_asyncio_locks_direct(tmp_path: Path) -> None:
    """``from asyncio.locks import Lock`` direct import is flagged."""
    source = (
        "from asyncio.locks import Lock\n"
        "class Service:\n"
        "    def __init__(self) -> None:\n"
        "        self._lock = Lock()\n"
        "    async def start(self) -> None:\n"
        "        pass\n"
    )
    findings = _scan_source(source, tmp_path)
    assert len(findings) == 1
    assert ":Service:_lock:Lock" in findings[0]


def test_does_not_flag_unrelated_callable_named_lock(tmp_path: Path) -> None:
    """A ``Lock`` callable not imported from asyncio is left alone."""
    source = (
        "from threading import Lock\n"
        "class Service:\n"
        "    def __init__(self) -> None:\n"
        "        self._lock = Lock()\n"
        "    async def start(self) -> None:\n"
        "        pass\n"
    )
    findings = _scan_source(source, tmp_path)
    assert findings == []


# ── full file scan ───────────────────────────────────────────────


def test_flags_lock_in_init_with_async_start(tmp_path: Path) -> None:
    """Class with asyncio.Lock in __init__ + async start() is flagged."""
    source = (
        "import asyncio\n"
        "class Service:\n"
        "    def __init__(self) -> None:\n"
        "        self._lock = asyncio.Lock()\n"
        "    async def start(self) -> None:\n"
        "        async with self._lock:\n"
        "            pass\n"
    )
    findings = _scan_source(source, tmp_path)
    assert len(findings) == 1
    assert ":Service:_lock:Lock" in findings[0]


def test_flags_event_with_underscore_run_lifecycle(tmp_path: Path) -> None:
    """Async ``_run`` counts as a lifecycle method (matches sweeper shape)."""
    source = (
        "import asyncio\n"
        "class Sweeper:\n"
        "    def __init__(self) -> None:\n"
        "        self._stop = asyncio.Event()\n"
        "    async def _run(self) -> None:\n"
        "        await self._stop.wait()\n"
    )
    findings = _scan_source(source, tmp_path)
    assert len(findings) == 1
    assert ":Sweeper:_stop:Event" in findings[0]


def test_does_not_flag_class_without_lifecycle_method(tmp_path: Path) -> None:
    """Plain data class with asyncio.Lock but no start/stop is not flagged."""
    source = (
        "import asyncio\n"
        "class StateBag:\n"
        "    def __init__(self) -> None:\n"
        "        self._lock = asyncio.Lock()\n"
        "    def set(self, value: int) -> None:\n"
        "        self.value = value\n"
    )
    findings = _scan_source(source, tmp_path)
    assert findings == []


def test_does_not_flag_primitive_outside_init(tmp_path: Path) -> None:
    """Asyncio.Lock created inside an async method is not flagged."""
    source = (
        "import asyncio\n"
        "class Service:\n"
        "    async def start(self) -> None:\n"
        "        self._lock = asyncio.Lock()\n"
        "    async def stop(self) -> None:\n"
        "        pass\n"
    )
    findings = _scan_source(source, tmp_path)
    assert findings == []


def test_respects_per_line_opt_out_marker(tmp_path: Path) -> None:
    """``# lint-allow: loop-bound-init`` on the assignment line silences."""
    source = (
        "import asyncio\n"
        "class Service:\n"
        "    def __init__(self) -> None:\n"
        "        self._lock = asyncio.Lock()  "
        "# lint-allow: loop-bound-init -- short-lived per-request\n"
        "    async def start(self) -> None:\n"
        "        pass\n"
    )
    findings = _scan_source(source, tmp_path)
    assert findings == []


def test_recognises_annotated_assignment(tmp_path: Path) -> None:
    """``self._lock: asyncio.Lock = asyncio.Lock()`` is also flagged."""
    source = (
        "import asyncio\n"
        "class Service:\n"
        "    def __init__(self) -> None:\n"
        "        self._lock: asyncio.Lock = asyncio.Lock()\n"
        "    async def start(self) -> None:\n"
        "        pass\n"
    )
    findings = _scan_source(source, tmp_path)
    assert len(findings) == 1
    assert ":Service:_lock:Lock" in findings[0]


def test_flags_each_violating_attr_separately(tmp_path: Path) -> None:
    """Multiple loop-bound primitives in __init__ surface as separate findings."""
    source = (
        "import asyncio\n"
        "class Scheduler:\n"
        "    def __init__(self) -> None:\n"
        "        self._lock = asyncio.Lock()\n"
        "        self._wake = asyncio.Event()\n"
        "    async def start(self) -> None:\n"
        "        pass\n"
    )
    findings = _scan_source(source, tmp_path)
    assert len(findings) == 2
    assert any(":_lock:Lock" in f for f in findings)
    assert any(":_wake:Event" in f for f in findings)


def test_baseline_grandfathers_existing_sites(tmp_path: Path) -> None:
    """Entries listed in the baseline file are not reported as new."""
    source_file = tmp_path / "sample.py"
    source_file.write_text(
        "import asyncio\n"
        "class Service:\n"
        "    def __init__(self) -> None:\n"
        "        self._lock = asyncio.Lock()\n"
        "    async def start(self) -> None:\n"
        "        pass\n",
        encoding="utf-8",
    )
    findings = _MODULE._scan_file(source_file)  # type: ignore[attr-defined]
    assert len(findings) == 1

    baseline_path = tmp_path / "baseline.txt"
    baseline_path.write_text(findings[0] + "\n", encoding="utf-8")
    loaded = _MODULE._load_baseline(baseline_path)  # type: ignore[attr-defined]
    assert findings[0] in loaded
    new_findings = [f for f in findings if f not in loaded]
    assert new_findings == []


# ── canonical fixed exemplars ───────────────────────────────────


def test_scheduler_no_longer_flagged() -> None:
    """``ApprovalTimeoutScheduler`` is the canonical fixed exemplar."""
    path = _REPO_ROOT / "src" / "synthorg" / "security" / "timeout" / "scheduler.py"
    findings = _MODULE._scan_file(path)  # type: ignore[attr-defined]
    # The class must initialise ``self._wake_event`` and
    # ``self._lifecycle_lock`` to ``None`` in ``__init__`` so each
    # ``start()`` rebinds them on the live loop; eager construction
    # would re-introduce the cross-loop ``RuntimeError`` this gate
    # exists to prevent.
    assert all("ApprovalTimeoutScheduler" not in f for f in findings)


def test_sweeper_no_longer_flagged() -> None:
    """``EscalationExpirationSweeper`` is the canonical fixed exemplar."""
    path = (
        _REPO_ROOT
        / "src"
        / "synthorg"
        / "communication"
        / "conflict_resolution"
        / "escalation"
        / "sweeper.py"
    )
    findings = _MODULE._scan_file(path)  # type: ignore[attr-defined]
    assert all("EscalationExpirationSweeper" not in f for f in findings)
