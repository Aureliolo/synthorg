# module-kind: code
"""Match a source's model identifier to the models an operator configured.

The same model is written several ways by the people who publish about it:
``vendor/model-y``, ``model-y`` and ``provider-a/model-y`` are one model to a
reader and three strings to a program. Matching them is where a wrong grade
gets in, so this layer is deliberately narrow: it compares identifiers
exactly, and once more with a leading routing prefix removed from both
sides, and stops there.

What it will not do is guess. No edit distance, no substring containment,
and in particular no stripping of an effort or variant suffix: an
identifier ending in a variant marker names a *different configuration* of
the model, measured separately and often scoring differently, so folding it
into the base name would average two things an operator chose between.

Capability belongs to the model rather than to the connection, so one
source row legitimately grades the same model on every provider serving it.
An identifier that matches nothing simply grades nothing, and the count of
what went unmatched is reported so a bad mapping is visible rather than
silent.
"""

from collections.abc import Iterable, Mapping
from types import MappingProxyType

from pydantic import BaseModel, ConfigDict, Field

#: Separator between a routing prefix and the model id in a namespaced
#: identifier. A model id may legitimately contain further separators, so
#: only the first segment is ever treated as a prefix.
_PREFIX_SEPARATOR = "/"


class MatchReport(BaseModel):
    """What one matching pass resolved, and what it could not.

    Attributes:
        matched_identifiers: How many distinct source identifiers found at
            least one configured model.
        unmatched_identifiers: How many found none. Large next to the
            matched count, this is the signal that a source names models
            in a way this installation does not.
        matched_models: How many configured ``(provider, model_id)`` pairs
            came away with evidence.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    matched_identifiers: int = Field(ge=0, description="Source ids that resolved")
    unmatched_identifiers: int = Field(ge=0, description="Source ids that did not")
    matched_models: int = Field(ge=0, description="Configured models with evidence")


def normalise_identifier(raw: str) -> str:
    """Reduce an identifier to the form comparisons are made in.

    Returns:
        The identifier trimmed and case-folded. Case is dropped because
        publishers disagree about it for the same model; nothing else is
        removed, so two genuinely different ids stay different.
    """
    return raw.strip().casefold()


def strip_routing_prefix(identifier: str) -> str:
    """Remove a leading ``prefix/`` routing segment, when present.

    Returns:
        The identifier after its first segment, or unchanged when it has
        no separator or nothing would remain.
    """
    head, separator, tail = identifier.partition(_PREFIX_SEPARATOR)
    if not separator or not tail or not head:
        return identifier
    return tail


class ConfiguredModelIndex:
    """Looks up configured ``(provider, model_id)`` pairs by identifier.

    Args:
        models: The configured pairs to index.
    """

    __slots__ = ("_by_exact", "_by_stripped")

    def __init__(self, models: Iterable[tuple[str, str]]) -> None:
        by_exact: dict[str, list[tuple[str, str]]] = {}
        by_stripped: dict[str, list[tuple[str, str]]] = {}
        for provider, model_id in models:
            exact = normalise_identifier(model_id)
            by_exact.setdefault(exact, []).append((provider, model_id))
            stripped = strip_routing_prefix(exact)
            if stripped != exact:
                by_stripped.setdefault(stripped, []).append((provider, model_id))
            else:
                by_stripped.setdefault(exact, []).append((provider, model_id))
        self._by_exact: Mapping[str, tuple[tuple[str, str], ...]] = MappingProxyType(
            {k: tuple(v) for k, v in by_exact.items()},
        )
        self._by_stripped: Mapping[str, tuple[tuple[str, str], ...]] = MappingProxyType(
            {k: tuple(v) for k, v in by_stripped.items()},
        )

    def lookup(self, source_identifier: str) -> tuple[tuple[str, str], ...]:
        """Return every configured model *source_identifier* names.

        An exact match is preferred, so a configured id that matches one
        source row verbatim is never diverted to a prefix-stripped
        neighbour.

        Returns:
            The matching pairs, empty when the identifier names nothing
            configured.
        """
        normalised = normalise_identifier(source_identifier)
        exact = self._by_exact.get(normalised)
        if exact:
            return exact
        stripped = strip_routing_prefix(normalised)
        return self._by_stripped.get(stripped, ())


def match_identifiers(
    index: ConfiguredModelIndex,
    identifiers: Iterable[str],
) -> tuple[dict[str, tuple[tuple[str, str], ...]], MatchReport]:
    """Resolve every source identifier against the configured models.

    Returns:
        The per-identifier matches (absent when an identifier matched
        nothing) and a report of the pass, for the dashboard to show.
    """
    resolved: dict[str, tuple[tuple[str, str], ...]] = {}
    unmatched = 0
    covered: set[tuple[str, str]] = set()
    for identifier in identifiers:
        matches = index.lookup(identifier)
        if not matches:
            unmatched += 1
            continue
        resolved[identifier] = matches
        covered.update(matches)
    return resolved, MatchReport(
        matched_identifiers=len(resolved),
        unmatched_identifiers=unmatched,
        matched_models=len(covered),
    )


__all__ = [
    "ConfiguredModelIndex",
    "MatchReport",
    "match_identifiers",
    "normalise_identifier",
    "strip_routing_prefix",
]
