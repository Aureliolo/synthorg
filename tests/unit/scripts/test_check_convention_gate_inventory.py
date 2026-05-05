"""Tests for the convention-rollout meta-gate.

The gate's contract: catch the SECOND occurrence of an ungated
convention. Audits catch the first. These tests encode that intent
explicitly: a single new MANDATORY paragraph absent from the YAML is
already a regression (test_first_occurrence_is_strict), and a tree
with ONE registered + ONE unregistered MANDATORY surfaces only the
unregistered one (test_second_occurrence_isolated).
"""

import importlib.util
import textwrap
from collections.abc import Iterable
from pathlib import Path
from typing import Protocol, cast

import pytest

pytestmark = pytest.mark.unit


class _CheckConventionGateModule(Protocol):
    """Subset of ``scripts/check_convention_gate_inventory.py`` the tests use.

    Captures the dynamically-loaded module surface so mypy strict mode
    can type-check call sites without ``# type: ignore`` markers.
    """

    InventorySchemaError: type[Exception]

    @staticmethod
    def slugify(text: str) -> str: ...
    @staticmethod
    def make_id(file: str, header: str) -> str: ...
    @staticmethod
    def extract_mandatory_entries(text: str, file: str) -> list[object]: ...
    @staticmethod
    def collect_doc_files(repo_root: Path) -> list[Path]: ...
    @staticmethod
    def scan_repo(repo_root: Path) -> list[object]: ...
    @staticmethod
    def load_inventory(yaml_path: Path) -> tuple[object, ...]: ...
    @staticmethod
    def reconcile(
        extracted: Iterable[object],
        inventory: Iterable[object],
        repo_root: Path,
    ) -> list[object]: ...
    @staticmethod
    def check(repo_root: Path) -> list[object]: ...
    @staticmethod
    def main(argv: list[str] | None = None) -> int: ...


def _load_module() -> _CheckConventionGateModule:
    """Load the gate script as a Python module via importlib.

    Direct module import (rather than subprocess invocation) lets the
    tests drive private helpers and synthesise repos in ``tmp_path``
    without touching the real working tree. The ``cast`` to the local
    Protocol gives mypy strict the surface it needs without a sea of
    ``# type: ignore`` markers at every call site.
    """
    repo_root = Path(__file__).resolve().parents[3]
    script_path = repo_root / "scripts" / "check_convention_gate_inventory.py"
    spec = importlib.util.spec_from_file_location(
        "check_convention_gate_inventory", script_path
    )
    if spec is None or spec.loader is None:
        msg = f"could not load module spec for {script_path}"
        raise RuntimeError(msg)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return cast(_CheckConventionGateModule, module)


_MODULE = _load_module()


@pytest.fixture
def fake_repo(tmp_path: Path) -> Path:
    """Build a minimal fake repo with the doc-set tree pre-created."""
    (tmp_path / "scripts").mkdir()
    (tmp_path / "docs" / "reference").mkdir(parents=True)
    (tmp_path / "docs" / "design").mkdir(parents=True)
    (tmp_path / "web").mkdir()
    (tmp_path / "cli").mkdir()
    return tmp_path


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(content), encoding="utf-8")


def test_slug_round_trip() -> None:
    assert _MODULE.slugify("Persistence Boundary") == "persistence-boundary"
    assert _MODULE.slugify("MSW handlers") == "msw-handlers"
    assert _MODULE.slugify("  Trim Me  ") == "trim-me"
    assert _MODULE.slugify("Foo / Bar (v2)") == "foo-bar-v2"


def test_make_id_combines_file_and_header() -> None:
    assert _MODULE.make_id("CLAUDE.md", "Persistence Boundary") == (
        "claude-md::persistence-boundary"
    )
    assert _MODULE.make_id("web/CLAUDE.md", "MSW handlers") == (
        "web-claude-md::msw-handlers"
    )


def test_extract_header_only_matches_mandatory_lines() -> None:
    text = textwrap.dedent(
        """
        # Title

        ## Persistence Boundary (MANDATORY)

        Body text.

        ## Other Section

        ### Sub (MANDATORY)
        """
    ).strip()
    entries = _MODULE.extract_mandatory_entries(text, "CLAUDE.md")
    assert len(entries) == 2
    assert entries[0].header == "Persistence Boundary"  # type: ignore[attr-defined]
    assert entries[0].kind == "header"  # type: ignore[attr-defined]
    assert entries[1].header == "Sub"  # type: ignore[attr-defined]


def test_extract_inline_bold_subsections() -> None:
    text = "**MSW handlers (MANDATORY)** must mirror the production routes."
    entries = _MODULE.extract_mandatory_entries(text, "web/CLAUDE.md")
    assert len(entries) == 1
    assert entries[0].header == "MSW handlers"  # type: ignore[attr-defined]
    assert entries[0].kind == "inline"  # type: ignore[attr-defined]


def test_extract_mandatory_variant_phrase() -> None:
    text = "**Foo (MANDATORY for every store)** sentence body."
    entries = _MODULE.extract_mandatory_entries(text, "docs/reference/x.md")
    assert len(entries) == 1
    assert entries[0].header == "Foo"  # type: ignore[attr-defined]


def test_load_inventory_happy_path(fake_repo: Path) -> None:
    inventory = fake_repo / "scripts" / "convention_gate_map.yaml"
    _write(
        inventory,
        """
        mandatory_rules:
          - id: claude-md::persistence-boundary
            file: CLAUDE.md
            header: Persistence Boundary
            gate: scripts/check_persistence_boundary.py
          - id: claude-md::planning
            file: CLAUDE.md
            header: Planning
            exempt:
              reason: Process rule, user approval before coding.
        """,
    )
    entries = _MODULE.load_inventory(inventory)
    assert len(entries) == 2
    assert entries[0].gate == "scripts/check_persistence_boundary.py"  # type: ignore[attr-defined]
    assert entries[1].exempt_reason  # type: ignore[attr-defined]


_SCHEMA_REJECTION_CASES: tuple[tuple[str, str], ...] = (
    (
        "empty-reason",
        """
        mandatory_rules:
          - id: claude-md::planning
            file: CLAUDE.md
            header: Planning
            exempt:
              reason: ""
        """,
    ),
    (
        "duplicate-ids",
        """
        mandatory_rules:
          - id: claude-md::persistence-boundary
            file: CLAUDE.md
            header: Persistence Boundary
            gate: scripts/check_persistence_boundary.py
          - id: claude-md::persistence-boundary
            file: CLAUDE.md
            header: Persistence Boundary
            exempt:
              reason: oops second copy
        """,
    ),
    (
        "both-gate-and-exempt",
        """
        mandatory_rules:
          - id: claude-md::foo
            file: CLAUDE.md
            header: Foo
            gate: scripts/check_foo.py
            exempt:
              reason: cannot have both
        """,
    ),
    (
        "neither-gate-nor-exempt",
        """
        mandatory_rules:
          - id: claude-md::foo
            file: CLAUDE.md
            header: Foo
        """,
    ),
    (
        "mandatory-rules-null",
        "mandatory_rules: null\n",
    ),
    (
        "id-mismatch",
        """
        mandatory_rules:
          - id: claude-md::wrong-slug
            file: CLAUDE.md
            header: Persistence Boundary
            gate: scripts/check_persistence_boundary.py
        """,
    ),
)


@pytest.mark.parametrize(
    "yaml_body",
    [case[1] for case in _SCHEMA_REJECTION_CASES],
    ids=[case[0] for case in _SCHEMA_REJECTION_CASES],
)
def test_load_inventory_rejects_invalid_schema(fake_repo: Path, yaml_body: str) -> None:
    inventory = fake_repo / "scripts" / "convention_gate_map.yaml"
    _write(inventory, yaml_body)
    with pytest.raises(_MODULE.InventorySchemaError):
        _MODULE.load_inventory(inventory)


def _seed_one_rule(repo: Path, *, registered: bool) -> None:
    """Seed a fake repo with one MANDATORY paragraph and matching YAML.

    When ``registered=True``, also creates the gate script's path on
    disk so the gate's existence check passes; the schema validator
    requires every ``gate:`` entry to resolve to a real file. The
    rule id is always ``claude-md::persistence-boundary``.
    """
    _write(
        repo / "CLAUDE.md",
        """
        # Title

        ## Persistence Boundary (MANDATORY)

        Body text.
        """,
    )
    if registered:
        # The gate script the YAML names must exist in the fake repo too.
        _write(repo / "scripts" / "check_persistence_boundary.py", "# stub\n")
        _write(
            repo / "scripts" / "convention_gate_map.yaml",
            """
            mandatory_rules:
              - id: claude-md::persistence-boundary
                file: CLAUDE.md
                header: Persistence Boundary
                gate: scripts/check_persistence_boundary.py
            """,
        )
    else:
        _write(
            repo / "scripts" / "convention_gate_map.yaml",
            "mandatory_rules: []\n",
        )


def test_happy_path_zero_violations(fake_repo: Path) -> None:
    _seed_one_rule(fake_repo, registered=True)
    assert _MODULE.check(fake_repo) == []


def test_first_occurrence_is_strict(fake_repo: Path) -> None:
    _seed_one_rule(fake_repo, registered=False)
    violations = _MODULE.check(fake_repo)
    assert len(violations) == 1
    rendered = violations[0].render()  # type: ignore[attr-defined]
    assert "claude-md::persistence-boundary" in rendered
    assert "not registered" in rendered


def test_second_occurrence_isolated(fake_repo: Path) -> None:
    _write(
        fake_repo / "CLAUDE.md",
        """
        ## Persistence Boundary (MANDATORY)

        First rule.

        ## New Untracked Convention (MANDATORY)

        Second rule, missing from YAML.
        """,
    )
    _write(fake_repo / "scripts" / "check_persistence_boundary.py", "# stub\n")
    _write(
        fake_repo / "scripts" / "convention_gate_map.yaml",
        """
        mandatory_rules:
          - id: claude-md::persistence-boundary
            file: CLAUDE.md
            header: Persistence Boundary
            gate: scripts/check_persistence_boundary.py
        """,
    )
    violations = _MODULE.check(fake_repo)
    assert len(violations) == 1
    rendered = violations[0].render()  # type: ignore[attr-defined]
    assert "claude-md::new-untracked-convention" in rendered


def test_exempt_entry_passes(fake_repo: Path) -> None:
    _write(
        fake_repo / "CLAUDE.md",
        """
        ## Planning (MANDATORY)

        Process rule.
        """,
    )
    _write(
        fake_repo / "scripts" / "convention_gate_map.yaml",
        """
        mandatory_rules:
          - id: claude-md::planning
            file: CLAUDE.md
            header: Planning
            exempt:
              reason: Process rule, user approval before coding.
        """,
    )
    assert _MODULE.check(fake_repo) == []


def test_gate_script_missing_on_disk(fake_repo: Path) -> None:
    _seed_one_rule(fake_repo, registered=True)
    (fake_repo / "scripts" / "check_persistence_boundary.py").unlink()
    violations = _MODULE.check(fake_repo)
    assert len(violations) == 1
    rendered = violations[0].render()  # type: ignore[attr-defined]
    assert "gate script missing" in rendered
    assert "scripts/check_persistence_boundary.py" in rendered


def test_inline_bold_unregistered_triggers_violation(fake_repo: Path) -> None:
    _write(
        fake_repo / "web" / "CLAUDE.md",
        "**MSW handlers (MANDATORY)** must mirror production routes.\n",
    )
    _write(
        fake_repo / "scripts" / "convention_gate_map.yaml",
        "mandatory_rules: []\n",
    )
    violations = _MODULE.check(fake_repo)
    assert len(violations) == 1
    rendered = violations[0].render()  # type: ignore[attr-defined]
    assert "web-claude-md::msw-handlers" in rendered


def test_stale_yaml_entry(fake_repo: Path) -> None:
    _write(fake_repo / "CLAUDE.md", "# Title\n\nNo MANDATORY paragraphs here.\n")
    _write(fake_repo / "scripts" / "check_phantom.py", "# stub\n")
    _write(
        fake_repo / "scripts" / "convention_gate_map.yaml",
        """
        mandatory_rules:
          - id: claude-md::phantom
            file: CLAUDE.md
            header: Phantom
            gate: scripts/check_phantom.py
        """,
    )
    violations = _MODULE.check(fake_repo)
    assert len(violations) == 1
    rendered = violations[0].render()  # type: ignore[attr-defined]
    assert "stale entry" in rendered
    assert "claude-md::phantom" in rendered


def test_main_exit_zero_on_clean_tree(fake_repo: Path) -> None:
    _seed_one_rule(fake_repo, registered=True)
    assert _MODULE.main(["--repo-root", str(fake_repo)]) == 0


def test_main_exit_one_on_regression(
    fake_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _seed_one_rule(fake_repo, registered=False)
    assert _MODULE.main(["--repo-root", str(fake_repo)]) == 1
    out = capsys.readouterr()
    assert "not registered" in out.out
    assert "Convention-rollout gate failed" in out.err


def test_main_exit_two_on_schema_error(
    fake_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _write(fake_repo / "CLAUDE.md", "# nothing\n")
    _write(
        fake_repo / "scripts" / "convention_gate_map.yaml",
        "mandatory_rules: not-a-list\n",
    )
    assert _MODULE.main(["--repo-root", str(fake_repo)]) == 2
    err = capsys.readouterr().err
    assert "Inventory schema error" in err


def test_real_repo_passes() -> None:
    """End-to-end seal: the seeded YAML is consistent with the actual repo.

    This is the only test that runs the gate against the real working
    tree. It catches drift between the inventory and reality at PR
    time -- if anyone adds a MANDATORY paragraph and forgets to update
    the YAML, this fails.
    """
    repo_root = Path(__file__).resolve().parents[3]
    violations = _MODULE.check(repo_root)
    rendered = "\n".join(v.render() for v in violations)  # type: ignore[attr-defined]
    assert violations == [], f"Real-tree violations:\n{rendered}"


def test_scan_repo_raises_schema_error_on_unreadable_file(
    fake_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """OSError on a doc file read surfaces as InventorySchemaError, not a crash."""
    _write(fake_repo / "CLAUDE.md", "## Foo (MANDATORY)\nbody\n")

    real_read_text = Path.read_text

    def _broken_read(self: Path, *args: object, **kwargs: object) -> str:
        del args, kwargs
        if self.name == "CLAUDE.md":
            msg = "synthetic permission error"
            raise PermissionError(msg)
        return real_read_text(self, encoding="utf-8")

    monkeypatch.setattr(Path, "read_text", _broken_read)
    with pytest.raises(_MODULE.InventorySchemaError):
        _MODULE.scan_repo(fake_repo)


def test_extract_two_inline_bold_on_same_line() -> None:
    """A line with two ``**Foo (MANDATORY)**`` matches must yield both entries."""
    text = "**Foo (MANDATORY)** and also **Bar (MANDATORY)** in one sentence."
    entries = _MODULE.extract_mandatory_entries(text, "web/CLAUDE.md")
    assert len(entries) == 2
    headers = sorted(e.header for e in entries)  # type: ignore[attr-defined]
    assert headers == ["Bar", "Foo"]


def test_extract_header_at_line_one() -> None:
    """A MANDATORY header on line 1 (no leading newline) extracts at line=1."""
    text = "## Persistence Boundary (MANDATORY)\nBody.\n"
    entries = _MODULE.extract_mandatory_entries(text, "CLAUDE.md")
    assert len(entries) == 1
    assert entries[0].line == 1  # type: ignore[attr-defined]


def test_check_with_empty_doc_set_only_surfaces_stale(fake_repo: Path) -> None:
    """If no canonical doc files exist but YAML lists rules, every rule is stale."""
    _write(fake_repo / "scripts" / "check_phantom.py", "# stub\n")
    _write(
        fake_repo / "scripts" / "convention_gate_map.yaml",
        """
        mandatory_rules:
          - id: claude-md::phantom
            file: CLAUDE.md
            header: Phantom
            gate: scripts/check_phantom.py
        """,
    )
    violations = _MODULE.check(fake_repo)
    assert len(violations) == 1
    assert "stale entry" in violations[0].render()  # type: ignore[attr-defined]


def test_main_exit_two_on_bad_repo_root(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Passing --repo-root to a path that does not exist exits 2 with a clear msg."""
    missing = tmp_path / "nonexistent-subdir"
    assert _MODULE.main(["--repo-root", str(missing)]) == 2
    err = capsys.readouterr().err
    assert "does not exist" in err


def test_extract_handles_crlf_line_endings() -> None:
    """Markdown files with CRLF line endings (Windows default) extract cleanly."""
    text = "## Persistence Boundary (MANDATORY)\r\nBody.\r\n"
    entries = _MODULE.extract_mandatory_entries(text, "CLAUDE.md")
    assert len(entries) == 1
    # Header text must not carry a trailing \r left over from the split.
    assert entries[0].header == "Persistence Boundary"  # type: ignore[attr-defined]
