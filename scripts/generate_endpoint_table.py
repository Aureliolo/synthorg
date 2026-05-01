"""Generate the API endpoint table from the exported OpenAPI schema.

Reads ``docs/openapi/openapi.json`` (produced by
``scripts/export_openapi.py``), groups operations by section + tag,
and rewrites the table block in ``docs/openapi/index.md`` between
the ``<!-- BEGIN: auto-generated endpoint table -->`` /
``<!-- END: ... -->`` sentinels.

Run after ``scripts/export_openapi.py``::

    uv run python scripts/export_openapi.py
    uv run python scripts/generate_endpoint_table.py

CI runs both before ``zensical build``.  A pre-commit hook calls
this script with ``--check`` to ensure the committed
``docs/openapi/index.md`` matches what the generator would produce
(blocks doc drift on every commit that touches a controller).
"""

import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from collections.abc import Iterable

REPO_ROOT: Final[Path] = Path(__file__).resolve().parent.parent
SCHEMA_FILE: Final[Path] = REPO_ROOT / "docs" / "openapi" / "openapi.json"
INDEX_FILE: Final[Path] = REPO_ROOT / "docs" / "openapi" / "index.md"

BEGIN_SENTINEL: Final[str] = "<!-- BEGIN: auto-generated endpoint table -->"
END_SENTINEL: Final[str] = "<!-- END: auto-generated endpoint table -->"

# Five high-level sections preserved as headings.  Tag -> section
# assignment; every tag in the OpenAPI export must appear here so a
# new controller never silently drops into a default bucket.  Add a
# row when you ship a new tag.
TAG_TO_SECTION: Final[dict[str, str]] = {
    # Identity and users
    "auth": "Identity and users",
    "users": "Identity and users",
    # Organization and agents
    "company": "Organization and agents",
    "departments": "Organization and agents",
    "agents": "Organization and agents",
    "autonomy": "Organization and agents",
    "collaboration": "Organization and agents",
    "quality": "Organization and agents",
    "activities": "Organization and agents",
    "personalities": "Organization and agents",
    "ontology": "Organization and agents",
    "roles": "Organization and agents",
    "scaling": "Organization and agents",
    "evaluation": "Organization and agents",
    "clients": "Organization and agents",
    "training": "Organization and agents",
    # Work and coordination
    "projects": "Work and coordination",
    "tasks": "Work and coordination",
    "messages": "Work and coordination",
    "meetings": "Work and coordination",
    "approvals": "Work and coordination",
    "artifacts": "Work and coordination",
    "conflict-escalations": "Work and coordination",
    "reviews": "Work and coordination",
    "requests": "Work and coordination",
    # Workflows
    "workflows": "Workflows",
    "workflow-executions": "Workflows",
    "subworkflows": "Workflows",
    "template-packs": "Workflows",
    "setup": "Workflows",
    # Operations and platform
    "health": "Operations and platform",
    "providers": "Operations and platform",
    "budget": "Operations and platform",
    "analytics": "Operations and platform",
    "metrics": "Operations and platform",
    "memory": "Operations and platform",
    "backup": "Operations and platform",
    "settings": "Operations and platform",
    "ceremony-policy": "Operations and platform",
    "events": "Operations and platform",
    "interrupts": "Operations and platform",
    "Integrations": "Operations and platform",
    "coordination": "Operations and platform",
    "meta": "Operations and platform",
    "meta-analytics": "Operations and platform",
    "security": "Operations and platform",
    "admin": "Operations and platform",
}

SECTION_ORDER: Final[tuple[str, ...]] = (
    "Identity and users",
    "Organization and agents",
    "Work and coordination",
    "Workflows",
    "Operations and platform",
)

# Resource label override for tags whose canonical name reads better
# in title-case-with-spaces.
TAG_DISPLAY: Final[dict[str, str]] = {
    "auth": "Auth",
    "users": "Users",
    "company": "Company",
    "departments": "Departments",
    "agents": "Agents",
    "autonomy": "Agent Autonomy",
    "collaboration": "Agent Collaboration",
    "quality": "Agent Quality",
    "activities": "Activities",
    "personalities": "Personalities",
    "ontology": "Ontology",
    "roles": "Roles",
    "scaling": "Scaling",
    "evaluation": "Evaluation",
    "clients": "Clients",
    "training": "Training",
    "projects": "Projects",
    "tasks": "Tasks",
    "messages": "Messages",
    "meetings": "Meetings",
    "approvals": "Approvals",
    "artifacts": "Artifacts",
    "conflict-escalations": "Escalations",
    "reviews": "Reviews",
    "requests": "Requests",
    "workflows": "Workflows",
    "workflow-executions": "Workflow Executions",
    "subworkflows": "Subworkflows",
    "template-packs": "Template Packs",
    "setup": "Setup",
    "health": "Health",
    "providers": "Providers",
    "budget": "Budget",
    "analytics": "Analytics",
    "metrics": "Metrics",
    "memory": "Memory Admin",
    "backup": "Backups",
    "settings": "Settings",
    "ceremony-policy": "Ceremony Policy",
    "events": "Event Stream",
    "interrupts": "Interrupts",
    "Integrations": "Integrations",
    "coordination": "Coordination",
    "meta": "Meta",
    "meta-analytics": "Meta Analytics",
    "security": "Security",
    "admin": "Admin",
}

# Tag -> base path used in the rendered table.  Auto-derived from the
# OpenAPI paths (longest common prefix per tag); sites where the auto-
# derivation produces an empty string fall back to this manual map.
TAG_BASE_PATH_FALLBACK: Final[dict[str, str]] = {
    "health": "/healthz, /readyz",
}

API_PREFIX: Final[str] = "/api/v1"


def _strip_prefix(path: str) -> str:
    """Remove the ``/api/v1`` prefix for display.

    Only strips when ``path`` matches ``/api/v1`` exactly or starts
    with ``/api/v1/``; ``str.removeprefix`` would otherwise incorrectly
    strip ``/api/v10`` or ``/api/v1foo`` and corrupt rendered routes.
    """
    if path == API_PREFIX:
        return "/"
    if path.startswith(API_PREFIX + "/"):
        return path[len(API_PREFIX) :]
    return path


def _common_base_path(paths: Iterable[str]) -> str:
    """Return the shortest distinct base path shared by all paths.

    Trims to the first segment that contains no path parameter; if all
    paths start with the same parameterised prefix, returns that
    prefix.  Empty when no path is supplied OR when the supplied
    paths share only the root (``/``) -- callers must fall back to
    ``TAG_BASE_PATH_FALLBACK`` for tags whose endpoints sit at
    disjoint roots (e.g. ``/healthz`` + ``/readyz``); silently
    returning the first path would render misleadingly as if the
    whole tag lived under that one path.
    """
    paths_list = sorted({_strip_prefix(p) for p in paths})
    if not paths_list:
        return ""
    if len(paths_list) == 1:
        return paths_list[0]
    first = paths_list[0].split("/")
    base_segments: list[str] = []
    for i, segment in enumerate(first):
        if all(
            len(p.split("/")) > i and p.split("/")[i] == segment for p in paths_list
        ):
            base_segments.append(segment)
        else:
            break
    base = "/".join(base_segments) or "/"
    if base in {"", "/"}:
        # Disjoint roots: caller must use TAG_BASE_PATH_FALLBACK.
        return ""
    return base


def _section_for_tag(tag: str) -> str:
    """Map an OpenAPI tag onto its rendered section heading.

    Fail loudly when a tag is missing from ``TAG_TO_SECTION`` so that
    a new controller surfaces in CI instead of silently landing in
    "Operations and platform" -- the rendered table is the
    public-facing endpoint inventory and a misclassified entry is a
    documentation bug we want a human to resolve before the page
    ships.
    """
    if tag not in TAG_TO_SECTION:
        msg = (
            f"OpenAPI tag {tag!r} has no entry in TAG_TO_SECTION. "
            f"Add the tag to scripts/generate_endpoint_table.py "
            f"before regenerating docs/openapi/index.md."
        )
        raise KeyError(msg)
    return TAG_TO_SECTION[tag]


def _purpose_for_tag(tag: str, paths: Iterable[str]) -> str:
    """Derive a one-line purpose string from the path set.

    Uses the OpenAPI tag name + path count.  Authors who want a
    richer description should add it to the controller's class
    docstring (Litestar promotes the first line into the OpenAPI
    description), and we'll surface it here in a follow-up.
    """
    count = len({_strip_prefix(p) for p in paths})
    label = TAG_DISPLAY.get(tag, tag)
    if count == 1:
        return f"{label} endpoint."
    return f"{count} routes under {label}."


def _table_for_section(
    section: str,
    tags_in_section: list[tuple[str, list[str]]],
) -> list[str]:
    """Render the markdown table for a section."""
    lines = [
        f"### {section}",
        "",
        "| Resource | Path | Purpose |",
        "|---|---|---|",
    ]
    for tag, paths in sorted(
        tags_in_section, key=lambda x: TAG_DISPLAY.get(x[0], x[0])
    ):
        display = TAG_DISPLAY.get(tag, tag)
        common_base = _common_base_path(paths)
        if not common_base:
            # Disjoint roots -- caller MUST have a fallback entry, else
            # the table cell would render empty (a documentation bug
            # we refuse to ship silently).
            if tag not in TAG_BASE_PATH_FALLBACK:
                msg = (
                    f"Tag {tag!r} has disjoint endpoint paths "
                    f"({sorted(paths)!r}) but no entry in "
                    f"TAG_BASE_PATH_FALLBACK; add a manual base-path "
                    f"mapping in scripts/generate_endpoint_table.py "
                    f"before regenerating docs/openapi/index.md."
                )
                raise KeyError(msg)
            base = TAG_BASE_PATH_FALLBACK[tag]
        else:
            base = common_base
        purpose = _purpose_for_tag(tag, paths)
        lines.append(f"| {display} | `{base}` | {purpose} |")
    lines.append("")
    return lines


def _validated_tags(verb: str, path: str, op: dict[str, object]) -> list[str]:
    """Return the operation's tag list, raising on invalid / missing tags.

    Fail-fast on untagged or malformed operations: this script is the
    docs source of truth, so silently dropping a route would let a
    controller ship without appearing in the public endpoint inventory.
    """
    tags_field = op.get("tags")
    if not isinstance(tags_field, list) or not tags_field:
        msg = (
            f"OpenAPI operation {verb.upper()} {path} is missing a "
            "valid 'tags' list. Add a tag before regenerating the "
            "endpoint table."
        )
        raise TypeError(msg)
    for tag in tags_field:
        if not isinstance(tag, str) or not tag:
            msg = (
                f"OpenAPI operation {verb.upper()} {path} has an "
                f"invalid tag entry: {tag!r}"
            )
            raise TypeError(msg)
    return tags_field


def _collect_tag_paths(schema: dict[str, object]) -> dict[str, list[str]]:
    """Walk the OpenAPI ``paths`` and return ``tag -> [path, ...]``."""
    paths = schema.get("paths", {})
    if not isinstance(paths, dict):
        msg = "OpenAPI schema 'paths' is not a dict"
        raise TypeError(msg)
    tag_to_paths: dict[str, list[str]] = defaultdict(list)
    for path, methods in paths.items():
        if not isinstance(methods, dict):
            continue
        seen_tags: set[str] = set()
        for verb, op in methods.items():
            if verb in {"parameters", "summary", "description"}:
                continue
            if not isinstance(op, dict):
                continue
            for tag in _validated_tags(verb, path, op):
                if tag in seen_tags:
                    continue
                tag_to_paths[tag].append(path)
                seen_tags.add(tag)
    return tag_to_paths


def _build_table(schema: dict[str, object]) -> str:
    """Return the rendered markdown block (between sentinels, exclusive)."""
    tag_to_paths = _collect_tag_paths(schema)
    section_to_tags: dict[str, list[tuple[str, list[str]]]] = defaultdict(list)
    for tag, tag_paths in tag_to_paths.items():
        section_to_tags[_section_for_tag(tag)].append((tag, tag_paths))

    out_lines: list[str] = []
    for section in SECTION_ORDER:
        if section not in section_to_tags:
            continue
        out_lines.extend(_table_for_section(section, section_to_tags[section]))
    return "\n".join(out_lines).rstrip() + "\n"


def _replace_block(text: str, replacement: str) -> str:
    """Replace the content between the two sentinels."""
    pattern = re.compile(
        re.escape(BEGIN_SENTINEL) + r".*?" + re.escape(END_SENTINEL),
        re.DOTALL,
    )
    new_block = f"{BEGIN_SENTINEL}\n\n{replacement}\n{END_SENTINEL}"
    if not pattern.search(text):
        msg = (
            f"Could not find sentinels in {INDEX_FILE}; "
            "ensure both sentinel comments exist."
        )
        raise RuntimeError(msg)
    return pattern.sub(new_block, text)


def main() -> int:
    """CLI entry point; returns 0 on success, 1 on drift, 2 on missing schema."""
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--check",
        action="store_true",
        help="Exit non-zero if the generated table would differ from "
        "the committed file.",
    )
    args = parser.parse_args()

    if not SCHEMA_FILE.exists():
        print(
            f"ERROR: {SCHEMA_FILE} does not exist.  "
            f"Run `uv run python scripts/export_openapi.py` first.",
            file=sys.stderr,
        )
        return 2

    schema = json.loads(SCHEMA_FILE.read_text(encoding="utf-8"))
    rendered = _build_table(schema)
    current = INDEX_FILE.read_text(encoding="utf-8")
    expected = _replace_block(current, rendered)

    if args.check:
        if current != expected:
            print(
                "ERROR: docs/openapi/index.md is out of sync with the "
                "OpenAPI export.  Run "
                "`uv run python scripts/generate_endpoint_table.py` and "
                "commit the diff.",
                file=sys.stderr,
            )
            return 1
        return 0

    if current != expected:
        INDEX_FILE.write_text(expected, encoding="utf-8")
        print(f"Wrote endpoint table to {INDEX_FILE}")
    else:
        print(f"{INDEX_FILE} already in sync; no change")
    return 0


if __name__ == "__main__":
    sys.exit(main())
