"""Tests for ReportingLine and Department reporting-line validation."""

import pytest
from pydantic import ValidationError

from synthorg.core.company_departments import (
    Department,
    ReportingLine,
)

# ── ReportingLine ─────────────────────────────────────────────────


@pytest.mark.unit
class TestReportingLine:
    """Tests for ReportingLine validation and immutability."""

    def test_valid(self) -> None:
        """Accept a valid reporting line."""
        r = ReportingLine(subordinate="dev", supervisor="lead")
        assert r.subordinate == "dev"
        assert r.supervisor == "lead"

    def test_self_report_rejected(self) -> None:
        """Reject self-reporting relationships."""
        with pytest.raises(ValidationError, match="cannot report to themselves"):
            ReportingLine(subordinate="dev", supervisor="dev")

    def test_self_report_case_insensitive(self) -> None:
        """Self-report check is case-insensitive."""
        with pytest.raises(ValidationError, match="cannot report to themselves"):
            ReportingLine(subordinate="Dev", supervisor="dev")

    def test_frozen(self) -> None:
        """Ensure ReportingLine is immutable."""
        r = ReportingLine(subordinate="dev", supervisor="lead")
        with pytest.raises(ValidationError):
            r.subordinate = "other"  # type: ignore[misc]

    def test_subordinate_id_accepted(self) -> None:
        """Accept optional subordinate_id and supervisor_id."""
        r = ReportingLine(
            subordinate="Backend Developer",
            supervisor="Architect",
            subordinate_id="backend-1",
            supervisor_id="arch-main",
        )
        assert r.subordinate_id == "backend-1"
        assert r.supervisor_id == "arch-main"

    def test_ids_default_to_none(self) -> None:
        """IDs default to None when omitted."""
        r = ReportingLine(subordinate="dev", supervisor="lead")
        assert r.subordinate_id is None
        assert r.supervisor_id is None

    def test_same_role_different_ids_accepted(self) -> None:
        """Same role name with different IDs passes self-report check."""
        r = ReportingLine(
            subordinate="Data Analyst",
            subordinate_id="analyst-secondary",
            supervisor="Data Analyst",
            supervisor_id="analyst-primary",
        )
        assert r.subordinate == "Data Analyst"
        assert r.supervisor == "Data Analyst"

    def test_same_id_self_report_rejected(self) -> None:
        """Reject when subordinate_id equals supervisor_id."""
        with pytest.raises(ValidationError, match="cannot report to themselves"):
            ReportingLine(
                subordinate="Dev A",
                subordinate_id="dev-1",
                supervisor="Dev B",
                supervisor_id="dev-1",
            )

    def test_id_self_report_case_insensitive(self) -> None:
        """Self-report ID check is case-insensitive."""
        with pytest.raises(ValidationError, match="cannot report to themselves"):
            ReportingLine(
                subordinate="Dev A",
                subordinate_id="Dev-1",
                supervisor="Dev B",
                supervisor_id="dev-1",
            )

    def test_asymmetric_subordinate_id_not_self_report(self) -> None:
        """Asymmetric ID (subordinate_id set, supervisor_id None) accepted."""
        r = ReportingLine(
            subordinate="dev",
            subordinate_id="dev-1",
            supervisor="dev",
        )
        assert r.subordinate_id == "dev-1"
        assert r.supervisor_id is None

    def test_asymmetric_supervisor_id_not_self_report(self) -> None:
        """Asymmetric ID (supervisor_id set, subordinate_id None) accepted."""
        r = ReportingLine(
            subordinate="dev",
            supervisor="dev",
            supervisor_id="dev-lead",
        )
        assert r.supervisor_id == "dev-lead"
        assert r.subordinate_id is None

    def test_asymmetric_id_name_collision_accepted(self) -> None:
        """Name matching other side's ID is accepted when ID presence differs."""
        r = ReportingLine(
            subordinate="analyst-primary",
            supervisor="Data Analyst",
            supervisor_id="analyst-primary",
        )
        assert r.subordinate == "analyst-primary"
        assert r.subordinate_id is None
        assert r.supervisor_id == "analyst-primary"

    def test_subordinate_key_returns_id_when_present(self) -> None:
        """subordinate_key returns subordinate_id when set."""
        r = ReportingLine(
            subordinate="Backend Developer",
            subordinate_id="backend-senior",
            supervisor="architect",
        )
        assert r.subordinate_key == "backend-senior"

    def test_subordinate_key_returns_name_when_no_id(self) -> None:
        """subordinate_key falls back to subordinate when no ID."""
        r = ReportingLine(subordinate="dev", supervisor="lead")
        assert r.subordinate_key == "dev"

    def test_supervisor_key_returns_id_when_present(self) -> None:
        """supervisor_key returns supervisor_id when set."""
        r = ReportingLine(
            subordinate="dev",
            supervisor="Backend Developer",
            supervisor_id="backend-senior",
        )
        assert r.supervisor_key == "backend-senior"

    def test_supervisor_key_returns_name_when_no_id(self) -> None:
        """supervisor_key falls back to supervisor when no ID."""
        r = ReportingLine(subordinate="dev", supervisor="lead")
        assert r.supervisor_key == "lead"

    def test_computed_keys_in_model_dump_roundtrip(self) -> None:
        """Computed keys appear in model_dump and survive round-trip."""
        r = ReportingLine(
            subordinate="Backend Developer",
            subordinate_id="backend-senior",
            supervisor="Team Lead",
            supervisor_id="lead-001",
        )
        data = r.model_dump()
        assert data["subordinate_key"] == "backend-senior"
        assert data["supervisor_key"] == "lead-001"

        # Under extra="forbid", computed fields cannot round-trip as
        # inputs; recompute them by feeding back only the stored fields.
        r2 = ReportingLine.model_validate(
            {
                k: v
                for k, v in data.items()
                if k not in {"subordinate_key", "supervisor_key"}
            },
        )
        assert r2.subordinate_key == "backend-senior"
        assert r2.supervisor_key == "lead-001"

    @pytest.mark.parametrize(
        ("field", "value", "match"),
        [
            ("subordinate_id", "", "at least 1 character"),
            ("subordinate_id", "  ", "whitespace-only"),
            ("supervisor_id", "", "at least 1 character"),
            ("supervisor_id", "  ", "whitespace-only"),
        ],
    )
    def test_blank_id_rejected(
        self,
        field: str,
        value: str,
        match: str,
    ) -> None:
        """Reject empty or whitespace-only IDs."""
        with pytest.raises(ValidationError, match=match):
            ReportingLine(
                subordinate="dev",
                supervisor="lead",
                **{field: value},
            )


# ── Department reporting-line validation ──────────────────────────


@pytest.mark.unit
class TestDepartmentReportingLines:
    """Tests for Department with reporting_lines validation."""

    def test_with_reporting_lines(self) -> None:
        """Accept department with reporting lines."""
        dept = Department(
            name="eng",
            head="cto",
            reporting_lines=(ReportingLine(subordinate="dev", supervisor="lead"),),
        )
        assert len(dept.reporting_lines) == 1

    def test_backward_compatible_defaults(self) -> None:
        """Default reporting_lines for backward compatibility."""
        dept = Department(name="eng", head="cto")
        assert dept.reporting_lines == ()

    def test_duplicate_subordinates_rejected(self) -> None:
        """Reject duplicate subordinates in reporting lines."""
        with pytest.raises(ValidationError, match="Duplicate subordinates"):
            Department(
                name="eng",
                head="cto",
                reporting_lines=(
                    ReportingLine(subordinate="dev", supervisor="lead"),
                    ReportingLine(
                        subordinate="dev",
                        supervisor="manager",
                    ),
                ),
            )

    def test_duplicate_subordinates_case_insensitive(self) -> None:
        """Reject subordinates that differ only by case."""
        with pytest.raises(ValidationError, match="Duplicate subordinates"):
            Department(
                name="eng",
                head="cto",
                reporting_lines=(
                    ReportingLine(
                        subordinate="Alice",
                        supervisor="lead",
                    ),
                    ReportingLine(
                        subordinate="alice",
                        supervisor="manager",
                    ),
                ),
            )

    def test_duplicate_subordinates_whitespace_insensitive(self) -> None:
        """Reject subordinates that differ only by surrounding whitespace."""
        with pytest.raises(ValidationError, match="Duplicate subordinates"):
            Department(
                name="eng",
                head="cto",
                reporting_lines=(
                    ReportingLine(
                        subordinate="Alice",
                        supervisor="lead",
                    ),
                    ReportingLine(
                        subordinate=" Alice ",
                        supervisor="manager",
                    ),
                ),
            )

    def test_same_role_different_subordinate_ids_accepted(self) -> None:
        """Allow same role name when subordinate_ids differ."""
        dept = Department(
            name="eng",
            head="architect",
            reporting_lines=(
                ReportingLine(
                    subordinate="Backend Developer",
                    subordinate_id="backend-1",
                    supervisor="architect",
                ),
                ReportingLine(
                    subordinate="Backend Developer",
                    subordinate_id="backend-2",
                    supervisor="architect",
                ),
                ReportingLine(
                    subordinate="Backend Developer",
                    subordinate_id="backend-3",
                    supervisor="architect",
                ),
            ),
        )
        assert len(dept.reporting_lines) == 3

    def test_duplicate_subordinate_ids_rejected(self) -> None:
        """Reject duplicate subordinate_ids."""
        with pytest.raises(ValidationError, match="Duplicate subordinates"):
            Department(
                name="eng",
                head="architect",
                reporting_lines=(
                    ReportingLine(
                        subordinate="Backend Developer",
                        subordinate_id="backend-1",
                        supervisor="architect",
                    ),
                    ReportingLine(
                        subordinate="Backend Developer",
                        subordinate_id="backend-1",
                        supervisor="architect",
                    ),
                ),
            )

    def test_mixed_id_and_no_id_subordinates(self) -> None:
        """Allow mixing entries with and without subordinate_id."""
        dept = Department(
            name="eng",
            head="architect",
            reporting_lines=(
                ReportingLine(
                    subordinate="Frontend Developer",
                    supervisor="architect",
                ),
                ReportingLine(
                    subordinate="Backend Developer",
                    subordinate_id="backend-1",
                    supervisor="architect",
                ),
                ReportingLine(
                    subordinate="Backend Developer",
                    subordinate_id="backend-2",
                    supervisor="architect",
                ),
            ),
        )
        assert len(dept.reporting_lines) == 3

    def test_duplicate_subordinate_ids_case_insensitive(self) -> None:
        """Reject subordinate_ids that differ only by case."""
        with pytest.raises(ValidationError, match="Duplicate subordinates"):
            Department(
                name="eng",
                head="architect",
                reporting_lines=(
                    ReportingLine(
                        subordinate="Backend Developer",
                        subordinate_id="Backend-1",
                        supervisor="architect",
                    ),
                    ReportingLine(
                        subordinate="Backend Developer",
                        subordinate_id="backend-1",
                        supervisor="architect",
                    ),
                ),
            )

    def test_name_matching_other_subordinate_id_accepted(self) -> None:
        """Name-based and ID-based keys in different namespaces do not collide."""
        dept = Department(
            name="eng",
            head="architect",
            reporting_lines=(
                ReportingLine(
                    subordinate="backend-1",
                    supervisor="architect",
                ),
                ReportingLine(
                    subordinate="Backend Developer",
                    subordinate_id="backend-1",
                    supervisor="architect",
                ),
            ),
        )
        assert len(dept.reporting_lines) == 2

    def test_head_id_accepted(self) -> None:
        """Accept optional head_id for disambiguating the department head."""
        dept = Department(
            name="eng",
            head="Backend Developer",
            head_id="backend-senior",
        )
        assert dept.head_id == "backend-senior"

    def test_head_id_defaults_to_none(self) -> None:
        """head_id defaults to None when omitted."""
        dept = Department(name="eng", head="cto")
        assert dept.head_id is None

    def test_blank_head_id_rejected(self) -> None:
        """Reject empty string head_id."""
        with pytest.raises(ValidationError, match="at least 1 character"):
            Department(name="eng", head="cto", head_id="")

    def test_whitespace_head_id_rejected(self) -> None:
        """Reject whitespace-only head_id."""
        with pytest.raises(ValidationError, match="whitespace-only"):
            Department(name="eng", head="cto", head_id="  ")

    def test_head_id_without_head_rejected(self) -> None:
        """Reject head_id when head is None."""
        with pytest.raises(ValidationError, match=r"head_id.*head is None"):
            Department(name="eng", head_id="backend-senior")
