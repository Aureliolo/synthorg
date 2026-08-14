"""Shared fixtures for the capability-source tests.

One source ships, but every mechanism around it is per-source: the refresh
loop contains a failure to the source that raised it, the enabled set is
filtered per label, each source carries its own status row, and seeding
skips only the sources that already have a history. None of that can be
exercised against a registry of one, so a synthetic second source is
registered here. It reads through a real parser rather than a stub, so a
test that passes is a statement about the shipped code path.
"""

from datetime import UTC, datetime

import pytest

from synthorg.core.types import NotBlankStr
from synthorg.providers.capability_sources import ingest as ingest_module
from synthorg.providers.capability_sources.bundle import BundledSnapshot
from synthorg.providers.capability_sources.models import CapabilityScore
from synthorg.providers.capability_sources.registry import (
    EPOCH_LABEL,
    CapabilitySourceSpec,
    list_capability_sources,
)

#: Label of the synthetic source. Deliberately not a real leaderboard: a
#: test that named one would read as a claim that we ship it.
SECOND_LABEL = "test-source"

SECOND_SPEC = CapabilitySourceSpec(
    label=NotBlankStr(SECOND_LABEL),
    display_name=NotBlankStr("Test Source"),
    feed_url=NotBlankStr("https://feeds.example/test-source.csv"),
    parser_key=NotBlankStr("epoch_csv"),
    axes=("general",),
    licence_note=NotBlankStr("Test fixture; not a real feed."),
    cadence_note=NotBlankStr("Test fixture; never fetched outside a test."),
)


@pytest.fixture
def two_sources(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[CapabilitySourceSpec, ...]:
    """Register a synthetic second source for the duration of one test.

    Returns:
        Every spec visible to the ingest service, shipped ones first.
    """
    specs = (*list_capability_sources(), SECOND_SPEC)
    by_label = {str(spec.label): spec for spec in specs}
    monkeypatch.setattr(ingest_module, "list_capability_sources", lambda: specs)
    monkeypatch.setattr(ingest_module, "get_capability_source", by_label.get)
    return specs


def _bundled_row(label: str, *, captured: datetime) -> CapabilityScore:
    """Build one snapshot row for *label*.

    Returns:
        A row dated a day before the capture, so it ages like a real one.
    """
    measured = captured.replace(day=captured.day - 1)
    return CapabilityScore(
        source_label=NotBlankStr(label),
        model_identifier=NotBlankStr("model-y"),
        axis="general",
        score=80.0,
        as_of=measured,
        ingested_at=captured,
    )


@pytest.fixture
def bundled_two_sources(monkeypatch: pytest.MonkeyPatch) -> tuple[str, str]:
    """Seed the bundle loader with a snapshot covering two sources.

    Seeding skips only the sources that already have a history, so telling
    "skipped" from "seeded nothing at all" needs a second source present.

    Returns:
        The two labels the substituted snapshot covers.
    """
    captured = datetime(2026, 8, 13, tzinfo=UTC)
    labels = (EPOCH_LABEL, SECOND_LABEL)
    snapshot = BundledSnapshot(
        captured_at=captured,
        scores={label: (_bundled_row(label, captured=captured),) for label in labels},
    )
    monkeypatch.setattr(
        ingest_module, "load_bundled_snapshot", lambda **_kwargs: snapshot
    )
    return labels
