# module-kind: declarative
"""Declarative registry of the capability-evidence sources SynthOrg ships.

Modelled on :mod:`synthorg.providers.presets`, which is the sanctioned
place a declarative registry may name third parties. Every entry records
what a reviewer and an operator both need before trusting a number: where
it comes from, what licence permits us to read it, how often it moves, and
what it measures.

A source qualifies on five bars: it MEASURES rather than restates, its
ground truth is objective, it publishes a stable machine-readable feed,
its licence permits both programmatic reading and the redistribution a
bundled snapshot amounts to, and its model identifiers resolve to a
configured model without guessing.

**Epoch AI** clears all five, but only in part, and the parser reads only
that part. Its hub is a blend: some benchmarks Epoch runs itself under one
documented harness with consistent settings, others are another
leaderboard's numbers, and others again are what a vendor reported about
its own model. Only the first is admitted, by an exact match on the feed's
own ``source`` column. The bar is about who produced a number, so it has
to be applied per ROW; treating the hub as a single source would have let
a vendor's self-assessment grade its own model, which is the proxy this
layer exists to replace.

The first bar excludes an entire popular category, so it is worth being
explicit about why. A head-to-head **preference** board measures which of
two replies a reader liked, with no test executed and no task completed.
That is a real measurement of a real thing, and the thing is not
capability: preference tracks presentation (length and formatting most of
all), and it rewards agreeableness, which is the trait that makes an agent
least safe to leave running. This product routes work to agents, so a
board of votes grades the wrong property however many votes it holds.

Also excluded: sources whose display names cannot be resolved to a
provider's model id without guessing (a matcher that guesses is how a
wrong grade gets in); sources whose terms permit reading but not
redistribution; sources that stamp every row with one publication date,
because a row that cannot age is a row the recency cut cannot retire; and
sources whose published results feed has stopped moving, however actively
developed the harness behind it still is. That last one is worth checking
rather than assuming, because a benchmark can keep releasing questions
long after it stopped publishing machine-readable answers.
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

_SPECS: Final[tuple[CapabilitySourceSpec, ...]] = (
    CapabilitySourceSpec(
        label=NotBlankStr(EPOCH_LABEL),
        display_name=NotBlankStr("Epoch AI Benchmarking Hub"),
        feed_url=NotBlankStr("https://epoch.ai/data/eci_benchmarks.csv"),
        parser_key=NotBlankStr("epoch_csv"),
        axes=("coding", "reasoning", "general"),
        licence_note=NotBlankStr(
            "Creative Commons Attribution: free to use, distribute and "
            "reproduce with credit. Only rows Epoch evaluated itself are "
            "read, so the externally-sourced slices under other licences "
            "are not ingested or redistributed."
        ),
        attribution="Benchmark data by Epoch AI, CC BY.",
        cadence_note=NotBlankStr(
            "Actively maintained, refreshed on no fixed schedule. The feed "
            "dates no individual measurement, so evidence age is counted "
            "from when this installation last read it."
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
    "CapabilitySourceSpec",
    "get_capability_source",
    "list_capability_sources",
]
