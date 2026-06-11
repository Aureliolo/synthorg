"""Generate the framework comparison Markdown page from YAML data.

Used by CI (pages.yml, pages-preview.yml) to generate:
- ``docs/reference/comparison.md`` -- Markdown comparison tables

The same YAML data (``data/competitors.yaml``) is also consumed
directly by the Astro landing page (``site/src/pages/compare.astro``).

Run ``uv run python scripts/generate_comparison.py`` before
``uv run zensical build``.
"""

import datetime as dt
import subprocess
import sys
import traceback
from pathlib import Path
from typing import TYPE_CHECKING, Required, TypedDict, cast

import yaml

if TYPE_CHECKING:
    from collections.abc import Sequence

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_FILE = REPO_ROOT / "data" / "competitors.yaml"
OUTPUT_FILE = REPO_ROOT / "docs" / "reference" / "comparison.md"
AUTO_SENTINEL = "auto"


class _Competitor(TypedDict, total=False):
    """One competitor entry from ``data/competitors.yaml``.

    ``name`` / ``slug`` / ``category`` are guaranteed present by
    :func:`_validate_competitors`; the rest are optional.
    """

    name: Required[str]
    slug: Required[str]
    category: Required[str]
    url: str
    repo: str
    license: str
    pricing: str
    self_hosted: str
    is_synthorg: bool
    features: dict[str, dict[str, str]]


class _ComparisonData(TypedDict):
    """The validated top-level structure of ``data/competitors.yaml``."""

    meta: dict[str, str]
    dimensions: list[dict[str, str]]
    categories: list[dict[str, str]]
    competitors: list[_Competitor]


# Support value display symbols
SUPPORT_ICONS = {
    "full": "\u2714",  # checkmark
    "partial": "~",
    "none": "-",
    "planned": "\u23f2",  # timer clock
}

# Pricing display labels
PRICING_LABELS = {
    "free": "Free",
    "free-restrictive": "Free (copyleft)",
    "depends": "Depends",
    "open-core": "Open-core",
    "paid": "Paid",
}

# Self-hosted display labels
SELF_HOSTED_LABELS = {
    "true": "\u2714",
    "false": "-",
    "partial": "~",
}

# Thematic groupings for splitting the table
TABLE_GROUPS = [
    {
        "title": "Organization & Coordination",
        "keys": ["org_structure", "multi_agent", "task_delegation", "human_in_loop"],
    },
    {
        "title": "Technical Capabilities",
        "keys": ["memory", "tool_use", "security_model", "workflow_types"],
    },
    {
        "title": "Operations & Tooling",
        "keys": ["budget_tracking", "observability", "web_dashboard", "cli"],
    },
    {
        "title": "Maturity",
        "keys": ["production_ready", "template_system"],
    },
]


def _load_data() -> _ComparisonData:
    """Load and validate the competitors YAML file.

    Validates top-level keys (meta, dimensions, categories, competitors),
    ``meta.last_updated``, non-empty competitors list, and required fields
    on each competitor entry (name, slug, category).

    Raises:
        FileNotFoundError: If the data file does not exist.
        ValueError: If the YAML is empty, malformed, or missing required
            keys at the top level or on individual competitor entries.
    """
    if not DATA_FILE.exists():
        msg = f"Data file not found: {DATA_FILE}"
        raise FileNotFoundError(msg)

    with DATA_FILE.open(encoding="utf-8") as f:
        data = yaml.safe_load(f)

    if data is None:
        msg = f"YAML file is empty or contains no data: {DATA_FILE}"
        raise ValueError(msg)

    required_keys = {"meta", "dimensions", "categories", "competitors"}
    missing = required_keys - set(data.keys())
    if missing:
        msg = f"Missing top-level keys in {DATA_FILE}: {missing}"
        raise ValueError(msg)

    if not data["competitors"]:
        msg = f"No competitors found in {DATA_FILE}"
        raise ValueError(msg)

    if "last_updated" not in data.get("meta", {}):
        msg = f"Missing meta.last_updated in {DATA_FILE}"
        raise ValueError(msg)

    new_meta = {
        **data["meta"],
        "last_updated": _resolve_last_updated(data["meta"]["last_updated"]),
    }
    new_data = {**data, "meta": new_meta}

    _validate_competitors(new_data["competitors"])
    _validate_dimension_keys(new_data["dimensions"])

    return cast("_ComparisonData", new_data)


def _resolve_last_updated(declared: str) -> str:
    """Return the `last_updated` value to render in the generated page.

    When the YAML field is the ``auto`` sentinel, derive an ISO date from
    the most recent commit that touched ``data/competitors.yaml`` so the
    rendered timestamp tracks the source data automatically.  Falls back
    to today's UTC date if git history is unavailable (shallow clone or
    non-git environment).  Any other declared value passes through
    unchanged so existing pinned dates keep working.
    """
    if declared != AUTO_SENTINEL:
        return declared
    try:
        result = subprocess.run(
            [
                "git",
                "log",
                "-1",
                "--format=%cs",
                "--",
                str(DATA_FILE.relative_to(REPO_ROOT)),
            ],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        )
    except (
        subprocess.CalledProcessError,
        subprocess.TimeoutExpired,
        FileNotFoundError,
    ) as exc:
        # Visible warning so a misconfigured build environment (no git on
        # PATH, shallow clone with no history for this file, slow FS) does
        # not silently render today's date and mask the real problem.
        print(
            f"WARNING: could not derive comparison-page last_updated from git"
            f" ({type(exc).__name__}); using today's UTC date.",
            file=sys.stderr,
        )
        return dt.datetime.now(dt.UTC).date().isoformat()
    derived = result.stdout.strip()
    if not derived:
        print(
            "WARNING: git returned empty stdout for last_updated; using"
            f" today's UTC date. raw stdout={result.stdout!r}"
            f" returncode={result.returncode}",
            file=sys.stderr,
        )
        return dt.datetime.now(dt.UTC).date().isoformat()
    return derived


def _validate_competitors(competitors: list[object]) -> None:
    """Validate required fields and enum values on each competitor entry.

    Takes ``list[object]`` rather than ``list[_Competitor]`` because the
    entries are still untrusted YAML at this point: the per-entry
    ``isinstance`` guard is what proves the :class:`_Competitor` shape the
    rest of the module relies on.
    """
    required_keys = {"name", "slug", "category"}
    valid_pricing = set(PRICING_LABELS)
    valid_self_hosted = set(SELF_HOSTED_LABELS)
    for i, comp in enumerate(competitors):
        if not isinstance(comp, dict):
            msg = f"Competitor at index {i} is not a mapping"
            raise TypeError(msg)
        name = comp.get("name", f"<index {i}>")
        missing = required_keys - set(comp.keys())
        if missing:
            msg = f"Competitor '{name}' is missing required keys: {missing}"
            raise ValueError(msg)
        pricing = comp.get("pricing")
        if pricing is not None and pricing not in valid_pricing:
            msg = f"Competitor '{name}' has invalid pricing: '{pricing}'"
            raise ValueError(msg)
        self_hosted = comp.get("self_hosted")
        if self_hosted is not None and self_hosted not in valid_self_hosted:
            msg = f"Competitor '{name}' has invalid self_hosted: '{self_hosted}'"
            raise ValueError(msg)


def _validate_dimension_keys(dimensions: list[dict[str, str]]) -> None:
    """Warn if TABLE_GROUPS references keys not in the loaded dimensions."""
    dim_keys = {d["key"] for d in dimensions}
    for group in TABLE_GROUPS:
        unknown = set(group["keys"]) - dim_keys
        if unknown:
            print(
                f"WARNING: TABLE_GROUPS '{group['title']}' references "
                f"unknown dimensions: {unknown}",
                file=sys.stderr,
            )


def _dimension_label(dimensions: list[dict[str, str]], key: str) -> str:
    """Get the display label for a dimension key."""
    for dim in dimensions:
        if dim["key"] == key:
            return dim["label"]
    print(
        f"WARNING: Unknown dimension key '{key}', using raw key as label",
        file=sys.stderr,
    )
    return key


def _support_icon(value: str) -> str:
    """Convert a support value to its display symbol."""
    icon = SUPPORT_ICONS.get(value)
    if icon is None:
        print(
            f"WARNING: Unknown support value '{value}', using raw value",
            file=sys.stderr,
        )
        return value
    return icon


def _category_label(categories: list[dict[str, str]], key: str) -> str:
    """Get the display label for a category key."""
    for cat in categories:
        if cat["key"] == key:
            return cat["label"]
    print(
        f"WARNING: Unknown category key '{key}', using raw key as label",
        file=sys.stderr,
    )
    return key


def _frontmatter_and_intro(last_updated: str) -> list[str]:
    """Generate the frontmatter, title, legend, and intro callout."""
    return [
        "---",
        "title: Framework Comparison",
        "description: >-",
        "  How SynthOrg compares to every notable agent orchestration",
        "  framework, platform, and research project.",
        "---",
        "",
        "<!-- Generated from data/competitors.yaml"
        " by scripts/generate_comparison.py --"
        " do not edit directly -->",
        "",
        "# Framework Comparison",
        "",
        "How SynthOrg compares to agent orchestration frameworks,"
        " platforms, and research projects.",
        "",
        f"Last updated: {last_updated}",
        "",
        "**Legend:**",
        f"{SUPPORT_ICONS['full']} Full support"
        f" | ~ Partial support"
        f" | {SUPPORT_ICONS['none']} Not supported"
        f" | {SUPPORT_ICONS['planned']} Planned",
        "",
        '!!! tip "Interactive Version"',
        "    For a filterable, sortable version of this comparison,"
        " visit the [interactive comparison page](https://synthorg.io/compare/).",
        "",
    ]


def _competitor_row(
    comp: _Competitor,
    group_keys: Sequence[str],
    categories: list[dict[str, str]],
) -> str:
    """Build a single Markdown table row for a competitor."""
    name = comp["name"]
    url = comp.get("url", "")
    if url:
        name_cell = (
            f"[**{name}**]({url})" if comp.get("is_synthorg") else f"[{name}]({url})"
        )
    else:
        name_cell = f"**{name}**" if comp.get("is_synthorg") else name

    cat_label = _category_label(categories, comp.get("category", ""))
    license_val = comp.get("license", "")
    pricing_raw = comp.get("pricing", "")
    pricing_val = PRICING_LABELS.get(pricing_raw, pricing_raw)
    self_hosted_raw = comp.get("self_hosted", "")
    self_hosted_val = SELF_HOSTED_LABELS.get(self_hosted_raw, self_hosted_raw)
    features = comp.get("features", {})

    dim_cells = []
    for key in group_keys:
        feat = features.get(key, {})
        support = feat.get("support", "none") if isinstance(feat, dict) else "none"
        dim_cells.append(_support_icon(support))

    return (
        f"| {name_cell} | {cat_label} | {license_val}"
        f" | {pricing_val} | {self_hosted_val} | " + " | ".join(dim_cells) + " |"
    )


def _thematic_tables(
    dimensions: list[dict[str, str]],
    categories: list[dict[str, str]],
    competitors: list[_Competitor],
) -> list[str]:
    """Generate the thematic comparison tables."""
    lines: list[str] = []
    for group in TABLE_GROUPS:
        lines.append(f"## {group['title']}")
        lines.append("")

        dim_headers = [_dimension_label(dimensions, k) for k in group["keys"]]
        header = (
            "| Framework | Category | License | Pricing | Self-Hosted | "
            + " | ".join(dim_headers)
            + " |"
        )
        separator = (
            "|:----------|:---------|:--------|:--------|:-----------:|"
            + "|".join([":---:" for _ in group["keys"]])
            + "|"
        )
        lines.append(header)
        lines.append(separator)

        lines.extend(
            _competitor_row(comp, group["keys"], categories) for comp in competitors
        )
        lines.append("")
    return lines


def _project_links(competitors: list[_Competitor]) -> list[str]:
    """Generate the project links section."""
    lines = ["## Project Links", ""]
    for comp in competitors:
        name = comp["name"]
        url = comp.get("url", "")
        repo = comp.get("repo", "")
        parts = [f"**{name}**"]
        if url:
            parts.append(f"[Website]({url})")
        if repo:
            parts.append(f"[Repository]({repo})")
        lines.append(f"- {' -- '.join(parts)}")
    lines.append("")
    return lines


def _generate_markdown(data: _ComparisonData) -> str:
    """Generate the full Markdown page from the structured data."""
    lines: list[str] = []
    lines.extend(_frontmatter_and_intro(data["meta"]["last_updated"]))
    lines.extend(
        _thematic_tables(data["dimensions"], data["categories"], data["competitors"])
    )
    lines.extend(_project_links(data["competitors"]))
    return "\n".join(lines)


def main() -> int:
    """Load YAML data and generate the comparison Markdown page."""
    try:
        data = _load_data()
        markdown = _generate_markdown(data)
    except Exception as exc:
        print("Failed to generate comparison page:", file=sys.stderr)
        traceback.print_exception(exc)
        return 1

    try:
        OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
        OUTPUT_FILE.write_text(markdown, encoding="utf-8")
        print(f"Wrote comparison page to {OUTPUT_FILE.relative_to(REPO_ROOT)}")
    except OSError as exc:
        print("Failed to write output file:", file=sys.stderr)
        traceback.print_exception(exc)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
