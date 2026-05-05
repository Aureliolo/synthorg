#!/usr/bin/env python3
"""Meta-gate: every MANDATORY paragraph maps to a registered gate or exemption.

Globs the canonical doc set (CLAUDE.md, web/CLAUDE.md, cli/CLAUDE.md,
docs/reference/*.md, docs/design/*.md), extracts every ``(MANDATORY)``
section header and inline-bold subsection, and verifies each one has a
matching entry in ``scripts/convention_gate_map.yaml``. Each YAML entry
declares one of:

* ``gate: <relative path to scripts/check_*.py or other enforcement>``
* ``exempt: { reason: "<non-empty justification>" }``

Exit codes:

* 0: every MANDATORY entry is registered and every referenced gate exists
* 1: missing entries, stale YAML entries, or missing gate scripts
* 2: YAML schema error (treated as setup failure, not regression)

Catches the SECOND occurrence of an ungated convention. Audits catch the
first. See CLAUDE.md "Convention Rollout (MANDATORY)" for the policy.
"""

import argparse
import dataclasses
import re
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Literal

import yaml

if TYPE_CHECKING:
    from collections.abc import Iterable

_REPO_ROOT_DEFAULT = Path(__file__).resolve().parent.parent
_INVENTORY_RELATIVE = Path("scripts") / "convention_gate_map.yaml"

_CANONICAL_DOC_FILES: tuple[str, ...] = (
    "CLAUDE.md",
    "web/CLAUDE.md",
    "cli/CLAUDE.md",
)
_CANONICAL_DOC_GLOBS: tuple[str, ...] = (
    "docs/reference/*.md",
    "docs/design/*.md",
)

_HEADER_RE = re.compile(r"^(?P<hashes>#{1,6})\s+(?P<text>.+?)\s*\(MANDATORY[^)]*\)\s*$")
_INLINE_BOLD_RE = re.compile(r"\*\*(?P<text>[^*\n]+?)\s*\(MANDATORY[^)]*\)\*\*")

_SLUG_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")


class InventorySchemaError(Exception):
    """Raised when ``scripts/convention_gate_map.yaml`` violates its schema.

    Schema errors are setup failures (exit code 2), distinct from
    regression failures (exit code 1). Surfacing them as a typed
    exception lets ``main`` map cleanly to the right exit code.
    """


@dataclasses.dataclass(frozen=True)
class MandatoryEntry:
    """One MANDATORY paragraph extracted from a markdown file."""

    file: str
    line: int
    header: str
    kind: Literal["header", "inline"]

    @property
    def rule_id(self) -> str:
        """Stable id derived from ``(file, header)``; survives line drift."""
        return make_id(self.file, self.header)


@dataclasses.dataclass(frozen=True)
class InventoryEntry:
    """One registered rule from ``scripts/convention_gate_map.yaml``.

    Exactly one of ``gate`` / ``exempt_reason`` is set. The XOR
    invariant is enforced both at YAML load time (via
    ``_validate_gate_or_exempt``) and at construction (via
    ``__post_init__``) so direct instantiation outside the loader
    cannot produce an invalid instance.
    """

    rule_id: str
    file: str
    header: str
    gate: str | None
    exempt_reason: str | None

    def __post_init__(self) -> None:
        """Enforce the XOR invariant: exactly one of gate or exempt_reason."""
        if (self.gate is None) == (self.exempt_reason is None):
            msg = (
                f"InventoryEntry {self.rule_id!r} must have exactly one of"
                " gate or exempt_reason"
            )
            raise InventorySchemaError(msg)


@dataclasses.dataclass(frozen=True)
class Violation:
    """A single failure surfaced by the gate."""

    location: str
    message: str

    def render(self) -> str:
        """Format for stdout: ``<location>: <message>``."""
        return f"{self.location}: {self.message}"


def slugify(text: str) -> str:
    """Lowercase, ASCII-only, hyphen-separated slug.

    Args:
        text: Free-form header text or file path component.

    Returns:
        Slug suitable for use as the right half of a rule id. Empty
        input maps to the empty string; callers should reject that.
    """
    lowered = text.strip().lower()
    return _SLUG_NON_ALNUM_RE.sub("-", lowered).strip("-")


def make_id(file: str, header: str) -> str:
    """Build the stable ``<file-slug>::<header-slug>`` rule id.

    Raises:
        InventorySchemaError: If either ``file`` or ``header`` slugifies
            to the empty string. A degenerate id like ``"::"`` would
            silently mismatch every inventory entry; failing loud lets
            callers surface the underlying input bug instead.
    """
    file_slug = slugify(file.replace("/", " "))
    header_slug = slugify(header)
    if not file_slug or not header_slug:
        msg = f"Cannot build rule id from empty slug: file={file!r} header={header!r}"
        raise InventorySchemaError(msg)
    return f"{file_slug}::{header_slug}"


def extract_mandatory_entries(text: str, file: str) -> list[MandatoryEntry]:
    """Extract every MANDATORY header / inline-bold subsection from ``text``.

    Args:
        text: Full file contents.
        file: Repo-relative POSIX path used to label each entry.

    Returns:
        Entries in source-order. Header matches use ``kind="header"``;
        inline ``**...(MANDATORY)**`` matches use ``kind="inline"``.
        The header text has the trailing ``(MANDATORY...)`` stripped
        and surrounding whitespace normalised; case is preserved.
    """
    entries: list[MandatoryEntry] = []
    for line_index, line in enumerate(text.splitlines(), start=1):
        header_match = _HEADER_RE.match(line)
        if header_match is not None:
            entries.append(
                MandatoryEntry(
                    file=file,
                    line=line_index,
                    header=header_match.group("text").strip(),
                    kind="header",
                )
            )
            continue
        entries.extend(
            MandatoryEntry(
                file=file,
                line=line_index,
                header=inline_match.group("text").strip(),
                kind="inline",
            )
            for inline_match in _INLINE_BOLD_RE.finditer(line)
        )
    return entries


def collect_doc_files(repo_root: Path) -> list[Path]:
    """Return every canonical doc file that exists, sorted deterministically."""
    found: list[Path] = []
    for fixed in _CANONICAL_DOC_FILES:
        candidate = repo_root / fixed
        if candidate.is_file():
            found.append(candidate)
    for pattern in _CANONICAL_DOC_GLOBS:
        found.extend(path for path in sorted(repo_root.glob(pattern)) if path.is_file())
    return found


def _relative_posix(path: Path, repo_root: Path) -> str:
    """Return ``path`` relative to ``repo_root`` as a POSIX string.

    Raises:
        InventorySchemaError: If ``path`` resolves outside ``repo_root``
            (e.g. via a symlink that escapes the repo). Surfacing this
            as the typed schema error lets ``main`` exit cleanly with
            code 2 instead of crashing on an uncaught ``ValueError``.
    """
    try:
        return path.resolve().relative_to(repo_root.resolve()).as_posix()
    except ValueError as exc:
        msg = f"Doc file path escapes repo root: {path}"
        raise InventorySchemaError(msg) from exc


def scan_repo(repo_root: Path) -> list[MandatoryEntry]:
    """Walk the canonical doc set and extract every MANDATORY entry."""
    entries: list[MandatoryEntry] = []
    for path in collect_doc_files(repo_root):
        rel = _relative_posix(path, repo_root)
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            msg = f"Could not read {path}: {type(exc).__name__}: {exc!s}"
            raise InventorySchemaError(msg) from exc
        entries.extend(extract_mandatory_entries(text, rel))
    return entries


def _yaml_error_summary(exc: yaml.YAMLError) -> str:
    """Build a one-line summary of a YAML parse error with position info.

    PyYAML's ``MarkedYAMLError`` subclasses (Scanner / Parser / Constructor
    errors) carry ``problem``, ``problem_mark``, and ``context`` attributes
    that point at the syntax error's location. The default ``str(exc)``
    is multi-line and ugly; this helper produces a single line suitable
    for the gate's stderr output.
    """
    problem = getattr(exc, "problem", None)
    mark = getattr(exc, "problem_mark", None)
    if problem and mark is not None:
        line = mark.line + 1
        column = mark.column + 1
        return f"{type(exc).__name__} at line {line}, column {column}: {problem}"
    return f"{type(exc).__name__}: {exc!s}"


def load_inventory(yaml_path: Path) -> tuple[InventoryEntry, ...]:
    """Parse and validate ``scripts/convention_gate_map.yaml``.

    Raises:
        InventorySchemaError: If the file is missing, malformed, the top
            level is not a list under ``mandatory_rules``, an entry is
            missing required fields, neither or both of ``gate`` /
            ``exempt`` are set, ``exempt.reason`` is empty, or the same
            ``id`` appears twice.
    """
    if not yaml_path.is_file():
        msg = f"Inventory file missing: {yaml_path}"
        raise InventorySchemaError(msg)
    try:
        raw_text = yaml_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        msg = (
            f"Could not read inventory file {yaml_path}: {type(exc).__name__}: {exc!s}"
        )
        raise InventorySchemaError(msg) from exc
    try:
        raw = yaml.safe_load(raw_text)
    except yaml.YAMLError as exc:
        msg = f"Inventory YAML parse error: {_yaml_error_summary(exc)}"
        raise InventorySchemaError(msg) from exc
    if not isinstance(raw, dict) or "mandatory_rules" not in raw:
        msg = "Inventory missing top-level 'mandatory_rules' list"
        raise InventorySchemaError(msg)
    rules = raw["mandatory_rules"]
    if not isinstance(rules, list):
        msg = "'mandatory_rules' must be a list"
        raise InventorySchemaError(msg)
    entries: list[InventoryEntry] = []
    seen_ids: set[str] = set()
    for index, item in enumerate(rules):
        entry = _validate_inventory_item(item, index)
        if entry.rule_id in seen_ids:
            msg = f"Duplicate rule id in inventory: {entry.rule_id!r}"
            raise InventorySchemaError(msg)
        seen_ids.add(entry.rule_id)
        entries.append(entry)
    return tuple(entries)


def _require_non_empty_str(value: object, label: str) -> str:
    """Return ``value`` if it's a non-empty string, otherwise raise."""
    if not isinstance(value, str) or not value.strip():
        msg = f"{label} must be a non-empty string (got {type(value).__name__})"
        raise InventorySchemaError(msg)
    return value


def _require_safe_relative_path(value: str, label: str) -> str:
    """Reject absolute paths and parent-directory escapes in YAML gate refs.

    Defence-in-depth for committed YAML: a ``gate`` entry pointing at an
    absolute path or one containing ``..`` segments could match a file
    outside the repo root and silently satisfy the existence check.
    """
    if Path(value).is_absolute():
        msg = f"{label} must be a relative path (got absolute: {value!r})"
        raise InventorySchemaError(msg)
    if any(part == ".." for part in Path(value).parts):
        msg = f"{label} must not contain '..' segments (got: {value!r})"
        raise InventorySchemaError(msg)
    return value


def _validate_gate_or_exempt(
    item: dict[object, object], index: int, rule_id: str
) -> tuple[str | None, str | None]:
    """Return ``(gate, exempt_reason)`` from one inventory item.

    Exactly one of ``gate`` or ``exempt`` must be present; the other
    return slot is ``None``. Splitting this branch out of
    ``_validate_inventory_item`` keeps each function under the
    cyclomatic-complexity ceiling (C901, max 10).
    """
    entry_label = f"mandatory_rules[{index}] ({rule_id!r})"
    has_gate = "gate" in item
    has_exempt = "exempt" in item
    if has_gate == has_exempt:
        msg = f"{entry_label} must have exactly one of 'gate' or 'exempt'"
        raise InventorySchemaError(msg)
    if has_gate:
        gate_label = f"{entry_label}.gate"
        gate_value = _require_non_empty_str(item["gate"], gate_label)
        gate_value = _require_safe_relative_path(gate_value, gate_label)
        return gate_value, None
    exempt_raw = item["exempt"]
    if not isinstance(exempt_raw, dict) or "reason" not in exempt_raw:
        msg = f"{entry_label}.exempt must be a mapping with a 'reason' field"
        raise InventorySchemaError(msg)
    reason = _require_non_empty_str(
        exempt_raw["reason"], f"{entry_label}.exempt.reason"
    )
    return None, reason


def _validate_inventory_item(item: object, index: int) -> InventoryEntry:
    if not isinstance(item, dict):
        msg = f"mandatory_rules[{index}] must be a mapping"
        raise InventorySchemaError(msg)
    for required in ("id", "file", "header"):
        if required not in item:
            msg = f"mandatory_rules[{index}] missing required field: {required!r}"
            raise InventorySchemaError(msg)
    rule_id = _require_non_empty_str(item["id"], f"mandatory_rules[{index}].id")
    file_value = _require_non_empty_str(item["file"], f"mandatory_rules[{index}].file")
    header = _require_non_empty_str(item["header"], f"mandatory_rules[{index}].header")
    expected_id = make_id(file_value, header)
    if rule_id != expected_id:
        msg = (
            f"mandatory_rules[{index}] id mismatch: expected {expected_id!r}"
            f" (derived from file/header), got {rule_id!r}"
        )
        raise InventorySchemaError(msg)
    gate_value, exempt_reason = _validate_gate_or_exempt(item, index, rule_id)
    return InventoryEntry(
        rule_id=rule_id,
        file=file_value,
        header=header,
        gate=gate_value,
        exempt_reason=exempt_reason,
    )


def _gate_resolves_inside_repo(gate: str, repo_root: Path) -> bool:
    """Return True iff ``gate`` resolves to a regular file inside ``repo_root``.

    Resolution is strict (missing files return False) and the resolved
    path must remain under ``repo_root.resolve()`` so a symlink that
    escapes the repo cannot satisfy the gate-existence check.
    """
    candidate = repo_root / gate
    try:
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(repo_root.resolve())
    except OSError, ValueError:
        return False
    return resolved.is_file()


def _check_extracted_against_inventory(
    extracted: Iterable[MandatoryEntry],
    inventory_by_id: dict[str, InventoryEntry],
    repo_root: Path,
) -> tuple[list[Violation], set[str]]:
    """Per-entry check: registration coverage + gate-path liveness.

    Returns the violations list and the set of extracted rule ids
    (used by the caller to detect stale inventory entries).
    """
    violations: list[Violation] = []
    extracted_ids: set[str] = set()
    for entry in extracted:
        extracted_ids.add(entry.rule_id)
        registered = inventory_by_id.get(entry.rule_id)
        if registered is None:
            violations.append(
                Violation(
                    location=f"{entry.file}:{entry.line}",
                    message=(
                        "MANDATORY rule not registered in"
                        f" {_INVENTORY_RELATIVE.as_posix()}: id={entry.rule_id!r}"
                        f" header={entry.header!r}"
                    ),
                )
            )
            continue
        if registered.gate is not None and not _gate_resolves_inside_repo(
            registered.gate, repo_root
        ):
            violations.append(
                Violation(
                    location=f"{entry.file}:{entry.line}",
                    message=(
                        f"gate script missing on disk: {registered.gate}"
                        f" (rule id={entry.rule_id!r})"
                    ),
                )
            )
    return violations, extracted_ids


def _check_stale_inventory(
    inventory_by_id: dict[str, InventoryEntry],
    extracted_ids: set[str],
) -> list[Violation]:
    """Per-inventory check: every YAML rule must match an extracted entry."""
    return [
        Violation(
            location=_INVENTORY_RELATIVE.as_posix(),
            message=(
                f"stale entry: id={inventory_entry.rule_id!r} has no matching"
                " MANDATORY paragraph in the canonical doc set"
            ),
        )
        for inventory_entry in inventory_by_id.values()
        if inventory_entry.rule_id not in extracted_ids
    ]


def reconcile(
    extracted: Iterable[MandatoryEntry],
    inventory: Iterable[InventoryEntry],
    repo_root: Path,
) -> list[Violation]:
    """Produce one Violation per mismatch between docs and inventory.

    Three checks:

    1. Every extracted MANDATORY entry must be registered in the
       inventory under a matching ``rule_id``.
    2. For inventory entries with ``gate:``, the path must exist.
    3. Every inventory entry must match at least one extracted entry
       (no stale rules).
    """
    inventory_by_id: dict[str, InventoryEntry] = {
        entry.rule_id: entry for entry in inventory
    }
    violations, extracted_ids = _check_extracted_against_inventory(
        extracted, inventory_by_id, repo_root
    )
    violations.extend(_check_stale_inventory(inventory_by_id, extracted_ids))
    return violations


def check(repo_root: Path, *, verbose: bool = False) -> list[Violation]:
    """Run the full gate against ``repo_root`` and return all violations.

    Schema errors propagate as ``InventorySchemaError`` and should be
    handled by ``main`` (exit code 2). Reconciliation violations
    return as a list (exit code 1 if non-empty).

    Args:
        repo_root: Resolved path to the repo root.
        verbose: If True, print extracted/inventory counts to stderr so a
            "passes silently due to broken glob" failure is distinguishable
            from a correctly-empty repo.
    """
    extracted = scan_repo(repo_root)
    inventory_path = repo_root / _INVENTORY_RELATIVE
    _relative_posix(inventory_path, repo_root)
    inventory = load_inventory(inventory_path)
    if verbose:
        print(
            f"convention-gate: extracted {len(extracted)} MANDATORY entries,"
            f" inventory has {len(inventory)} registered rules",
            file=sys.stderr,
        )
    return reconcile(extracted, inventory, repo_root)


def _resolve_repo_root(arg: Path | None) -> Path:
    if arg is None:
        return _REPO_ROOT_DEFAULT
    resolved = arg.resolve(strict=True)
    if not resolved.is_dir():
        msg = f"--repo-root must be a directory: {resolved}"
        raise NotADirectoryError(msg)
    return resolved


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=None,
        help="Override the repo root (default: scripts/.. relative to this file)",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print extracted/inventory counts to stderr (debugging aid)",
    )
    return parser


def _resolve_repo_root_or_exit(arg: Path | None) -> Path | int:
    """Resolve ``--repo-root`` or return the exit code to surface to ``main``."""
    try:
        return _resolve_repo_root(arg)
    except FileNotFoundError as exc:
        print(f"Error: --repo-root does not exist: {exc}", file=sys.stderr)
        return 2
    except NotADirectoryError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    except OSError as exc:
        print(
            f"Error: cannot access --repo-root: {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        return 2


def _print_failure_epilogue() -> None:
    inventory_path = _INVENTORY_RELATIVE.as_posix()
    epilogue = (
        "\nConvention-rollout gate failed. Edit "
        + inventory_path
        + " so every MANDATORY paragraph in the canonical doc set has either"
        " a registered gate or an explicit exemption. See"
        " docs/reference/conventions.md '17. Registering a new MANDATORY"
        " rule' for the procedure."
    )
    print(epilogue, file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    """CLI entry point.

    Returns:
        ``0`` if every MANDATORY entry is registered and every gate
        path exists; ``1`` if reconciliation surfaces violations;
        ``2`` if the inventory YAML fails schema validation or the
        repo root cannot be resolved.
    """
    args = _build_arg_parser().parse_args(argv)
    resolved = _resolve_repo_root_or_exit(args.repo_root)
    if isinstance(resolved, int):
        return resolved
    try:
        violations = check(resolved, verbose=args.verbose)
    except InventorySchemaError as exc:
        print(f"Inventory schema error: {exc}", file=sys.stderr)
        return 2
    if not violations:
        return 0
    for violation in violations:
        print(violation.render())
    _print_failure_epilogue()
    return 1


if __name__ == "__main__":
    sys.exit(main())
