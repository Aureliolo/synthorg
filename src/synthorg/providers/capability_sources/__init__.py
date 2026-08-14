"""Externally-sourced evidence for the model capability ladder.

The heuristic classifier grades a model on proxies (parameter count, a
price band, a vendor's own usage tier), and a proxy is what let an older,
larger, dearer model outrank a newer one that benchmarked above it. This
package is the layer that corrects that: published measurements, ingested
with their provenance, sitting between the operator's override and the
heuristic in the precedence chain.

Sources are independent of one another. One source failing to fetch or
parse degrades to whatever the others still have, never to the heuristic,
and never touches evidence already ingested from the failed source: a
refresh that fails leaves the last good rows in place, ageing visibly,
rather than deleting what it could not replace.
"""

from synthorg.providers.capability_sources.models import (
    CAPABILITY_AXES,
    SCORE_MAX,
    SCORE_MIN,
    CapabilityAxis,
    CapabilityScore,
    CapabilityScoreKey,
)

__all__ = [
    "CAPABILITY_AXES",
    "SCORE_MAX",
    "SCORE_MIN",
    "CapabilityAxis",
    "CapabilityScore",
    "CapabilityScoreKey",
]
