"""Unit tests for ``scripts/check_no_magic_numbers.py``.

Exercises the AST detection rules (module-level numeric constants and
default-arg literals), the value/context allowlist (trivial values,
HTTP status defaults, I/O power-of-2 defaults, hex literals), the
file-allowlist prefixes, the per-line ``# lint-allow: magic-numbers``
suppression marker, and the baseline monotonic-shrink contract.

Tests load the script via :mod:`importlib` and call its private
helpers directly, matching the pattern in
``test_check_persistence_boundary.py``.
"""

import importlib.util
from pathlib import Path
from typing import Protocol, cast

import pytest

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_SCRIPT_PATH = _REPO_ROOT / "scripts" / "check_no_magic_numbers.py"


class _Hit(Protocol):
    """Structural view of the script's private ``_Hit`` class."""

    rel: str
    lineno: int
    col: int
    kind: str
    value: str

    def baseline_key(self) -> str: ...
    def message(self) -> str: ...


class _ScriptModule(Protocol):
    """Subset of the script's surface the tests exercise."""

    ScanError: type[Exception]

    @staticmethod
    def _scan_file(file_path: Path, rel: str) -> list[_Hit]: ...
    @staticmethod
    def _line_has_trailing_marker(line: str) -> bool: ...
    @staticmethod
    def _is_file_allowlisted(rel: str) -> bool: ...
    @staticmethod
    def _load_baseline(path: Path) -> set[str]: ...
    @staticmethod
    def _write_baseline(hits: list[_Hit], path: Path) -> None: ...
    @staticmethod
    def _baseline_sort_key(entry: str) -> tuple[str, int, int]: ...
    @staticmethod
    def main(argv: list[str] | None = None) -> int: ...


def _load_script() -> _ScriptModule:
    spec = importlib.util.spec_from_file_location(
        "_check_no_magic_numbers",
        _SCRIPT_PATH,
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return cast(_ScriptModule, module)


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


# ── Module-level constant detection ─────────────────────────────


def test_module_level_int_constant_flagged(write_py: WritePy) -> None:
    src = "_GC_THRESHOLD = 1024\n"
    path = write_py(src)
    hits = _MODULE._scan_file(path, "src/synthorg/api/foo.py")
    assert len(hits) == 1
    assert hits[0].lineno == 1
    assert "module-level-constant" in hits[0].kind
    assert hits[0].value == "1024"


def test_module_level_float_constant_flagged(write_py: WritePy) -> None:
    src = "_PASS_THRESHOLD = 0.7\n"
    path = write_py(src)
    hits = _MODULE._scan_file(path, "src/synthorg/foo.py")
    assert len(hits) == 1
    assert hits[0].value == "0.7"


def test_module_level_negative_constant_flagged(write_py: WritePy) -> None:
    src = "_OFFSET = -42\n"
    path = write_py(src)
    hits = _MODULE._scan_file(path, "src/synthorg/foo.py")
    assert len(hits) == 1
    assert hits[0].value == "-42"


@pytest.mark.parametrize(
    "value",
    ["0", "1", "-1", "0.0", "1.0", "-1.0"],
)
def test_trivial_module_level_values_not_flagged(write_py: WritePy, value: str) -> None:
    src = f"_FOO = {value}\n"
    path = write_py(src)
    assert _MODULE._scan_file(path, "src/synthorg/foo.py") == []


def test_hex_literal_not_flagged(write_py: WritePy) -> None:
    """Hex bit-masks are conventional; the algorithm IS the constant."""
    src = "_MASK = 0xff\n_BIT = 0x80\n"
    path = write_py(src)
    assert _MODULE._scan_file(path, "src/synthorg/foo.py") == []


def test_string_constant_not_flagged(write_py: WritePy) -> None:
    src = '_NAME = "hello"\n_PI = 3.14\n'
    path = write_py(src)
    hits = _MODULE._scan_file(path, "src/synthorg/foo.py")
    assert len(hits) == 1
    assert hits[0].value == "3.14"


def test_module_level_annassign_flagged(write_py: WritePy) -> None:
    """``X: int = 1024`` (PEP 526) is the same shape and should flag."""
    src = "_FOO: int = 1024\n"
    path = write_py(src)
    hits = _MODULE._scan_file(path, "src/synthorg/foo.py")
    assert len(hits) == 1


def test_nested_assign_not_flagged(write_py: WritePy) -> None:
    """Assignments inside class/function bodies are out of scope."""
    src = (
        "def f() -> int:\n"
        "    local = 1024\n"
        "    return local\n"
        "class Foo:\n"
        "    attr = 0.7\n"
    )
    path = write_py(src)
    assert _MODULE._scan_file(path, "src/synthorg/foo.py") == []


def test_bool_literal_not_flagged(write_py: WritePy) -> None:
    """``True``/``False`` are int-subclass but never magic numbers."""
    src = "_FLAG = True\n"
    path = write_py(src)
    assert _MODULE._scan_file(path, "src/synthorg/foo.py") == []


# ── Default-arg detection ───────────────────────────────────────


def test_float_default_arg_flagged(write_py: WritePy) -> None:
    src = "def f(threshold: float = 0.7) -> float:\n    return threshold\n"
    path = write_py(src)
    hits = _MODULE._scan_file(path, "src/synthorg/foo.py")
    assert len(hits) == 1
    assert "default-arg" in hits[0].kind
    assert hits[0].value == "0.7"


def test_int_default_arg_flagged(write_py: WritePy) -> None:
    src = "def f(retries: int = 5) -> int:\n    return retries\n"
    path = write_py(src)
    hits = _MODULE._scan_file(path, "src/synthorg/foo.py")
    assert len(hits) == 1
    assert hits[0].value == "5"


def test_kwonly_default_flagged(write_py: WritePy) -> None:
    src = "def f(*, timeout: float = 30.0) -> float:\n    return timeout\n"
    path = write_py(src)
    hits = _MODULE._scan_file(path, "src/synthorg/foo.py")
    assert len(hits) == 1
    assert hits[0].value == "30.0"


def test_async_def_default_flagged(write_py: WritePy) -> None:
    src = "async def f(timeout: float = 30.0) -> float:\n    return timeout\n"
    path = write_py(src)
    hits = _MODULE._scan_file(path, "src/synthorg/foo.py")
    assert len(hits) == 1


def test_status_code_default_allowlisted(write_py: WritePy) -> None:
    """``def f(*, status_code: int = 404)`` is HTTP convention; allowed."""
    src = (
        "def make_response(*, status_code: int = 404) -> int:\n    return status_code\n"
    )
    path = write_py(src)
    assert _MODULE._scan_file(path, "src/synthorg/foo.py") == []


def test_io_buffering_default_allowlisted(write_py: WritePy) -> None:
    """``def f(buffering: int = 8192)`` is I/O convention; allowed."""
    src = "def reader(buffering: int = 8192) -> int:\n    return buffering\n"
    path = write_py(src)
    assert _MODULE._scan_file(path, "src/synthorg/foo.py") == []


def test_io_chunk_size_default_now_flagged(write_py: WritePy) -> None:
    """``chunk_size`` is no longer in the I/O allowlist; defaults flag."""
    src = "def stream(chunk_size: int = 4096) -> int:\n    return chunk_size\n"
    path = write_py(src)
    hits = _MODULE._scan_file(path, "src/synthorg/foo.py")
    assert len(hits) == 1


def test_io_keyword_with_non_pow2_still_flagged(write_py: WritePy) -> None:
    """``buffer_size=3000`` is policy disguised as I/O size; flag it."""
    src = "def stream(buffer_size: int = 3000) -> int:\n    return buffer_size\n"
    path = write_py(src)
    hits = _MODULE._scan_file(path, "src/synthorg/foo.py")
    assert len(hits) == 1


def test_default_none_not_flagged(write_py: WritePy) -> None:
    src = "def f(x: int | None = None) -> object:\n    return x\n"
    path = write_py(src)
    assert _MODULE._scan_file(path, "src/synthorg/foo.py") == []


# ── Per-line opt-out marker ─────────────────────────────────────


def test_lint_allow_marker_suppresses_line(write_py: WritePy) -> None:
    src = "_FOO = 1024  # lint-allow: magic-numbers -- buffer size from RFC 1234\n"
    path = write_py(src)
    assert _MODULE._scan_file(path, "src/synthorg/foo.py") == []


def test_lint_allow_without_justification_does_not_suppress(
    write_py: WritePy,
) -> None:
    src = "_FOO = 1024  # lint-allow: magic-numbers --\n"
    path = write_py(src)
    hits = _MODULE._scan_file(path, "src/synthorg/foo.py")
    assert len(hits) == 1


def test_lint_allow_for_other_gate_does_not_suppress(write_py: WritePy) -> None:
    """Markers for sibling gates must not silence this one."""
    src = "_FOO = 1024  # lint-allow: persistence-boundary -- nope\n"
    path = write_py(src)
    hits = _MODULE._scan_file(path, "src/synthorg/foo.py")
    assert len(hits) == 1


def test_marker_helper_rejects_empty_justification() -> None:
    assert not _MODULE._line_has_trailing_marker(
        "x = 1  # lint-allow: magic-numbers --"
    )
    assert not _MODULE._line_has_trailing_marker("x = 1  # lint-allow: magic-numbers")
    assert _MODULE._line_has_trailing_marker(
        "x = 1  # lint-allow: magic-numbers -- justified"
    )


# ── File allowlist ──────────────────────────────────────────────


@pytest.mark.parametrize(
    "rel",
    [
        "src/synthorg/settings/definitions/api.py",
        "src/synthorg/settings/definitions/engine.py",
        "src/synthorg/persistence/migrations/0001_init.py",
        "src/synthorg/observability/events/api.py",
    ],
)
def test_file_prefix_allowlist(rel: str) -> None:
    assert _MODULE._is_file_allowlisted(rel)


@pytest.mark.parametrize(
    "rel",
    [
        "src/synthorg/engine/routing/scorer.py",
        "src/synthorg/api/controllers/clients.py",
        "src/synthorg/foo.py",
    ],
)
def test_file_prefix_not_in_allowlist(rel: str) -> None:
    assert not _MODULE._is_file_allowlisted(rel)


# ── Baseline ────────────────────────────────────────────────────


def test_baseline_load_empty_when_missing(tmp_path: Path) -> None:
    nowhere = tmp_path / "missing.txt"
    assert _MODULE._load_baseline(nowhere) == set()


def test_baseline_round_trip(tmp_path: Path, write_py: WritePy) -> None:
    """``_write_baseline`` -> ``_load_baseline`` round-trip preserves entries."""
    src = "_A = 7\n_B = 8\n"
    py = write_py(src, name="x.py")
    hits = _MODULE._scan_file(py, "src/synthorg/x.py")
    assert len(hits) == 2
    out = tmp_path / "baseline.txt"
    _MODULE._write_baseline(list(hits), out)
    loaded = _MODULE._load_baseline(out)
    assert loaded == {h.baseline_key() for h in hits}


def test_baseline_validates_entries(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    bad = tmp_path / "baseline.txt"
    bad.write_text("# header\nthis-is-not-a-valid-entry\n", encoding="utf-8")
    with pytest.raises(ValueError, match="baseline failed validation"):
        _MODULE._load_baseline(bad)
    assert "malformed entry" in capsys.readouterr().err


def test_baseline_rejects_duplicates(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    bad = tmp_path / "baseline.txt"
    bad.write_text("# header\nsrc/foo.py:1:0\nsrc/foo.py:1:0\n", encoding="utf-8")
    with pytest.raises(ValueError, match="baseline failed validation"):
        _MODULE._load_baseline(bad)
    assert "duplicate entry" in capsys.readouterr().err


def test_baseline_sorted_deterministically(tmp_path: Path, write_py: WritePy) -> None:
    """Sort key is (path, lineno, col) numerically -- not lexicographic.

    The trailing pad bumps ``_C`` past line 9 so the baseline file
    contains the lex-mismatch case "73 vs 623": a pure ``str`` sort
    places "9" after "12" because ``"9" > "1"`` codepoint-wise. Only a
    numeric sort puts the entries in actual line order.
    """
    src = "_A = 100\n" + "\n" * 10 + "_B = 200\n_C = 300\n"
    py = write_py(src)
    hits = _MODULE._scan_file(py, "src/synthorg/foo.py")
    out = tmp_path / "baseline.txt"
    _MODULE._write_baseline(list(hits), out)
    body = out.read_text(encoding="utf-8")
    keys = [line for line in body.splitlines() if line and not line.startswith("#")]
    line_numbers = [int(k.rsplit(":", 2)[-2]) for k in keys]
    assert line_numbers == sorted(line_numbers)


def test_baseline_sort_key_is_numeric_not_lexicographic() -> None:
    """``_baseline_sort_key`` orders ``foo.py:73`` before ``foo.py:623``.

    Pure string sort would invert this because ``"6" < "7"``.
    """
    entries = [
        "src/foo.py:623:27",
        "src/foo.py:73:30",
        "src/foo.py:9:0",
        "src/foo.py:120:1",
    ]
    ordered = sorted(entries, key=_MODULE._baseline_sort_key)
    assert ordered == [
        "src/foo.py:9:0",
        "src/foo.py:73:30",
        "src/foo.py:120:1",
        "src/foo.py:623:27",
    ]


# ── End-to-end CLI ──────────────────────────────────────────────


def test_main_scan_passes_when_baseline_covers_all(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When every current hit is in the baseline, ``main`` exits 0."""
    src_root = tmp_path / "src" / "synthorg"
    src_root.mkdir(parents=True)
    target = src_root / "policy.py"
    target.write_text("_FOO = 99\n", encoding="utf-8")

    # ``--update`` writes to ``<project_root>/scripts/no_magic_numbers_baseline.txt``;
    # pre-create the parent so the write does not fail.
    (tmp_path / "scripts").mkdir()

    # ``_REPO_ROOT`` is the script's git-tracked-files cwd fallback,
    # which still needs to point at the temp tree. ``_BASELINE_PATH``
    # is intentionally NOT patched so the test exercises the real
    # ``--repo-root`` baseline-resolution path -- if that path
    # regresses to the checkout-local lookup, the assertion below
    # will fail.
    monkeypatch.setattr(_MODULE, "_REPO_ROOT", tmp_path, raising=False)
    # ``git`` pre-push hooks export ``GIT_DIR`` / ``GIT_WORK_TREE``
    # that point at the host repo. Those propagate into our subprocess
    # ``git ls-files`` and make it ignore the ``cwd=tmp_path`` we set,
    # returning the host repo's tracked files (including
    # ``src/synthorg/__init__.py``) which the script then tries to
    # read at ``tmp_path/src/synthorg/__init__.py`` and crashes.
    # Clearing them forces the script's documented rglob fallback so
    # the test only sees the files it created.
    monkeypatch.delenv("GIT_DIR", raising=False)
    monkeypatch.delenv("GIT_WORK_TREE", raising=False)
    monkeypatch.delenv("GIT_INDEX_FILE", raising=False)

    rc_update = _MODULE.main(
        ["--repo-root", str(tmp_path), "--paths", "src/synthorg", "--update"]
    )
    assert rc_update == 0

    rc_scan = _MODULE.main(["--repo-root", str(tmp_path), "--paths", "src/synthorg"])
    assert rc_scan == 0


def test_main_scan_fails_when_new_violation_added(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A violation outside the baseline makes ``main`` exit 1."""
    src_root = tmp_path / "src" / "synthorg"
    src_root.mkdir(parents=True)
    target = src_root / "policy.py"
    target.write_text("_FOO = 99\n", encoding="utf-8")

    # See sibling test for why ``_BASELINE_PATH`` is NOT patched here
    # and why the GIT_* env vars must be cleared.
    (tmp_path / "scripts").mkdir()
    monkeypatch.setattr(_MODULE, "_REPO_ROOT", tmp_path, raising=False)
    monkeypatch.delenv("GIT_DIR", raising=False)
    monkeypatch.delenv("GIT_WORK_TREE", raising=False)
    monkeypatch.delenv("GIT_INDEX_FILE", raising=False)

    rc_update = _MODULE.main(
        ["--repo-root", str(tmp_path), "--paths", "src/synthorg", "--update"]
    )
    assert rc_update == 0

    target.write_text("_FOO = 99\n_BAR = 7\n", encoding="utf-8")

    rc_scan = _MODULE.main(["--repo-root", str(tmp_path), "--paths", "src/synthorg"])
    assert rc_scan == 1


def test_main_refuses_path_outside_repo(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    rc = _MODULE.main(["--repo-root", str(tmp_path), "--paths", "/etc"])
    assert rc == 2
    assert "outside project root" in capsys.readouterr().err


# ── Scan error surfacing (fail loud) ────────────────────────────


def test_scan_file_raises_on_unreadable_encoding(tmp_path: Path) -> None:
    """Invalid UTF-8 surfaces ``ScanError`` rather than a silent empty list."""
    bad = tmp_path / "bad.py"
    # Latin-1 byte sequence that is not valid UTF-8.
    bad.write_bytes(b"\xff\xfe= 1\n")
    with pytest.raises(_MODULE.ScanError, match="cannot read file"):
        _MODULE._scan_file(bad, "src/synthorg/bad.py")


def test_scan_file_raises_on_syntax_error(tmp_path: Path) -> None:
    """Files that fail to parse surface ``ScanError`` rather than empty hits."""
    bad = tmp_path / "syntax.py"
    bad.write_text("def foo(:\n    pass\n", encoding="utf-8")
    with pytest.raises(_MODULE.ScanError, match="cannot parse file"):
        _MODULE._scan_file(bad, "src/synthorg/syntax.py")


# ── Negated default-arg detection ───────────────────────────────


def test_negated_int_default_flagged(write_py: WritePy) -> None:
    src = "def f(offset: int = -5) -> int:\n    return offset\n"
    path = write_py(src)
    hits = _MODULE._scan_file(path, "src/synthorg/foo.py")
    assert len(hits) == 1
    assert hits[0].value == "-5"


def test_negated_float_default_flagged(write_py: WritePy) -> None:
    src = "def f(bias: float = -0.5) -> float:\n    return bias\n"
    path = write_py(src)
    hits = _MODULE._scan_file(path, "src/synthorg/foo.py")
    assert len(hits) == 1
    assert hits[0].value == "-0.5"


# ── Baseline UnicodeDecodeError ────────────────────────────────


def test_baseline_load_raises_on_invalid_utf8(tmp_path: Path) -> None:
    bad = tmp_path / "baseline.txt"
    bad.write_bytes(b"\xff\xfeinvalid\n")
    with pytest.raises(ValueError, match="cannot read baseline"):
        _MODULE._load_baseline(bad)
