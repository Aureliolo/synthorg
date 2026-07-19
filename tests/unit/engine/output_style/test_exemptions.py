"""Unit tests for sanctioned-exemption resolution and marker parsing."""

import pytest

from synthorg.engine.output_style.exemptions import (
    ExemptionResolver,
    OutputContext,
    parse_exemption_markers,
)
from synthorg.engine.output_style.models import (
    ExemptionScopeKind,
    OutputChannel,
    SanctionedExemption,
)


def _exemption(**overrides: object) -> SanctionedExemption:
    fields: dict[str, object] = {
        "rule_id": "emdash_literal",
        "scope_kind": ExemptionScopeKind.PATH,
        "match": "src/textfilter/**",
        "reason": "building an em-dash filter",
    }
    fields.update(overrides)
    return SanctionedExemption(**fields)  # type: ignore[arg-type]


class TestExemptionResolver:
    @pytest.mark.unit
    def test_path_scope_matches(self) -> None:
        resolver = ExemptionResolver((_exemption(),))
        ctx = OutputContext(
            channel=OutputChannel.CODE_FILE,
            file_path="src/textfilter/strip.py",
        )
        assert resolver.resolve("emdash_literal", ctx) is not None

    @pytest.mark.unit
    def test_path_scope_normalises_backslashes(self) -> None:
        resolver = ExemptionResolver((_exemption(),))
        ctx = OutputContext(
            channel=OutputChannel.CODE_FILE,
            file_path="src\\textfilter\\strip.py",
        )
        assert resolver.resolve("emdash_literal", ctx) is not None

    @pytest.mark.unit
    def test_no_match_outside_scope(self) -> None:
        resolver = ExemptionResolver((_exemption(),))
        ctx = OutputContext(
            channel=OutputChannel.CODE_FILE,
            file_path="src/other/mod.py",
        )
        assert resolver.resolve("emdash_literal", ctx) is None

    @pytest.mark.unit
    def test_wrong_rule_id_not_exempt(self) -> None:
        resolver = ExemptionResolver((_exemption(),))
        ctx = OutputContext(
            channel=OutputChannel.CODE_FILE,
            file_path="src/textfilter/strip.py",
        )
        assert resolver.resolve("some_other_rule", ctx) is None

    @pytest.mark.unit
    def test_wildcard_rule_id_matches_any(self) -> None:
        resolver = ExemptionResolver((_exemption(rule_id="*"),))
        ctx = OutputContext(
            channel=OutputChannel.CODE_FILE,
            file_path="src/textfilter/strip.py",
        )
        assert resolver.resolve("anything", ctx) is not None

    @pytest.mark.unit
    def test_task_type_scope_case_insensitive(self) -> None:
        resolver = ExemptionResolver(
            (_exemption(scope_kind=ExemptionScopeKind.TASK_TYPE, match="Build-Filter"),)
        )
        ctx = OutputContext(channel=OutputChannel.DELIVERABLE, task_type="build-filter")
        assert resolver.resolve("emdash_literal", ctx) is not None

    @pytest.mark.unit
    def test_department_scope(self) -> None:
        resolver = ExemptionResolver(
            (_exemption(scope_kind=ExemptionScopeKind.DEPARTMENT, match="linguistics"),)
        )
        ctx = OutputContext(channel=OutputChannel.MESSAGE, department="Linguistics")
        assert resolver.resolve("emdash_literal", ctx) is not None

    @pytest.mark.unit
    def test_deliverable_tag_scope(self) -> None:
        resolver = ExemptionResolver(
            (
                _exemption(
                    scope_kind=ExemptionScopeKind.DELIVERABLE_TAG, match="typography"
                ),
            )
        )
        ctx = OutputContext(
            channel=OutputChannel.DELIVERABLE,
            deliverable_tags=("typography", "docs"),
        )
        assert resolver.resolve("emdash_literal", ctx) is not None

    @pytest.mark.unit
    def test_absent_context_field_never_matches(self) -> None:
        resolver = ExemptionResolver((_exemption(),))
        ctx = OutputContext(channel=OutputChannel.MESSAGE)
        assert resolver.resolve("emdash_literal", ctx) is None


class TestMarkerParsing:
    @pytest.mark.unit
    def test_parse_single_marker(self) -> None:
        markers = parse_exemption_markers(
            "text\n# output-style-allow: emdash_literal -- building a filter\nmore"
        )
        assert len(markers) == 1
        assert markers[0].rule_id == "emdash_literal"
        assert "filter" in markers[0].reason

    @pytest.mark.unit
    def test_no_markers(self) -> None:
        assert parse_exemption_markers("plain text with no marker") == ()

    @pytest.mark.unit
    def test_marker_does_not_grant_exemption(self) -> None:
        # A marker with no matching sanctioned scope must not exempt.
        resolver = ExemptionResolver(())
        ctx = OutputContext(
            channel=OutputChannel.CODE_FILE,
            file_path="src/anywhere.py",
        )
        assert resolver.resolve("emdash_literal", ctx) is None
