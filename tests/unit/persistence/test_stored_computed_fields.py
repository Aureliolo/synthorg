"""A model that derives a value survives its own round trip through a column.

Every frozen model here sets ``extra="forbid"``, and ``model_dump`` emits
``@computed_field`` properties. So a model carrying one writes to a JSON column
fine and raises on every later read: a live approvals page showed a count of
106 above an empty list, because the count is SQL and the list parses rows.

The invariant is about the pair, not about the one field that exposed it: what
is stored is what the model was built from, and what is read is tolerant of a
derived value an older writer left behind, because no migration can restore a
value the model computes anyway.
"""

from datetime import UTC, datetime
from typing import Self

import pytest
from pydantic import BaseModel, ConfigDict, computed_field, model_validator

from synthorg.approval.enums import ApprovalRiskLevel
from synthorg.core.evidence import EvidencePackage, RecommendedAction
from synthorg.core.types import NotBlankStr
from synthorg.persistence._shared.computed_fields import (
    dump_stored_json,
    load_stored_json,
)
from tests._shared import as_uuid

pytestmark = pytest.mark.unit


class _Derived(BaseModel):
    """A model with one stored field and one derived from it."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    parts: tuple[str, ...] = ()

    # pydantic stacks this pair by design and mypy cannot model the
    # pass-through. ``synthorg.*`` disables the code wholesale; the allowlist
    # is deliberately not extended to tests, so the one test model that needs
    # a computed field says so here.
    @computed_field  # type: ignore[prop-decorator]
    @property
    def part_count(self) -> int:
        """How many parts there are.

        Returns:
            The number of parts.
        """
        return len(self.parts)

    @model_validator(mode="after")
    def _noop(self) -> Self:
        """Present so the model matches the shape the codebase uses.

        Returns:
            ``Self`` instance.
        """
        return self


class TestTheRoundTrip:
    def test_a_derived_value_is_not_stored(self) -> None:
        stored = dump_stored_json(_Derived(parts=("a", "b")))
        assert "part_count" not in stored
        assert stored["parts"] == ["a", "b"]

    def test_what_was_stored_reads_back(self) -> None:
        original = _Derived(parts=("a", "b"))
        assert load_stored_json(_Derived, dump_stored_json(original)) == original

    def test_a_plain_dump_would_not_read_back(self) -> None:
        # The defect, stated as the thing the helpers exist to prevent: the
        # model's own serialiser produces something its own validator refuses.
        with pytest.raises(ValueError, match="part_count"):
            _Derived.model_validate(_Derived(parts=("a",)).model_dump(mode="json"))

    def test_a_row_an_older_writer_left_still_reads(self) -> None:
        # No migration can help: the value is derived, so the stored copy is
        # not information. Dropping it is the only honest repair, and the row
        # is unreadable until something does.
        legacy = {"parts": ["a", "b"], "part_count": 99}
        assert load_stored_json(_Derived, legacy).part_count == 2


class TestTheEvidencePackageThatFoundIt:
    def _package(self) -> EvidencePackage:
        return EvidencePackage(
            id=NotBlankStr(str(as_uuid("evidence-1"))),
            title=NotBlankStr("A deploy needs a decision"),
            narrative=NotBlankStr("The release is ready and needs a human to say so"),
            recommended_actions=(
                RecommendedAction(
                    action_type=NotBlankStr("approve"),
                    label=NotBlankStr("Approve"),
                    description=NotBlankStr("Ship the release"),
                ),
            ),
            source_agent_id=NotBlankStr("agent-1"),
            risk_level=ApprovalRiskLevel.MEDIUM,
            created_at=datetime(2026, 8, 19, tzinfo=UTC),
        )

    def test_it_round_trips(self) -> None:
        package = self._package()
        assert load_stored_json(EvidencePackage, dump_stored_json(package)) == package

    def test_its_derived_signature_state_is_not_stored(self) -> None:
        assert "is_fully_signed" not in dump_stored_json(self._package())
