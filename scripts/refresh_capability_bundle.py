#!/usr/bin/env python
"""Regenerate the capability snapshot that ships with the release.

Fetches every declared source, parses it through the shipped parsers, and
writes the compact snapshot the runtime seeds from when it has never
fetched a source itself. Run monthly by
``.github/workflows/maint-capability-bundle.yml``; run by hand with
``uv run python scripts/refresh_capability_bundle.py``.

Fails rather than writing a partial snapshot. A bundle missing one source
would ship an installation that silently grades on half the evidence, and
the whole point of shipping one is that an offline installation gets the
same grading as a connected one. ``--allow-partial`` exists for the case
where a source is known to be down and shipping the other is genuinely
better than shipping nothing; it says so on stderr and in the file.
"""

import argparse
import asyncio
import json
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

import httpx

from synthorg.providers.capability_sources.bundle import BUNDLE_FILENAME
from synthorg.providers.capability_sources.errors import CapabilitySourceParseError
from synthorg.providers.capability_sources.models import CapabilityScore
from synthorg.providers.capability_sources.parsers import parse_document
from synthorg.providers.capability_sources.registry import (
    CapabilitySourceSpec,
    list_capability_sources,
)

_PACKAGE_DIR: Final[Path] = (
    Path(__file__).resolve().parent.parent
    / "src"
    / "synthorg"
    / "providers"
    / "capability_sources"
)

_FETCH_TIMEOUT_SECONDS: Final[float] = 180.0

#: A source contributing fewer measurements than this has not been read
#: properly, whatever its HTTP status said. Both shipped feeds produce
#: hundreds; a handful means a shape change the parser absorbed by
#: skipping nearly every row.
_MIN_SCORES_PER_SOURCE: Final[int] = 50


async def _fetch(client: httpx.AsyncClient, url: str) -> bytes:
    """Fetch one feed.

    Returns:
        The response body.
    """
    response = await client.get(url)
    response.raise_for_status()
    return response.content


def _rows(scores: Sequence[CapabilityScore]) -> list[list[str | float]]:
    """Convert scores into the snapshot's compact row form.

    Returns:
        One ``[model_identifier, axis, score, as_of]`` row per score,
        sorted so a regeneration that changes nothing produces no diff.
    """
    return sorted(
        (
            [
                str(s.model_identifier),
                str(s.axis),
                round(s.score, 4),
                s.as_of.isoformat(),
            ]
            for s in scores
        ),
        key=lambda row: (str(row[0]), str(row[1])),
    )


async def _collect(
    spec: CapabilitySourceSpec,
    client: httpx.AsyncClient,
    now: datetime,
) -> tuple[str, list[list[str | float]], str]:
    """Fetch and parse one source.

    Returns:
        Its label, its rows, and a failure reason (empty when it worked).
    """
    label = str(spec.label)
    try:
        document = await _fetch(client, str(spec.feed_url))
    except (httpx.HTTPError, OSError) as exc:
        return label, [], f"fetch failed: {type(exc).__name__}: {exc}"
    try:
        parsed = parse_document(
            str(spec.parser_key), document, source_label=label, ingested_at=now
        )
    except CapabilitySourceParseError as exc:
        return label, [], f"parse failed: {exc}"
    if len(parsed.scores) < _MIN_SCORES_PER_SOURCE:
        return (
            label,
            [],
            (
                f"produced only {len(parsed.scores)} measurements from "
                f"{parsed.rows_read} rows, below the {_MIN_SCORES_PER_SOURCE} "
                f"floor; the feed's shape has probably changed"
            ),
        )
    return label, _rows(parsed.scores), ""


async def _build(now: datetime) -> tuple[dict[str, list[list[str | float]]], list[str]]:
    """Fetch every declared source.

    Returns:
        The per-source rows and the failure reasons collected.
    """
    sources: dict[str, list[list[str | float]]] = {}
    failures: list[str] = []
    async with httpx.AsyncClient(
        timeout=_FETCH_TIMEOUT_SECONDS, follow_redirects=True
    ) as client:
        for spec in list_capability_sources():
            label, rows, reason = await _collect(spec, client, now)
            if reason:
                failures.append(f"{label}: {reason}")
                continue
            sources[label] = rows
            print(f"{label}: {len(rows)} measurements")
    return sources, failures


def _write(
    path: Path,
    *,
    captured_at: datetime,
    sources: dict[str, list[list[str | float]]],
    partial: bool,
) -> None:
    """Write the snapshot to *path*."""
    document = {
        "captured_at": captured_at.isoformat(),
        "partial": partial,
        "sources": {label: sources[label] for label in sorted(sources)},
    }
    path.write_text(
        json.dumps(document, indent=1, sort_keys=False) + "\n",
        encoding="utf-8",
    )


def main(argv: Sequence[str] | None = None) -> int:
    """Regenerate the snapshot.

    Returns:
        0 on success, 1 when a source failed and partial output was not
        requested.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--allow-partial",
        action="store_true",
        help="write a snapshot even when a source could not be read",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=_PACKAGE_DIR / BUNDLE_FILENAME,
        help="where to write the snapshot",
    )
    args = parser.parse_args(argv)

    now = datetime.now(tz=UTC)
    sources, failures = asyncio.run(_build(now))

    for failure in failures:
        print(f"FAILED {failure}", file=sys.stderr)
    if failures and not args.allow_partial:
        print(
            "Refusing to write a partial snapshot. Re-run when the source "
            "recovers, or pass --allow-partial to ship what did work.",
            file=sys.stderr,
        )
        return 1
    if not sources:
        print("No source produced any measurements.", file=sys.stderr)
        return 1

    _write(
        args.output,
        captured_at=now,
        sources=sources,
        partial=bool(failures),
    )
    total = sum(len(rows) for rows in sources.values())
    print(
        f"Wrote {args.output} ({total} measurements across {len(sources)} source(s))."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
