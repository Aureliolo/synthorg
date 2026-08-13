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
* **LMArena** scores head-to-head preference across a very large vote
  volume, which measures something no fixed question set can: whether a
  model's answers hold up on work people actually bring it. It is the
  complement to Epoch rather than a second opinion on the same thing, so
  the two disagreeing is informative.

The two are read together and never averaged: where they disagree the
lower rung wins, because the cost of over-grading a model is work routed
to something that cannot do it.

Deliberately not shipped: sources whose display names cannot be resolved
to a provider's model id without guessing (a matcher that guesses is how a
wrong grade gets in); sources whose terms permit reading but not the
redistribution that shipping a parser plus a default-on fetch amounts to;
and sources whose published results feed has stopped moving, however
actively developed the harness behind it still is. That last one is worth
checking rather than assuming, because a benchmark can keep releasing
questions long after it stopped publishing machine-readable answers.
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
LMARENA_LABEL: Final[str] = "lmarena"

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
        label=NotBlankStr(LMARENA_LABEL),
        display_name=NotBlankStr("LMArena Leaderboard"),
        feed_url=NotBlankStr(
            "https://huggingface.co/datasets/lmarena-ai/leaderboard-dataset/"
            "resolve/main/text/latest-00000-of-00001.parquet"
        ),
        parser_key=NotBlankStr("lmarena_parquet"),
        axes=("coding", "reasoning", "general"),
        licence_note=NotBlankStr(
            "Creative Commons Attribution 4.0: free to use, share and "
            "adapt with credit. The published dataset is ungated. Only "
            "the current-board snapshot is read; the sibling history "
            "file is not, so a stale publication date cannot win."
        ),
        attribution="Leaderboard data by LMArena, CC BY 4.0.",
        cadence_note=NotBlankStr(
            "Republished daily, so a publication date more than a few "
            "days old means the fetch is failing rather than the board "
            "standing still."
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
    "LMARENA_LABEL",
    "CapabilitySourceSpec",
    "get_capability_source",
    "list_capability_sources",
]
