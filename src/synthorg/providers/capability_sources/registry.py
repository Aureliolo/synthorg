# module-kind: declarative
"""Declarative registry of the capability-evidence sources SynthOrg ships.

Modelled on :mod:`synthorg.providers.presets`, which is the sanctioned
place a declarative registry may name third parties. Every entry records
what a reviewer and an operator both need before trusting a number: where
it comes from, what licence permits us to read it, how often it moves, and
what it measures.

Two sources ship, and each was chosen against the same four bars: a stable
machine-readable feed, a licence permitting programmatic use, model
identifiers that resolve without guessing, and a live update cadence.

* **Epoch AI** runs its own evaluations under one documented harness with
  consistent settings across models, rather than restating what each
  vendor reported about itself. That is the property that matters here:
  the defect this layer corrects was a grading that trusted a proxy, so a
  source which measures beats one which repeats.
* **LiveBench** publishes fresh questions monthly and grades against
  objective ground truth with no LLM judge, which makes contamination a
  bounded rather than unbounded worry.

Deliberately not shipped: sources whose display names cannot be resolved
to a provider's model id without guessing (a matcher that guesses is how a
wrong grade gets in), and sources whose terms permit reading but not the
redistribution that shipping a parser plus a default-on fetch amounts to.
"""

from collections.abc import Mapping
from types import MappingProxyType
from typing import Final

from pydantic import BaseModel, ConfigDict, Field

from synthorg.core.types import NotBlankStr
from synthorg.providers.capability_sources.models import CapabilityAxis


class CapabilitySourceSpec(BaseModel):
    """One declared source of published capability evidence.

    Attributes:
        label: Stable registry key. Persisted on every score row, so it
            is part of the data contract and does not change once shipped.
        display_name: What the dashboard calls it.
        feed_url: Where the shipped parser reads from. An operator may
            point a source at a different URL, which is validated against
            the SSRF allowlist before it is fetched.
        parser_key: Which shipped parser reads this feed's shape.
        axes: What this source can measure. A source publishing only a
            composite index declares ``general`` alone.
        licence_note: The terms under which we read it, stated rather
            than assumed. A source whose licence is unclear does not
            belong in this registry at all.
        attribution: Credit the licence requires us to display. Empty
            when the licence requires none.
        cadence_note: How often the feed moves, so an operator can judge
            whether an old ``as_of`` means a stale feed or a stable one.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    label: NotBlankStr = Field(description="Stable registry key")
    display_name: NotBlankStr = Field(description="Name shown in the dashboard")
    feed_url: NotBlankStr = Field(description="Default feed location")
    parser_key: NotBlankStr = Field(description="Which shipped parser reads it")
    axes: tuple[CapabilityAxis, ...] = Field(
        min_length=1,
        description="Axes this source can measure",
    )
    licence_note: NotBlankStr = Field(description="Terms under which we read it")
    attribution: str = Field(
        default="",
        description="Credit the licence requires (empty when none)",
    )
    cadence_note: NotBlankStr = Field(description="How often the feed moves")


EPOCH_LABEL: Final[str] = "epoch-ai"
LIVEBENCH_LABEL: Final[str] = "livebench"

_SPECS: Final[tuple[CapabilitySourceSpec, ...]] = (
    CapabilitySourceSpec(
        label=NotBlankStr(EPOCH_LABEL),
        display_name=NotBlankStr("Epoch AI Benchmarking Hub"),
        feed_url=NotBlankStr("https://epoch.ai/data/eci_benchmarks.csv"),
        parser_key=NotBlankStr("epoch_csv"),
        axes=("coding", "reasoning", "general"),
        licence_note=NotBlankStr(
            "Creative Commons Attribution: free to use, distribute and "
            "reproduce with credit. Some rows are sourced from external "
            "leaderboards that keep their own (Apache-2.0) licences."
        ),
        attribution="Benchmark data by Epoch AI, CC BY.",
        cadence_note=NotBlankStr(
            "Actively maintained; the published dataset is refreshed on "
            "no fixed schedule, so judge freshness from each row's as_of "
            "rather than from the fetch time."
        ),
    ),
    CapabilitySourceSpec(
        label=NotBlankStr(LIVEBENCH_LABEL),
        display_name=NotBlankStr("LiveBench"),
        feed_url=NotBlankStr(
            "https://raw.githubusercontent.com/LiveBench/LiveBench/main/"
            "leaderboard.json"
        ),
        parser_key=NotBlankStr("livebench_json"),
        axes=("coding", "reasoning", "general"),
        licence_note=NotBlankStr(
            "Open-source benchmark published on GitHub under its "
            "repository licence; results are released alongside the "
            "questions that produced them."
        ),
        attribution="Benchmark data by LiveBench.",
        cadence_note=NotBlankStr(
            "Fresh questions released monthly to bound contamination, so "
            "a score older than a couple of months was measured against "
            "a question set that has since moved on."
        ),
    ),
)

_BY_LABEL: Final[Mapping[str, CapabilitySourceSpec]] = MappingProxyType(
    {spec.label: spec for spec in _SPECS},
)


def list_capability_sources() -> tuple[CapabilitySourceSpec, ...]:
    """Return every declared source, in registry order.

    Returns:
        The shipped source specifications.
    """
    return _SPECS


def get_capability_source(label: str) -> CapabilitySourceSpec | None:
    """Return the spec for *label*, or ``None`` when it is not declared.

    Returns ``None`` rather than guessing at a near-miss label, following
    :mod:`synthorg.integrations.connections.http_vendor`: a source
    resolved by approximation would file evidence under a name the
    operator cannot find it by.

    Returns:
        The matching spec, or ``None``.
    """
    return _BY_LABEL.get(label)


__all__ = [
    "EPOCH_LABEL",
    "LIVEBENCH_LABEL",
    "CapabilitySourceSpec",
    "get_capability_source",
    "list_capability_sources",
]
