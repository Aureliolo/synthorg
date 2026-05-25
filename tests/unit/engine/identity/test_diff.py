"""Tests for compute_diff, AgentIdentityDiff, and IdentityFieldChange."""

import pytest
from pydantic import BaseModel, ConfigDict

from synthorg.engine.identity.diff import (
    AgentIdentityDiff,
    IdentityFieldChange,
    compute_diff,
)


class _Flat(BaseModel):
    """Minimal flat model -- simulates a simple identity with two fields."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    role: str


class _Nested(BaseModel):
    """Model with one level of nesting -- simulates personality sub-model."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    settings: dict[str, object]


class _Deep(BaseModel):
    """Model with a nested sub-model."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    title: str
    personality: _Flat


class TestComputeDiffIdentical:
    """Identical snapshots produce empty diffs."""

    @pytest.mark.unit
    def test_identical_flat_no_changes(self) -> None:
        m = _Flat(name="alice", role="engineer")
        result = compute_diff("agt-1", m, m, from_version=1, to_version=2)
        assert result.field_changes == ()
        assert result.summary == "no changes"

    @pytest.mark.unit
    def test_identical_nested_no_changes(self) -> None:
        m = _Deep(title="Lead", personality=_Flat(name="bob", role="manager"))
        result = compute_diff("agt-2", m, m, from_version=3, to_version=4)
        assert result.field_changes == ()
        assert result.summary == "no changes"


class TestComputeDiffModified:
    """Modified fields are detected correctly."""

    @pytest.mark.unit
    def test_single_flat_field_modified(self) -> None:
        old = _Flat(name="alice", role="engineer")
        new = _Flat(name="alice", role="senior-engineer")
        result = compute_diff("agt-1", old, new, from_version=1, to_version=2)
        assert len(result.field_changes) == 1
        change = result.field_changes[0]
        assert change.field_path == "role"
        assert change.change_type == "modified"
        assert "engineer" in (change.old_value or "")
        assert "senior-engineer" in (change.new_value or "")

    @pytest.mark.unit
    def test_multiple_fields_modified(self) -> None:
        old = _Flat(name="alice", role="engineer")
        new = _Flat(name="bob", role="manager")
        result = compute_diff("agt-1", old, new, from_version=1, to_version=2)
        assert len(result.field_changes) == 2
        paths = {c.field_path for c in result.field_changes}
        assert paths == {"name", "role"}
        assert result.summary == "2 fields changed"

    @pytest.mark.unit
    def test_nested_field_modified(self) -> None:
        old = _Deep(title="Lead", personality=_Flat(name="alice", role="eng"))
        new = _Deep(title="Lead", personality=_Flat(name="alice", role="mgr"))
        result = compute_diff("agt-1", old, new, from_version=1, to_version=2)
        assert len(result.field_changes) == 1
        assert result.field_changes[0].field_path == "personality.role"
        assert result.field_changes[0].change_type == "modified"

    @pytest.mark.unit
    def test_top_level_and_nested_fields_both_changed(self) -> None:
        old = _Deep(title="Lead", personality=_Flat(name="alice", role="eng"))
        new = _Deep(title="Senior Lead", personality=_Flat(name="bob", role="eng"))
        result = compute_diff("agt-1", old, new, from_version=2, to_version=3)
        paths = {c.field_path for c in result.field_changes}
        assert "title" in paths
        assert "personality.name" in paths
        assert len(result.field_changes) == 2

    @pytest.mark.unit
    def test_summary_one_field(self) -> None:
        old = _Flat(name="alice", role="eng")
        new = _Flat(name="alice", role="mgr")
        result = compute_diff("agt-1", old, new, from_version=1, to_version=2)
        assert result.summary == "1 field changed"


class TestComputeDiffAddedRemoved:
    """Added/removed keys in dict fields are reported correctly."""

    @pytest.mark.unit
    def test_dict_key_added(self) -> None:
        old = _Nested(name="x", settings={"a": 1})
        new = _Nested(name="x", settings={"a": 1, "b": 2})
        result = compute_diff("agt-1", old, new, from_version=1, to_version=2)
        assert len(result.field_changes) == 1
        change = result.field_changes[0]
        assert change.field_path == "settings.b"
        assert change.change_type == "added"
        assert change.old_value is None

    @pytest.mark.unit
    def test_dict_key_removed(self) -> None:
        old = _Nested(name="x", settings={"a": 1, "b": 2})
        new = _Nested(name="x", settings={"a": 1})
        result = compute_diff("agt-1", old, new, from_version=1, to_version=2)
        assert len(result.field_changes) == 1
        change = result.field_changes[0]
        assert change.field_path == "settings.b"
        assert change.change_type == "removed"
        assert change.new_value is None


class TestComputeDiffMetadata:
    """Diff result carries correct agent_id and version numbers."""

    @pytest.mark.unit
    def test_metadata_preserved(self) -> None:
        m = _Flat(name="x", role="y")
        result = compute_diff("agt-42", m, m, from_version=7, to_version=8)
        assert result.agent_id == "agt-42"
        assert result.from_version == 7
        assert result.to_version == 8


class TestComputeDiffOrdering:
    """Field changes are sorted by field_path."""

    @pytest.mark.unit
    def test_changes_sorted_by_field_path(self) -> None:
        old = _Flat(name="a", role="x")
        new = _Flat(name="b", role="y")
        result = compute_diff("agt-1", old, new, from_version=1, to_version=2)
        paths = [c.field_path for c in result.field_changes]
        assert paths == sorted(paths)


class TestIdentityFieldChangeModel:
    """IdentityFieldChange validation."""

    @pytest.mark.unit
    def test_frozen(self) -> None:
        from pydantic import ValidationError

        c = IdentityFieldChange(
            field_path="name",
            change_type="modified",
            old_value='"a"',
            new_value='"b"',
        )
        with pytest.raises(ValidationError, match="frozen"):
            c.field_path = "other"  # type: ignore[misc]


class TestAgentIdentityDiffModel:
    """AgentIdentityDiff validation."""

    @pytest.mark.unit
    def test_empty_diff_valid(self) -> None:
        d = AgentIdentityDiff(
            agent_id="agt-1",
            from_version=1,
            to_version=2,
        )
        assert d.field_changes == ()
        assert d.summary == "no changes"

    @pytest.mark.unit
    def test_from_version_ge_one(self) -> None:
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            AgentIdentityDiff(
                agent_id="agt-1",
                from_version=0,
                to_version=1,
            )

    @pytest.mark.unit
    def test_same_from_and_to_version_raises(self) -> None:
        from pydantic import ValidationError

        with pytest.raises(ValidationError, match="must be less than"):
            AgentIdentityDiff(
                agent_id="agt-1",
                from_version=3,
                to_version=3,
            )

    @pytest.mark.unit
    def test_reversed_versions_raises(self) -> None:
        from pydantic import ValidationError

        with pytest.raises(ValidationError, match="must be less than"):
            AgentIdentityDiff(
                agent_id="agt-1",
                from_version=5,
                to_version=2,
            )


class TestIdentityFieldChangeModelValidator:
    """IdentityFieldChange model_validator enforces change_type invariants."""

    @pytest.mark.unit
    @pytest.mark.parametrize(
        ("change_type", "old_value", "new_value", "error_match"),
        [
            # Valid cases (error_match=None means no error expected)
            ("added", None, '"value"', None),
            ("removed", '"value"', None, None),
            ("modified", '"old"', '"new"', None),
            # Invalid: added with old_value present
            ("added", '"previous"', '"new"', "old_value=None"),
            # Invalid: added with new_value missing
            ("added", None, None, "new_value to be set"),
            # Invalid: removed with new_value present
            ("removed", '"old"', '"should-not-be-here"', "new_value=None"),
            # Invalid: removed with old_value missing
            ("removed", None, None, "old_value to be set"),
            # Invalid: modified with None old_value
            ("modified", None, '"new"', "both old_value and new_value"),
            # Invalid: modified with None new_value
            ("modified", '"old"', None, "both old_value and new_value"),
        ],
    )
    def test_change_invariant_matrix(
        self,
        change_type: str,
        old_value: str | None,
        new_value: str | None,
        error_match: str | None,
    ) -> None:
        from pydantic import ValidationError

        if error_match is None:
            c = IdentityFieldChange(
                field_path="settings.x",
                change_type=change_type,  # type: ignore[arg-type]
                old_value=old_value,
                new_value=new_value,
            )
            assert c.change_type == change_type
        else:
            with pytest.raises(ValidationError, match=error_match):
                IdentityFieldChange(
                    field_path="settings.x",
                    change_type=change_type,  # type: ignore[arg-type]
                    old_value=old_value,
                    new_value=new_value,
                )
