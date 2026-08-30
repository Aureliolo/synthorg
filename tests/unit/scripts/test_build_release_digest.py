# module-kind: tests
"""The digest keeps the prose a summary needs and drops everything else."""

import io
import json
from typing import Final
from unittest.mock import patch

import pytest
from scripts.build_release_digest import (
    build_digest,
    cap,
    clean_body,
    is_noise_commit,
    main,
    split_message,
    strip_review_blocks,
)

pytestmark = pytest.mark.unit

_LIMIT: Final[int] = 600


def _payload(messages: list[str], total: int | None = None) -> str:
    """Return a GitHub compare payload carrying `messages`."""
    return json.dumps(
        {
            "total_commits": len(messages) if total is None else total,
            "commits": [{"commit": {"message": m}} for m in messages],
        }
    )


class TestSplitMessage:
    """A commit message divides into a subject line and everything after."""

    def test_splits_subject_from_body(self) -> None:
        subject, body = split_message("feat: a thing\n\nthe body\nmore body")
        assert subject == "feat: a thing"
        assert body.strip() == "the body\nmore body"

    def test_subject_only_message_yields_empty_body(self) -> None:
        subject, body = split_message("chore: tidy")
        assert subject == "chore: tidy"
        assert body == ""


class TestCleanBody:
    """Structural markup carries no information a summary can use."""

    def test_drops_issue_reference_lines(self) -> None:
        assert clean_body("Closes #2862\n\nreal substance here") == (
            "real substance here"
        )

    def test_drops_headings(self) -> None:
        assert clean_body("## Summary\n\nwhat it does") == "what it does"

    def test_drops_tables(self) -> None:
        body = "intro\n\n| Update | Change |\n|---|---|\n| a | b |"
        assert clean_body(body) == "intro"

    def test_drops_fenced_code(self) -> None:
        body = "before\n\n```python\nx = 1\n```\n\nafter"
        cleaned = clean_body(body)
        assert "x = 1" not in cleaned
        assert "before" in cleaned
        assert "after" in cleaned

    def test_drops_html_comments(self) -> None:
        assert clean_body("kept <!-- hidden --> tail") == "kept  tail"

    def test_drops_trailers(self) -> None:
        assert clean_body("body text\n\nCo-authored-by: someone") == "body text"

    def test_collapses_release_please_link_wrappers(self) -> None:
        cleaned = clean_body("shipped ([#2883](https://example.invalid/pr/2883))")
        assert cleaned == "shipped (#2883)"

    def test_keeps_markdown_link_text_and_drops_the_url(self) -> None:
        cleaned = clean_body("see [the design](https://example.invalid/d) for more")
        assert cleaned == "see the design for more"

    def test_strips_bare_urls(self) -> None:
        assert clean_body("read https://example.invalid/x now").split() == [
            "read",
            "now",
        ]

    def test_collapses_blank_line_runs(self) -> None:
        assert clean_body("a\n\n\n\n\nb") == "a\n\nb"


class TestStripReviewBlocks:
    """Review chatter is dropped as a block, opener and findings together."""

    def test_drops_opener_and_its_findings_list(self) -> None:
        body = (
            "the real change\n"
            "\n"
            "Pre-reviewed by 18 agents, 35 findings addressed (3 Critical):\n"
            "\n"
            "- a finding\n"
            "- another finding\n"
            "\n"
            "prose that follows"
        )
        stripped = strip_review_blocks(body)
        assert "the real change" in stripped
        assert "prose that follows" in stripped
        assert "finding" not in stripped
        assert "Pre-reviewed" not in stripped

    def test_block_terminates_at_the_next_heading(self) -> None:
        body = "Pre-reviewed by 12 agents\n- a finding\n\n## Verification\n\nkept"
        stripped = strip_review_blocks(body)
        assert "finding" not in stripped
        assert "## Verification" in stripped
        assert "kept" in stripped

    @pytest.mark.parametrize(
        "opener",
        [
            "Pre-reviewed by 12 agents (focused subset); 25 findings addressed.",
            "Pre-PR reviewed by 9 agents (go-reviewer, go-security-reviewer)",
            "Pre-reviewed locally before this PR opened, across the roster",
            "Reviewed by 7 agents before this PR existed.",
            "14 agents reviewed the branch (~30 findings)",
            "8 local review agents; 12 findings addressed.",
            "Findings from the pre-PR review round, fixed in full:",
        ],
    )
    def test_recognises_every_observed_opener(self, opener: str) -> None:
        stripped = strip_review_blocks(f"substance\n\n{opener}\n- detail")
        assert "substance" in stripped
        assert "detail" not in stripped

    @pytest.mark.parametrize(
        "severity",
        ["Critical", "High", "Major/Medium/Minor", "Low", "Important", "Nit"],
    )
    def test_severity_sections_start_a_block(self, severity: str) -> None:
        stripped = strip_review_blocks(f"kept\n\n**{severity} (3):**\n- a finding")
        assert "kept" in stripped
        assert "finding" not in stripped

    def test_leaves_ordinary_prose_untouched(self) -> None:
        body = "a change that reviewed nothing\n\nsecond paragraph"
        assert strip_review_blocks(body) == body


class TestCap:
    """Truncation lands on a word boundary and says it happened."""

    def test_returns_short_text_unchanged(self) -> None:
        assert cap("short", _LIMIT) == "short"

    def test_truncates_on_a_word_boundary(self) -> None:
        capped = cap("alpha beta gamma delta", 12)
        assert capped.endswith("[...]")
        assert "delta" not in capped

    def test_boundary_length_is_not_truncated(self) -> None:
        text = "x" * _LIMIT
        assert cap(text, _LIMIT) == text


class TestIsNoiseCommit:
    """Automated dependency updates contribute nothing to a summary."""

    @pytest.mark.parametrize(
        "subject",
        [
            "chore: Lock file maintenance (#2882)",
            "chore(deps): update renovate/renovate to v41",
            "build(deps): bump dependabot fetch-metadata",
        ],
    )
    def test_detects_dependency_updates(self, subject: str) -> None:
        assert is_noise_commit(subject, "")

    def test_detects_via_the_body_when_the_subject_is_generic(self) -> None:
        assert is_noise_commit("chore: updates", "This PR contains renovate updates.")

    def test_leaves_real_work_alone(self) -> None:
        assert not is_noise_commit("feat: background shell commands", "a body")


class TestBuildDigest:
    """The digest pairs each subject with the prose worth reading."""

    def test_reduces_a_noise_commit_to_its_subject(self) -> None:
        subject = "chore: Lock file maintenance (#2882)"
        digest = build_digest([f"{subject}\n\n| Update | Change |\n|---|---|"])
        assert digest == subject

    def test_keeps_subject_and_cleaned_body(self) -> None:
        digest = build_digest(["feat: a thing\n\nCloses #1\n\nwhat it does"])
        assert digest == "feat: a thing\nwhat it does"

    def test_emits_subject_alone_when_the_body_cleans_away(self) -> None:
        assert build_digest(["fix: a thing\n\nCloses #4"]) == "fix: a thing"

    def test_separates_commits_by_a_blank_line(self) -> None:
        digest = build_digest(["feat: one\n\nbody one", "feat: two\n\nbody two"])
        assert digest == "feat: one\nbody one\n\nfeat: two\nbody two"

    def test_applies_the_per_commit_cap(self) -> None:
        digest = build_digest([f"feat: big\n\n{'word ' * 400}"], limit=50)
        assert digest.endswith("[...]")
        assert len(digest) < 100

    def test_skips_an_empty_message(self) -> None:
        assert build_digest(["", "feat: real\n\nbody"]) == "feat: real\nbody"


class TestMain:
    """The entry point reads a compare payload and writes the digest."""

    def test_writes_the_digest_oldest_last(self) -> None:
        payload = _payload(["feat: older\n\nbody a", "feat: newer\n\nbody b"])
        stdout = io.StringIO()
        with (
            patch("sys.stdin", io.StringIO(payload)),
            patch("sys.stdout", stdout),
        ):
            assert main() == 0
        # The API returns oldest first; the digest leads with the newest work.
        assert stdout.getvalue().index("newer") < stdout.getvalue().index("older")

    def test_warns_when_the_compare_response_is_partial(self) -> None:
        payload = _payload(["feat: one\n\nbody"], total=900)
        stderr = io.StringIO()
        with (
            patch("sys.stdin", io.StringIO(payload)),
            patch("sys.stdout", io.StringIO()),
            patch("sys.stderr", stderr),
        ):
            assert main() == 0
        assert "digest is partial" in stderr.getvalue()

    def test_handles_an_empty_range(self) -> None:
        stdout = io.StringIO()
        with (
            patch("sys.stdin", io.StringIO(_payload([]))),
            patch("sys.stdout", stdout),
        ):
            assert main() == 0
        assert stdout.getvalue() == ""
