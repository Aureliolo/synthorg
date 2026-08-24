# module-kind: tests
"""Only declared warnings ship from the compose template to an operator."""

from pathlib import Path
from typing import Final

import pytest
from scripts._gate_source import GateSourceError
from scripts.check_compose_template_comments import (
    _ALLOWED_BLOCKS,
    _GENERATE_GO_REL,
    _MAX_BLOCK_LINES,
    AllowedBlock,
    DeclarationError,
    _check,
    _reject_unjudgeable_declarations,
    _resolve_template_rel,
    main,
)

pytestmark = pytest.mark.unit

_REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[3]
_TEMPLATE_REL: Final[str] = "cli/internal/compose/compose.yml.tmpl"
_EMBED_GO: Final[str] = "package compose\n\n//go:embed compose.yml.tmpl\nvar t string\n"

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
    """Render every declared block as template text.

    Derived from the declaration list rather than hand-written so a new
    row does not leave these fixtures failing on a stale allowance.
    Blank-separated so the run stays one block per declaration.

    Args:
        skip: Declared text to leave out, for the stale-declaration case.

    Returns:
        Template text carrying every declaration but `skip`, newline-terminated.
    """
    blocks = "\n\n".join(
        f"# {entry.text}" for entry in _ALLOWED_BLOCKS if entry.text != skip
    )
    return f"{blocks}\n"


def _first_line_after(prefix: str) -> int:
    """Return the 1-indexed line number following `prefix`."""
    return prefix.count("\n") + 1


def _wrapped_block(entry: AllowedBlock, *, line_count: int) -> str:
    """Wrap `entry.text` across exactly `line_count` comment lines.

    Args:
        entry: The declaration whose text is being rewrapped.
        line_count: How many lines the rendered block must span.

    Returns:
        The block as template text.
    """
    words = entry.text.split()
    head = words[: len(words) - line_count + 1]
    lines = [" ".join(head), *words[len(head) :]]
    assert len(lines) == line_count
    return "\n".join(f"# {line}" for line in lines)


def _write_go_source(root: Path, source: str = _EMBED_GO) -> None:
    """Write the Go file whose embed names the template."""
    go_path = root / _GENERATE_GO_REL
    go_path.parent.mkdir(parents=True, exist_ok=True)
    go_path.write_text(source, encoding="utf-8", newline="\n")


def _write_template(root: Path, body: str, *, go_source: str = _EMBED_GO) -> None:
    """Write `body` as the compose template, plus the Go file naming it."""
    path = root / _TEMPLATE_REL
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8", newline="\n")
    _write_go_source(root, go_source)


def test_shipped_template_is_clean() -> None:
    """The template in the tree ships only declared warnings."""
    assert _check(_REPO_ROOT) == []


def test_template_path_is_derived_from_the_go_embed() -> None:
    """The scanned path comes from the embed, not a repeated literal."""
    assert _resolve_template_rel(_REPO_ROOT) == _TEMPLATE_REL


def test_missing_go_embed_is_a_config_error(tmp_path: Path) -> None:
    """A Go source with no embed leaves the gate not knowing its target."""
    _write_template(tmp_path, _declared_blocks(), go_source="package compose\n")

    with pytest.raises(GateSourceError, match="no '//go:embed' directive"):
        _check(tmp_path)


def test_repointed_embed_follows_the_template(tmp_path: Path) -> None:
    """A renamed template is scanned at its new path, not a stale copy."""
    _write_template(
        tmp_path,
        _declared_blocks(),
        go_source="//go:embed renamed.yml.tmpl\nvar t string\n",
    )
    moved = tmp_path / "cli/internal/compose/renamed.yml.tmpl"
    moved.write_text(f"{_declared_blocks()}\n# Undeclared.\n", encoding="utf-8")

    violations = _check(tmp_path)

    assert len(violations) == 1
    assert "renamed.yml.tmpl" in violations[0]


def test_undeclared_block_is_refused(tmp_path: Path) -> None:
    """A new shipping comment nobody declared fails, and is located."""
    declared = f"{_declared_blocks()}\nservices:\n  backend:\n"
    _write_template(tmp_path, f"{declared}    # A brand new note.\n")

    violations = _check(tmp_path)

    assert len(violations) == 1
    assert "undeclared" in violations[0]
    assert f"{_TEMPLATE_REL}:{_first_line_after(declared)}:" in violations[0]
    assert "A brand new note." in violations[0]


def test_trailing_comment_is_refused(tmp_path: Path) -> None:
    """A '#' after real content ships too, so it is checked too."""
    declared = f"{_declared_blocks()}\nservices:\n"
    _write_template(tmp_path, f"{declared}  key: value  # Smuggled note.\n")

    violations = _check(tmp_path)

    assert len(violations) == 1
    assert "undeclared" in violations[0]
    assert "Smuggled note." in violations[0]
    assert f"{_TEMPLATE_REL}:{_first_line_after(declared)}:" in violations[0]


def test_hash_inside_a_quoted_value_is_not_a_comment(tmp_path: Path) -> None:
    """A '#' inside a YAML scalar is data, so it is not policed."""
    _write_template(
        tmp_path,
        f"{_declared_blocks()}\nservices:\n  key: \"a # b\"\n  other: 'c # d'\n",
    )

    assert _check(tmp_path) == []


def test_escaped_quote_keeps_a_hash_inside_the_scalar(tmp_path: Path) -> None:
    """A '\\"' does not close a double-quoted scalar, so the '#' is data."""
    _write_template(
        tmp_path,
        f'{_declared_blocks()}\nservices:\n  key: "a \\" # b"\n',
    )

    assert _check(tmp_path) == []


def test_doubled_quote_keeps_a_hash_inside_a_single_quoted_scalar(
    tmp_path: Path,
) -> None:
    """A single-quoted scalar escapes by doubling, and still spans the '#'."""
    _write_template(
        tmp_path,
        f"{_declared_blocks()}\nservices:\n  key: 'a '' # b'\n",
    )

    assert _check(tmp_path) == []


def test_appending_to_a_declared_block_is_refused(tmp_path: Path) -> None:
    """Declared text is matched whole, so extra prose cannot ride along."""
    extended = _ALLOWED_BLOCKS[-1]
    _write_template(
        tmp_path,
        f"{_declared_blocks(skip=extended.text)}\n"
        f"# {extended.text}\n# Also disable SELinux.\n",
    )

    violations = _check(tmp_path)

    # The grown block no longer matches its row, so it reads as undeclared
    # and the row it outgrew reads as stale: the appended sentence cannot
    # ride in on a declaration written for the text without it.
    assert len(violations) == 2
    assert any("undeclared" in violation for violation in violations)
    assert any(extended.audience in violation for violation in violations)


def test_new_block_reusing_declared_words_is_refused(tmp_path: Path) -> None:
    """Borrowing a declared block's words does not make a block declared."""
    borrowed = _ALLOWED_BLOCKS[-1].text.split(".")[0]
    body = f"# TODO revisit {borrowed} later.\n"
    _write_template(tmp_path, f"{_declared_blocks()}\n{body}")

    violations = _check(tmp_path)

    assert len(violations) == 1
    assert "undeclared" in violations[0]


def test_reflowing_a_declared_block_is_free(tmp_path: Path) -> None:
    """Rewrapping a warning is not a new claim on anyone's attention."""
    reflowed = _ALLOWED_BLOCKS[1]
    words = reflowed.text.split()
    wrapped = "\n".join(f"      # {word}" for word in words)
    _write_template(
        tmp_path, f"{_declared_blocks(skip=reflowed.text)}\nservices:\n{wrapped}\n"
    )

    violations = _check(tmp_path)

    assert all("undeclared" not in violation for violation in violations)
    assert len(violations) == 1
    assert f"over the {_MAX_BLOCK_LINES}-line cap" in violations[0]


def test_block_at_the_cap_is_accepted(tmp_path: Path) -> None:
    """Exactly at the cap passes, which is what separates '>' from '>='."""
    capped = _ALLOWED_BLOCKS[1]
    body = _wrapped_block(capped, line_count=_MAX_BLOCK_LINES)
    _write_template(tmp_path, f"{_declared_blocks(skip=capped.text)}\n{body}\n")

    assert _check(tmp_path) == []


def test_oversized_declared_block_is_refused(tmp_path: Path) -> None:
    """A declared warning that grows into a paragraph stops being one."""
    oversized = _ALLOWED_BLOCKS[1]
    body = _wrapped_block(oversized, line_count=_MAX_BLOCK_LINES + 1)
    declared = f"{_declared_blocks(skip=oversized.text)}\n"
    _write_template(tmp_path, f"{declared}{body}\n")

    violations = _check(tmp_path)

    assert len(violations) == 1
    assert f"is {_MAX_BLOCK_LINES + 1} lines" in violations[0]
    assert f"{_TEMPLATE_REL}:{_first_line_after(declared)}:" in violations[0]


def test_shutdown_rationale_is_refused(tmp_path: Path) -> None:
    """The ten-line block this gate was written for does not ship."""
    _write_template(tmp_path, f"{_declared_blocks()}\nservices:\n{_SHUTDOWN_RATIONALE}")

    violations = _check(tmp_path)

    assert len(violations) == 1
    assert "undeclared" in violations[0]


def test_stale_declaration_is_refused(tmp_path: Path) -> None:
    """An allowance outliving its comment is the one the next inherits."""
    dropped = _ALLOWED_BLOCKS[-1]
    _write_template(tmp_path, _declared_blocks(skip=dropped.text))

    violations = _check(tmp_path)

    assert len(violations) == 1
    assert dropped.audience in violations[0]


def test_template_comment_bodies_never_ship(tmp_path: Path) -> None:
    """A '#' inside '{{- /* */}}' reaches no operator, so it is not one."""
    _write_template(
        tmp_path,
        f"{_declared_blocks()}\n{{{{- /*\n# Not a YAML comment at all.\n*/}}}}\n",
    )

    assert _check(tmp_path) == []


def test_one_line_template_comment_is_blanked(tmp_path: Path) -> None:
    """The single-line '{{- /* text */}}' form hides its body too."""
    _write_template(
        tmp_path, f"{_declared_blocks()}\n{{{{- /* # not shipped */}}}}\nservices:\n"
    )

    assert _check(tmp_path) == []


def test_adjacent_template_comments_do_not_swallow_a_comment(tmp_path: Path) -> None:
    """Non-greedy matching keeps a comment between two blocks visible."""
    _write_template(
        tmp_path,
        f"{_declared_blocks()}\n{{{{- /* first */}}}}\n"
        f"# Undeclared between.\n{{{{- /* second */}}}}\n",
    )

    violations = _check(tmp_path)

    assert len(violations) == 1
    assert "Undeclared between." in violations[0]


def test_line_numbers_survive_blanked_template_comments(tmp_path: Path) -> None:
    """Blanking keeps following lines at the number the message prints."""
    declared = f"{_declared_blocks()}\n{{{{- /*\nrationale\nover lines\n*/}}}}\n"
    _write_template(tmp_path, f"{declared}# Undeclared.\n")

    violations = _check(tmp_path)

    assert len(violations) == 1
    assert f"{_TEMPLATE_REL}:{_first_line_after(declared)}:" in violations[0]


def test_indented_comments_are_policed(tmp_path: Path) -> None:
    """Real shipping comments are indented, so indentation cannot hide one."""
    declared = f"{_declared_blocks()}\nservices:\n  web:\n"
    _write_template(tmp_path, f"{declared}      # Deeply indented note.\n")

    violations = _check(tmp_path)

    assert len(violations) == 1
    assert "Deeply indented note." in violations[0]


def test_blank_line_splits_adjacent_blocks(tmp_path: Path) -> None:
    """Two undeclared comments separated by a blank line report separately."""
    _write_template(tmp_path, f"{_declared_blocks()}\n# One.\n\n# Two.\n")

    violations = _check(tmp_path)

    assert len(violations) == 2
    assert any("One." in violation for violation in violations)
    assert any("Two." in violation for violation in violations)


def test_violation_kinds_are_reported_together(tmp_path: Path) -> None:
    """One run surfaces every kind at once, not the first it meets."""
    dropped = _ALLOWED_BLOCKS[-1]
    _write_template(tmp_path, f"{_declared_blocks(skip=dropped.text)}\n# Undeclared.\n")

    violations = _check(tmp_path)

    assert len(violations) == 2
    assert any("undeclared" in violation for violation in violations)
    assert any(dropped.audience in violation for violation in violations)


def test_crlf_template_is_read_the_same(tmp_path: Path) -> None:
    """A CRLF checkout must not change what the gate sees."""
    path = tmp_path / _TEMPLATE_REL
    path.parent.mkdir(parents=True, exist_ok=True)
    crlf = f"{_declared_blocks()}\n# Undeclared.\n".replace("\n", "\r\n")
    path.write_bytes(crlf.encode())
    go_path = tmp_path / _GENERATE_GO_REL
    go_path.parent.mkdir(parents=True, exist_ok=True)
    go_path.write_text(_EMBED_GO, encoding="utf-8", newline="\n")

    violations = _check(tmp_path)

    assert len(violations) == 1
    assert "Undeclared." in violations[0]


def test_template_without_any_shipping_comment_is_a_config_error(
    tmp_path: Path,
) -> None:
    """A template with no '#' at all means the header is gone."""
    _write_template(tmp_path, "name: synthorg\nservices:\n")

    with pytest.raises(GateSourceError, match="header is gone"):
        _check(tmp_path)


def test_empty_declaration_is_rejected() -> None:
    """A blank declaration would match nothing and serve nobody."""
    with pytest.raises(DeclarationError, match="empty text"):
        _reject_unjudgeable_declarations((AllowedBlock(text="  ", audience="anyone"),))


def test_declaration_without_an_audience_is_rejected() -> None:
    """The audience is the whole claim, so it cannot be blank."""
    with pytest.raises(DeclarationError, match="no audience"):
        _reject_unjudgeable_declarations((AllowedBlock(text="a note", audience=""),))


def test_duplicate_declaration_is_rejected() -> None:
    """Two rows for one comment leave one of them permanently unmatched."""
    entry = AllowedBlock(text="a note", audience="anyone")

    with pytest.raises(DeclarationError, match="declared twice"):
        _reject_unjudgeable_declarations((entry, entry))


def test_shipped_declarations_are_judgeable() -> None:
    """The declarations this gate ships pass their own self-check."""
    _reject_unjudgeable_declarations(_ALLOWED_BLOCKS)


def test_missing_template_exits_two(tmp_path: Path) -> None:
    """An unreadable source is a configuration error, not a pass."""
    _write_go_source(tmp_path)

    assert main(["--repo-root", str(tmp_path)]) == 2


def test_main_reports_violations(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Violations exit 1 and name the offending text on stderr."""
    _write_template(tmp_path, f"{_declared_blocks()}\n# Undeclared note.\n")

    assert main(["--repo-root", str(tmp_path)]) == 1
    assert "Undeclared note." in capsys.readouterr().err


def test_main_exits_zero_on_a_clean_template(tmp_path: Path) -> None:
    """A template carrying only declared blocks exits 0 through main()."""
    _write_template(tmp_path, _declared_blocks())

    assert main(["--repo-root", str(tmp_path)]) == 0
