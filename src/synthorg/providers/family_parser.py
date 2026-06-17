"""Model family/generation parsing.

Parses a stable *family* label and a sortable *generation* number from a
model id so the matcher can resolve a family/pattern reference to the
newest matching configured model.  The parser engine and value types
here are vendor-free; the per-provider capturing rules (which contain
real family names) live in :mod:`synthorg.providers.presets`, the only
location the vendor-name policy permits them.

The default :class:`RegexFamilyParser` consults a per-provider rule table
and falls back to a generic heuristic for providers without a rule.  It
is a pluggable seam: :func:`get_family_parser` returns the default
singleton, and any object satisfying :class:`FamilyParser` can replace it.
"""

import re
from collections.abc import Mapping
from datetime import date
from types import MappingProxyType
from typing import Final, NamedTuple, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from synthorg.core.types import NotBlankStr

_DATE_TOKEN_LEN: Final[int] = 8


class ParsedModelIdentity(BaseModel):
    """Family/generation/recency parsed from a model id.

    Attributes:
        family: Stable family label (e.g. ``"example-large"``); ``None``
            when nothing parseable was found.
        generation: Sortable generation number (higher is newer); ``None``
            when no version token was found.
        release_date: Release date when derivable from a dated id suffix.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    family: NotBlankStr | None = Field(default=None)
    generation: float | None = Field(default=None, ge=0.0)
    release_date: date | None = Field(default=None)


class FamilyRule(NamedTuple):
    """A single capturing rule for one provider.

    Rules are applied to the model id *after* a trailing ``-YYYYMMDD``
    date suffix has been stripped, so patterns never need to account for
    dates.

    Attributes:
        capture: Compiled pattern with named groups ``gen`` (the version
            token) plus any of ``family`` / a variant group referenced by
            ``family_template``.
        family_template: ``str.format_map`` template composing the family
            label from the match's named groups (missing groups render as
            the empty string).
    """

    capture: re.Pattern[str]
    family_template: str


@runtime_checkable
class FamilyParser(Protocol):
    """Parses a model id into a :class:`ParsedModelIdentity`."""

    def parse(
        self,
        model_id: str,
        *,
        litellm_provider: str | None,
    ) -> ParsedModelIdentity:
        """Parse *model_id* (provider hint guides rule selection)."""
        ...


# Generic-fallback patterns (vendor-free).
_DATE_SUFFIX_RE = re.compile(r"-(\d{8})$")
_GENERIC_RE = re.compile(r"^(?P<family>[a-z][a-z._-]*?)-?(?P<gen>\d+(?:[.-]\d+)?)")


def _parse_generation(raw: str | None) -> float | None:
    """Convert a captured version token to a sortable float.

    Returns:
        ``4.5`` for ``"4-5"`` / ``"4.5"``, ``3.0`` for ``"3"``; ``None``
        when *raw* is empty or not a single number.
    """
    if not raw:
        return None
    try:
        return float(raw.replace("-", "."))
    except ValueError:
        return None


def _parse_release_date(raw: str | None) -> date | None:
    """Parse a ``YYYYMMDD`` token to a :class:`date`.

    Returns:
        The parsed date, or ``None`` when *raw* is empty or malformed.
    """
    if not raw or len(raw) != _DATE_TOKEN_LEN:
        return None
    try:
        return date(int(raw[0:4]), int(raw[4:6]), int(raw[6:8]))
    except ValueError:
        return None


def _normalize_family(value: str) -> str | None:
    """Lowercase, collapse separators, and strip a composed family label.

    Returns:
        The cleaned family, or ``None`` when nothing remains.
    """
    cleaned = re.sub(r"[-_]{2,}", "-", value.strip().lower()).strip("-_")
    return cleaned or None


class RegexFamilyParser:
    """Default :class:`FamilyParser` backed by a per-provider rule table.

    Tries the provider's rules in order; the first match wins.  Falls
    back to a generic heuristic (leading alpha stem as family, first
    numeric token as generation, trailing ``-YYYYMMDD`` as release date)
    when no provider rule applies.
    """

    def __init__(self, rules: Mapping[str, tuple[FamilyRule, ...]]) -> None:
        """Store an immutable view of the per-provider rule table."""
        self._rules: Mapping[str, tuple[FamilyRule, ...]] = MappingProxyType(
            dict(rules),
        )

    def parse(
        self,
        model_id: str,
        *,
        litellm_provider: str | None,
    ) -> ParsedModelIdentity:
        """Parse *model_id*, preferring *litellm_provider*'s rules.

        A trailing ``-YYYYMMDD`` date is stripped before rules apply and
        carried through as ``release_date``.

        Returns:
            The parsed identity; falls back to the generic heuristic when
            no provider rule matches.
        """
        date_match = _DATE_SUFFIX_RE.search(model_id)
        release_date = _parse_release_date(date_match.group(1)) if date_match else None
        stripped = _DATE_SUFFIX_RE.sub("", model_id)

        for rule in self._rules.get(litellm_provider or "", ()):
            match = rule.capture.match(stripped)
            if match is None:
                continue
            groups = {k: (v or "") for k, v in match.groupdict().items()}
            return ParsedModelIdentity(
                family=_normalize_family(rule.family_template.format_map(groups)),
                generation=_parse_generation(match.groupdict().get("gen")),
                release_date=release_date,
            )
        return self._parse_generic(stripped, release_date)

    def _parse_generic(
        self,
        stripped_id: str,
        release_date: date | None,
    ) -> ParsedModelIdentity:
        """Heuristic parse for ids without a matching provider rule.

        Args:
            stripped_id: The model id with any trailing date removed.
            release_date: The date already parsed from the stripped suffix.

        Returns:
            A best-effort identity; ``family`` is the leading alpha stem
            (or the whole stem when no version token is present), and is
            ``None`` only when nothing alphabetic leads the id.
        """
        # Slash-qualified ids (e.g. a routed ``provider/model-4.1``) must
        # parse from the terminal model token, not the provider prefix, so
        # family/generation derive from the real model rather than degrading.
        stem = stripped_id.lower().rsplit("/", maxsplit=1)[-1]
        match = _GENERIC_RE.match(stem)
        if match is not None:
            return ParsedModelIdentity(
                family=_normalize_family(match.group("family")),
                generation=_parse_generation(match.group("gen")),
                release_date=release_date,
            )
        # No version token: family is the leading alpha stem, if any.
        alpha = re.match(r"^[a-z][a-z._-]*", stem)
        return ParsedModelIdentity(
            family=_normalize_family(alpha.group(0)) if alpha else None,
            generation=None,
            release_date=release_date,
        )


_DEFAULT_PARSER: RegexFamilyParser | None = None


def get_family_parser() -> FamilyParser:
    """Return the default family parser singleton.

    The default consults the vendor-named rule table in
    :mod:`synthorg.providers.presets` (imported lazily to avoid a heavy
    module-level dependency and an import cycle).

    Returns:
        A shared :class:`RegexFamilyParser` over the preset rule table.
    """
    global _DEFAULT_PARSER  # noqa: PLW0603
    if _DEFAULT_PARSER is None:
        from synthorg.providers.presets import MODEL_FAMILY_RULES  # noqa: PLC0415

        _DEFAULT_PARSER = RegexFamilyParser(MODEL_FAMILY_RULES)
    return _DEFAULT_PARSER
