# module-kind: tests
"""Only declared warnings ship from the compose template to an operator."""

import sys
from pathlib import Path
from typing import Final
from unittest.mock import patch

import pytest
from scripts._gate_source import GateSourceError
from scripts.check_compose_template_comments import (
    _ALLOWED_BLOCKS,
    _MAX_BLOCK_LINES,
    _TEMPLATE_REL,
    _check,
    main,
)

pytestmark = pytest.mark.unit

_REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[3]

# The block that prompted the gate: developer rationale in the shipping
# comment form, naming three private Python constants an operator cannot
# open, ending with an instruction addressed to a developer.
_SHUTDOWN_RATIONALE: Final[str] = """\
    # Covers the WHOLE lifespan shutdown, which is two phases in series and
    # not the one uvicorn's timeout_graceful_shutdown bounds: the request
    # drain (_DRAIN_TIMEOUT_SECONDS, 25s) runs first, then the service
    # teardown window (_TOTAL_SHUTDOWN_WINDOW_SECONDS, 75s) plus at most
    # _FLOOR_RESERVE_SECONDS (10s) of floor for the tail, plus the minimum
    # per-step grants. ~112s worst case, 120s here for headroom. Without it,
    # Docker's 10s default arrives mid-sequence and the steps that lose are
    # the ones at the end: the audit-chain flush and the persistence
    # disconnect. Move this and those three constants together.
"""


def _declared_blocks(*, skip: str = "") -> str:
    """Render one minimal shipping block per declared anchor.

    Derived from the allowlist rather than hand-written so a new row
    does not silently leave these fixtures failing on a stale allowance.
    Blank-separated so the run stays one block per anchor rather than
    one block over the size cap.

    Args:
        skip: Anchor to leave out, for the stale-declaration case.

    Returns:
        Template text carrying every declared anchor but `skip`,
        terminated by a newline.
    """
    blocks = "\n\n".join(
        f"# {entry.anchor} here." for entry in _ALLOWED_BLOCKS if entry.anchor != skip
    )
    return f"{blocks}\n"


def _first_line_after(prefix: str) -> int:
    """Return the 1-indexed line number following `prefix`."""
    return prefix.count("\n") + 1


def _write_template(root: Path, body: str) -> None:
    """Write `body` as the repository's compose template under `root`."""
    path = root / _TEMPLATE_REL
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8", newline="\n")


def _run(root: Path) -> int:
    """Invoke the gate's entry point against `root` and return its code."""
    argv = ["check_compose_template_comments.py", "--repo-root", str(root)]
    with patch.object(sys, "argv", argv):
        return main()


def test_shipped_template_is_clean() -> None:
    """The template in the tree ships only declared warnings."""
    assert _check(_REPO_ROOT) == []


def test_undeclared_block_is_refused(tmp_path: Path) -> None:
    """A new shipping comment nobody declared fails, and is located."""
    declared = f"{_declared_blocks()}\nservices:\n  backend:\n"
    _write_template(tmp_path, f"{declared}    # A brand new note.\n")

    violations = _check(tmp_path)

    assert len(violations) == 1
    assert "undeclared" in violations[0]
    assert f"{_TEMPLATE_REL}:{_first_line_after(declared)}:" in violations[0]
    assert "A brand new note." in violations[0]


def test_oversized_declared_block_is_refused(tmp_path: Path) -> None:
    """A declared warning that grows into a paragraph stops being one."""
    anchor = _ALLOWED_BLOCKS[0].anchor
    padding = "\n".join(f"# line {n}" for n in range(_MAX_BLOCK_LINES))
    _write_template(
        tmp_path,
        f"{_declared_blocks(skip=anchor)}\n# {anchor} here.\n{padding}\n",
    )

    violations = _check(tmp_path)

    assert len(violations) == 1
    assert f"over the {_MAX_BLOCK_LINES}-line cap" in violations[0]


def test_shutdown_rationale_is_refused(tmp_path: Path) -> None:
    """The ten-line block this gate was written for does not ship."""
    _write_template(tmp_path, f"{_declared_blocks()}\nservices:\n{_SHUTDOWN_RATIONALE}")

    violations = _check(tmp_path)

    assert len(violations) == 1
    assert "undeclared" in violations[0]


def test_stale_declaration_is_refused(tmp_path: Path) -> None:
    """An allowance outliving its comment is the one the next inherits."""
    dropped = _ALLOWED_BLOCKS[-1]
    _write_template(tmp_path, f"{_declared_blocks(skip=dropped.anchor)}\n")

    violations = _check(tmp_path)

    assert len(violations) == 1
    assert repr(dropped.anchor) in violations[0]
    assert dropped.audience in violations[0]


def test_template_comment_bodies_never_ship(tmp_path: Path) -> None:
    """A '#' inside '{{- /* */}}' reaches no operator, so it is not one."""
    _write_template(
        tmp_path,
        f"{_declared_blocks()}\n{{{{- /*\n# Not a YAML comment at all.\n*/}}}}\n",
    )

    assert _check(tmp_path) == []


def test_line_numbers_survive_blanked_template_comments(tmp_path: Path) -> None:
    """Blanking keeps following lines at the number the message prints."""
    declared = f"{_declared_blocks()}\n{{{{- /*\nrationale\nover lines\n*/}}}}\n"
    _write_template(tmp_path, f"{declared}# Undeclared.\n")

    violations = _check(tmp_path)

    assert len(violations) == 1
    assert f"{_TEMPLATE_REL}:{_first_line_after(declared)}:" in violations[0]


def test_blank_line_splits_adjacent_blocks(tmp_path: Path) -> None:
    """Two undeclared comments separated by a blank line report twice."""
    _write_template(tmp_path, f"{_declared_blocks()}\n# One.\n\n# Two.\n")

    assert len(_check(tmp_path)) == 2


def test_template_without_any_shipping_comment_is_a_config_error(
    tmp_path: Path,
) -> None:
    """A template with no '#' at all means the header is gone."""
    _write_template(tmp_path, "name: synthorg\nservices:\n")

    with pytest.raises(GateSourceError, match="header is gone"):
        _check(tmp_path)


def test_missing_template_exits_two(tmp_path: Path) -> None:
    """An unreadable template is a configuration error, not a pass."""
    assert _run(tmp_path) == 2


def test_main_reports_violations(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Violations exit 1 and name the offending text on stderr."""
    _write_template(tmp_path, f"{_declared_blocks()}\n# Undeclared note.\n")

    assert _run(tmp_path) == 1
    assert "Undeclared note." in capsys.readouterr().err


def test_main_passes_on_the_real_tree() -> None:
    """The gate exits 0 against the repository it ships in."""
    assert _run(_REPO_ROOT) == 0
