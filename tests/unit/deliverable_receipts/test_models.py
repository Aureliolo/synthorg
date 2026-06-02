"""Unit tests for deliverable-receipt model invariants."""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from synthorg.deliverable_receipts.models import (
    DeliverableReceipt,
    ReceiptCassetteRef,
    ReceiptRedTeamEntry,
    ReceiptSourceEntry,
    ReceiptTestEntry,
)
from synthorg.security.redteam.models import (
    RedTeamAttackSurface,
    RedTeamFinding,
    RedTeamSeverity,
    RedTeamVerdict,
)

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 6, 2, 12, 0, tzinfo=UTC)


def _finding(severity: RedTeamSeverity) -> RedTeamFinding:
    """Build a finding; HIGH+ severities carry required evidence."""
    evidence = (
        ("quoted defect",)
        if severity
        in (
            RedTeamSeverity.HIGH,
            RedTeamSeverity.CRITICAL,
        )
        else ()
    )
    return RedTeamFinding(
        attack_surface=RedTeamAttackSurface.CORRECTNESS,
        severity=severity,
        description="a defect",
        evidence=evidence,
    )


def _minimal_receipt(**overrides: object) -> DeliverableReceipt:
    data: dict[str, object] = {
        "receipt_id": "r-1",
        "task_id": "t-1",
        "project_id": "p-1",
        "execution_id": "e-1",
        "deliverable_doc_slug": "the-deliverable",
        "issued_at": _NOW,
        "total_cost": 0.0,
        "currency": "EUR",
    }
    data.update(overrides)
    return DeliverableReceipt.model_validate(data)


class TestReceiptTestEntry:
    def test_passing_run_with_clean_exit_is_valid(self) -> None:
        entry = ReceiptTestEntry(
            record_id="rec-1",
            command="python -m pytest",
            returncode=0,
            passed=True,
            timed_out=False,
            executed_at=_NOW,
        )
        assert entry.passed is True

    def test_passing_run_with_nonzero_exit_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ReceiptTestEntry(
                record_id="rec-1",
                command="python -m pytest",
                returncode=1,
                passed=True,
                timed_out=False,
                executed_at=_NOW,
            )

    def test_passing_run_that_timed_out_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ReceiptTestEntry(
                record_id="rec-1",
                command="python -m pytest",
                returncode=0,
                passed=True,
                timed_out=True,
                executed_at=_NOW,
            )

    def test_failing_run_is_valid(self) -> None:
        entry = ReceiptTestEntry(
            record_id="rec-1",
            command="python -m pytest",
            returncode=1,
            passed=False,
            timed_out=False,
            executed_at=_NOW,
        )
        assert entry.passed is False


class TestReceiptRedTeamEntry:
    def test_consistent_counts_valid(self) -> None:
        entry = ReceiptRedTeamEntry(
            verdict=RedTeamVerdict.PASS_WITH_FINDINGS,
            finding_count=2,
            high_plus_count=1,
            summary="one high, one low",
            findings_snapshot=(
                _finding(RedTeamSeverity.HIGH),
                _finding(RedTeamSeverity.LOW),
            ),
        )
        assert entry.high_plus_count == 1

    def test_finding_count_mismatch_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ReceiptRedTeamEntry(
                verdict=RedTeamVerdict.PASS,
                finding_count=5,
                high_plus_count=0,
                summary="mismatch",
                findings_snapshot=(_finding(RedTeamSeverity.LOW),),
            )

    def test_high_plus_count_mismatch_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ReceiptRedTeamEntry(
                verdict=RedTeamVerdict.PASS_WITH_FINDINGS,
                finding_count=1,
                high_plus_count=1,
                summary="low only but claims a high",
                findings_snapshot=(_finding(RedTeamSeverity.LOW),),
            )


class TestReceiptCassetteRef:
    def test_blank_content_hash_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ReceiptCassetteRef(path="cassettes/run.json", content_hash="")


class TestDeliverableReceipt:
    def test_minimal_receipt_valid(self) -> None:
        receipt = _minimal_receipt()
        assert receipt.sources == ()
        assert receipt.red_team is None
        assert receipt.cassette is None

    def test_negative_cost_rejected(self) -> None:
        with pytest.raises(ValidationError):
            _minimal_receipt(total_cost=-1.0)

    def test_non_finite_cost_rejected(self) -> None:
        with pytest.raises(ValidationError):
            _minimal_receipt(total_cost=float("inf"))

    def test_unknown_currency_rejected(self) -> None:
        with pytest.raises(ValidationError):
            _minimal_receipt(currency="ZZZ")

    def test_extra_field_rejected(self) -> None:
        with pytest.raises(ValidationError):
            _minimal_receipt(unexpected="nope")

    def test_populated_receipt_round_trips_json(self) -> None:
        receipt = _minimal_receipt(
            total_cost=1.25,
            sources=(
                ReceiptSourceEntry(
                    source_id="s-1",
                    chunk_id="c-1",
                    title="Spec",
                    uri="file:///spec.pdf",
                    content_hash="abc123",
                ),
            ),
            cassette=ReceiptCassetteRef(
                path="cassettes/run.json",
                content_hash="deadbeef",
            ),
        )
        restored = DeliverableReceipt.model_validate_json(receipt.model_dump_json())
        assert restored == receipt
