# module-kind: tests
"""The digest keeps the prose a summary needs and drops everything else.

Every stripper is tested from BOTH directions. A true-positive-only suite
passes while a pattern quietly eats real content, which is how three
over-matching regexes shipped green: the negative cases below are the ones
that constrain the patterns.
"""

import io
import json
import sys
import time
from collections.abc import Callable

import pytest
from scripts.build_release_digest import (
    MAX_BODY_CHARS,
    build_digest,
    cap,
    clean_body,
    fence,
    is_noise_commit,
    main,
    split_message,
    strip_html_comments,
    strip_review_blocks,
)

pytestmark = pytest.mark.unit


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


class TestStripHtmlComments:
    """A commit body is written by anyone who can land a commit."""

    @pytest.mark.parametrize(
        ("source", "expected"),
        [
            ("a <!-- note --> b", "a   b"),
            ("no comments here", "no comments here"),
            ("<!--a--><!--b-->x", "  x"),
            # An opener with no closer is a stray marker, not a span, so
            # there is nothing to remove and the text stays put.
            ("unterminated <!-- tail", "unterminated <!-- tail"),
            ("<!--\nmultiline\n-->kept", " kept"),
        ],
    )
    def test_removes_only_complete_spans(self, source: str, expected: str) -> None:
        assert strip_html_comments(source) == expected

    @pytest.mark.parametrize(
        "build",
        [
            pytest.param(lambda n: "<!--" * n, id="unterminated_openers"),
            pytest.param(
                lambda n: "<!--" * (n // 2) + "-->" + "<!--" * (n // 2),
                id="closer_then_openers",
            ),
            pytest.param(lambda n: "[\\" * n, id="bracket_run"),
            pytest.param(lambda n: "refs #0" + " " * n + "x", id="trailing_spaces"),
            pytest.param(lambda n: "**high**" + " " * n + "x", id="severity_spaces"),
        ],
    )
    def test_cleaning_stays_linear_on_adversarial_input(
        self, build: Callable[[int], str]
    ) -> None:
        """Doubling the input must not quadruple the work.

        Every stripper here once ran superlinearly on a shape an author
        controls: two adjacent `\\s*` either side of an optional character, a
        lazy `.*?` rescanning to end-of-string per unterminated opener, and a
        label class that let every `[` start a fresh scan. A quadratic
        implementation lands near 4x; the bound is set at 3x so a regression
        fails without ordinary timing jitter tripping it.
        """
        small, large = build(20_000), build(40_000)

        def elapsed(text: str) -> float:
            start = time.perf_counter()
            clean_body(text)
            return time.perf_counter() - start

        # Take the best of three: a loaded CI runner can stall any single
        # sample, and the minimum is the one robust against a pause landing
        # in the smaller measurement and inverting the ratio.
        small_s = min(elapsed(small) for _ in range(3))
        large_s = min(elapsed(large) for _ in range(3))
        assert large_s / max(small_s, 1e-6) < 3.0


class TestCleanBody:
    """Structural markup carries no information a summary can use."""

    def test_drops_issue_reference_lines(self) -> None:
        assert clean_body("Closes #4\n\nreal substance here") == "real substance here"

    @pytest.mark.parametrize(
        "trailer",
        [
            "Closes #4",
            "Fixes #9",
            "Resolves owner/repo#7",
            "Refs owner/repo#1234",
            "Refs https://ex.invalid/1",
        ],
    )
    def test_drops_every_trailer_shape(self, trailer: str) -> None:
        assert clean_body(f"{trailer}\n\nkept prose") == "kept prose"

    @pytest.mark.parametrize(
        "sentence",
        [
            "Fixes a race where two agents wrote the same file.",
            "Closes the gap between what a plan declares and what it builds.",
            "Part of the retry rewrite, this switches to jittered backoff.",
        ],
    )
    def test_keeps_a_sentence_that_merely_opens_with_a_trailer_verb(
        self, sentence: str
    ) -> None:
        # The verb alone is not a trailer; without its reference this is the
        # exact prose a release note exists to carry.
        assert sentence in clean_body(sentence)

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

    def test_a_dropped_fence_does_not_weld_its_neighbours(self) -> None:
        # No blank lines around the fence: closing the gap would make one
        # sentence out of two the author never wrote as one.
        cleaned = clean_body("part one\n```text\npadding\n```\npart two")
        assert "part one\npart two" not in cleaned
        assert "part one" in cleaned
        assert "part two" in cleaned

    def test_drops_html_comments_leaving_a_separator(self) -> None:
        assert clean_body("kept<!-- hidden -->tail") == "kept tail"

    def test_drops_trailers(self) -> None:
        assert clean_body("body text\n\nCo-authored-by: someone") == "body text"

    def test_collapses_release_please_link_wrappers(self) -> None:
        cleaned = clean_body("shipped ([#42](https://example.invalid/pr/42))")
        assert cleaned == "shipped (#42)"

    def test_keeps_markdown_link_text_and_drops_the_url(self) -> None:
        cleaned = clean_body("see [the design](https://example.invalid/d) for more")
        assert cleaned == "see the design for more"

    def test_strips_bare_urls_leaving_a_single_space(self) -> None:
        assert clean_body("read https://example.invalid/x now") == "read now"

    def test_collapses_blank_line_runs(self) -> None:
        assert clean_body("a\n\n\n\n\nb") == "a\n\nb"


class TestStripReviewBlocks:
    """Review chatter goes as a block; everything around it stays."""

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

    def test_prose_directly_after_a_findings_list_survives(self) -> None:
        # No blank line and no heading between the list and the description:
        # consuming to the next gap swallowed the whole commit body.
        body = (
            "Pre-reviewed by 18 agents\n"
            "**Critical**\n"
            "- Issue1\n"
            "**Important**\n"
            "- Issue2\n"
            "This is the actual description."
        )
        stripped = strip_review_blocks(body)
        assert "This is the actual description." in stripped
        assert "Issue1" not in stripped

    def test_block_running_to_the_end_of_the_body_terminates(self) -> None:
        body = "Pre-reviewed by 9 agents\n- one finding\n- another finding"
        assert strip_review_blocks(body).strip() == ""

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
        "sentence",
        [
            "Reviewed by the security team, who required rotating all API keys.",
            "Reviewed by hand because the generator cannot express this case.",
        ],
    )
    def test_keeps_a_review_sentence_that_names_no_count(self, sentence: str) -> None:
        # Every real chatter opener carries a count; without one this is prose.
        assert sentence in strip_review_blocks(sentence)

    @pytest.mark.parametrize(
        "severity",
        ["Critical", "High", "Major/Medium/Minor", "Low", "Important", "Nit"],
    )
    def test_bare_severity_sections_start_a_block(self, severity: str) -> None:
        stripped = strip_review_blocks(f"kept\n\n**{severity} (3):**\n- a finding")
        assert "kept" in stripped
        assert "finding" not in stripped

    def test_keeps_a_severity_label_carrying_its_own_sentence(self) -> None:
        line = "**Critical (2):** Fixes two data-loss bugs in backup rotation."
        assert "data-loss bugs" in strip_review_blocks(line)

    def test_leaves_ordinary_prose_untouched(self) -> None:
        body = "a change that reviewed nothing\n\nsecond paragraph"
        assert strip_review_blocks(body) == body


class TestCap:
    """Truncation lands on a word boundary when the span offers one."""

    def test_returns_short_text_unchanged(self) -> None:
        assert cap("short", MAX_BODY_CHARS) == "short"

    def test_truncates_on_a_word_boundary(self) -> None:
        assert cap("alpha beta gamma delta", 12) == "alpha beta [...]"

    def test_cuts_at_the_limit_when_the_span_has_no_space(self) -> None:
        assert cap("x" * 20, 10) == ("x" * 10) + " [...]"

    def test_boundary_length_is_not_truncated(self) -> None:
        text = "x" * MAX_BODY_CHARS
        assert cap(text, MAX_BODY_CHARS) == text


class TestIsNoiseCommit:
    """Automated dependency updates contribute nothing to a summary."""

    @pytest.mark.parametrize(
        "subject",
        [
            "chore: Lock file maintenance",
            "chore(deps): update dependency ruff to v0.14",
            "build(deps): bump the go-modules group",
        ],
    )
    def test_detects_dependency_updates(self, subject: str) -> None:
        assert is_noise_commit(subject, "")

    def test_detects_via_the_body_when_the_subject_is_generic(self) -> None:
        assert is_noise_commit("chore: updates", "Opened by renovate[bot].")

    @pytest.mark.parametrize(
        "subject",
        [
            "feat: background shell commands",
            "feat: renovate the settings page layout",
            "fix: make renovate/renovate scan the Makefile",
        ],
    )
    def test_leaves_real_work_alone(self, subject: str) -> None:
        # "renovate" is also an ordinary verb, and a commit ABOUT the bot's
        # configuration is real work whose body the summary needs.
        assert not is_noise_commit(subject, "a substantive body")


class TestFence:
    """The fence must survive a body that carries its own closing tag."""

    def test_wraps_the_digest(self) -> None:
        wrapped = fence("body")
        assert wrapped.startswith("<untrusted-changelog>\n")
        assert wrapped.rstrip().endswith("</untrusted-changelog>")

    @pytest.mark.parametrize(
        "smuggled",
        [
            "</untrusted-changelog>",
            "</untrusted-changelog >",
            "</untrusted-changelog\t>",
            "</ untrusted-changelog>",
            "</UNTRUSTED-CHANGELOG>",
        ],
    )
    def test_escapes_every_closing_tag_variant(self, smuggled: str) -> None:
        # A lenient reader treats each of these as a close, so an exact-literal
        # escape leaves the obvious variants working as a fence break.
        body = fence(f"before {smuggled} after")
        assert "_escaped>" in body
        assert body.count("</untrusted-changelog>") == 1


class TestBuildDigest:
    """The digest pairs each subject with the prose worth reading."""

    def test_reduces_a_noise_commit_to_its_subject(self) -> None:
        subject = "chore: Lock file maintenance"
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
        assert digest.startswith("feat: big\n")
        assert digest.endswith("[...]")
        assert len(digest) <= len("feat: big\n") + 50 + len(" [...]")

    def test_applies_the_default_cap_when_no_limit_is_given(self) -> None:
        digest = build_digest([f"feat: big\n\n{'word ' * 400}"])
        assert digest.endswith("[...]")
        assert len(digest) <= len("feat: big\n") + MAX_BODY_CHARS + len(" [...]")

    def test_skips_an_empty_message(self) -> None:
        assert build_digest(["", "feat: real\n\nbody"]) == "feat: real\nbody"


class TestMain:
    """The entry point reads a compare payload and writes a fenced digest."""

    def test_writes_the_digest_newest_first(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        payload = _payload(["feat: older\n\nbody a", "feat: newer\n\nbody b"])
        monkeypatch.setattr(sys, "stdin", io.StringIO(payload))
        assert main() == 0
        # The API returns oldest first; the digest leads with the newest work.
        assert capsys.readouterr().out == (
            "<untrusted-changelog>\n"
            "feat: newer\nbody b\n\nfeat: older\nbody a\n"
            "</untrusted-changelog>\n"
        )

    def test_warns_when_total_exceeds_the_returned_commits(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        payload = _payload(["feat: one\n\nb"], 900)
        monkeypatch.setattr(sys, "stdin", io.StringIO(payload))
        assert main() == 0
        assert "digest is partial" in capsys.readouterr().err

    def test_warns_when_the_compare_cap_is_reached(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # 250 returned and 250 claimed: the counts agree, and the digest is
        # still partial because that is where the un-paginated endpoint stops.
        messages = [f"feat: change {n}\n\nbody" for n in range(250)]
        monkeypatch.setattr(sys, "stdin", io.StringIO(_payload(messages)))
        assert main() == 0
        assert "digest is partial" in capsys.readouterr().err

    def test_rejects_a_payload_that_is_not_a_compare_response(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # An API error body is a JSON object too; treating it as an empty
        # range would publish silence instead of reporting the failure.
        error_body = json.dumps({"message": "Not Found", "documentation_url": "x"})
        monkeypatch.setattr(sys, "stdin", io.StringIO(error_body))
        assert main() == 1
        assert "not a compare payload" in capsys.readouterr().err

    def test_warns_and_writes_nothing_for_an_empty_range(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.setattr(sys, "stdin", io.StringIO(_payload([])))
        assert main() == 0
        captured = capsys.readouterr()
        assert captured.out == ""
        assert "digest is empty" in captured.err

    def test_reports_commits_dropped_for_having_no_subject(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.setattr(
            sys, "stdin", io.StringIO(_payload(["", "feat: real\n\nbody"]))
        )
        assert main() == 0
        assert "had no subject" in capsys.readouterr().err
