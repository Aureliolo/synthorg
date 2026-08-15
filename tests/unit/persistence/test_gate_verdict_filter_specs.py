"""Keyset-cursor validation on the two gate-verdict filter specs.

Both archives page on ``(recorded_at, report_id)``, and a spec carrying one
half of that pair is not a narrower cursor: it is a plain timestamp filter
that silently drops every row sharing the boundary instant, which is exactly
the case the surrogate key exists to keep apart. The refusal is a model
invariant, so it is asserted here rather than against a backend.
"""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from synthorg.persistence.completion_oracle_report_protocol import (
    CompletionOracleReportFilterSpec,
)
from synthorg.persistence.red_team_report_protocol import RedTeamReportFilterSpec

pytestmark = pytest.mark.unit

_SPECS = (CompletionOracleReportFilterSpec, RedTeamReportFilterSpec)


@pytest.mark.parametrize("spec", _SPECS)
def test_a_timestamp_without_its_archive_key_is_refused(
    spec: type[CompletionOracleReportFilterSpec] | type[RedTeamReportFilterSpec],
) -> None:
    with pytest.raises(ValidationError):
        spec(after_recorded_at=datetime.now(UTC))


@pytest.mark.parametrize("spec", _SPECS)
def test_an_archive_key_without_its_timestamp_is_refused(
    spec: type[CompletionOracleReportFilterSpec] | type[RedTeamReportFilterSpec],
) -> None:
    with pytest.raises(ValidationError):
        spec(after_report_id=7)


@pytest.mark.parametrize("spec", _SPECS)
def test_both_halves_together_are_a_position(
    spec: type[CompletionOracleReportFilterSpec] | type[RedTeamReportFilterSpec],
) -> None:
    boundary = datetime.now(UTC)
    cursor = spec(after_recorded_at=boundary, after_report_id=7)
    assert cursor.after_recorded_at == boundary
    assert cursor.after_report_id == 7


@pytest.mark.parametrize("spec", _SPECS)
def test_neither_half_is_the_first_page(
    spec: type[CompletionOracleReportFilterSpec] | type[RedTeamReportFilterSpec],
) -> None:
    first = spec()
    assert first.after_recorded_at is None
    assert first.after_report_id is None
