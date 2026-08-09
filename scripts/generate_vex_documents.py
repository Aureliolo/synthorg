#!/usr/bin/env python3
"""Render the vulnerability triage ledger into the files scanners read.

``.github/vex/triage.yaml`` is the only place a vulnerability is silenced.
This renders it into the two artefacts that actually do the silencing, and
which artefact an entry reaches is decided by its status alone, so no entry is
ever suppressed twice:

* ``not_affected`` renders into ``.github/vex/synthorg.openvex.json``. VEX can
  express it, so it travels with every published image as an OpenVEX
  attestation and a consumer picks it up with ``trivy image --vex oci``. Our
  own scans read the same document from disk, which is what keeps a statement
  that matches nothing from shipping unnoticed: with no ignore-file fallback
  behind it, the finding resurfaces and ``scripts/evaluate-scan.sh`` reports
  it.
* ``accepted`` renders into ``.github/.trivyignore.yaml``. The product is
  affected and the risk is accepted anyway; VEX has no status for that, and
  writing one of its ``not_affected`` justifications instead would be a false
  statement published under our signature.

Rendering is byte-stable: the OpenVEX document's ``@id`` is derived from the
statements it carries and its timestamp from the ledger's ``updated`` field,
so regenerating an unchanged ledger produces an unchanged file and
``scripts/check_vex_triage_sync.py`` can compare the two.

Usage::

    python scripts/generate_vex_documents.py
"""

import argparse
import dataclasses
import datetime as dt
import hashlib
import json
import sys
from pathlib import Path
from typing import (
    Final,
    Literal,
    NotRequired,
    TypedDict,
    TypeIs,
    get_args,
    override,
)

import yaml

REPO_ROOT: Final[Path] = Path(__file__).resolve().parent.parent

TRIAGE_FILE: Final[Path] = REPO_ROOT / ".github" / "vex" / "triage.yaml"
TRIVYIGNORE_FILE: Final[Path] = REPO_ROOT / ".github" / ".trivyignore.yaml"
OPENVEX_FILE: Final[Path] = REPO_ROOT / ".github" / "vex" / "synthorg.openvex.json"

REGENERATE_COMMAND: Final[str] = "uv run python scripts/generate_vex_documents.py"

TriageStatus = Literal["not_affected", "accepted"]

# The OpenVEX justification vocabulary is closed. A statement outside it is not
# a weaker statement, it is one no consumer can interpret.
Justification = Literal[
    "component_not_present",
    "vulnerable_code_not_present",
    "vulnerable_code_not_in_execute_path",
    "vulnerable_code_cannot_be_controlled_by_adversary",
    "inline_mitigations_already_exist",
]

STATUS_NOT_AFFECTED: Final[TriageStatus] = "not_affected"
STATUS_ACCEPTED: Final[TriageStatus] = "accepted"

# Derived from the types rather than restated, so the set a value is validated
# against and the type it is validated into cannot drift apart.
_STATUSES: Final[frozenset[str]] = frozenset(get_args(TriageStatus))
_JUSTIFICATIONS: Final[frozenset[str]] = frozenset(get_args(Justification))


_OPENVEX_CONTEXT: Final[str] = "https://openvex.dev/ns/v0.2.0"

# Content-addressed, so a revision of the ledger is a new document rather than
# a new version of the old one, and nobody has to remember to bump a counter.
_DOC_ID_PREFIX: Final[str] = (
    "https://github.com/Aureliolo/synthorg/.github/vex/synthorg-openvex-"
)
_DOC_VERSION: Final[int] = 1

_GENERATED_HEADER: Final[str] = (
    f"# Generated from .github/vex/triage.yaml. Edit the ledger, then run\n"
    f"# `{REGENERATE_COMMAND}`.\n"
    f"#\n"
    f"# Holds the risk-accepted findings only. A finding assessed as\n"
    f"# not_affected is published as an OpenVEX statement instead, so that the\n"
    f"# reasoning travels with the image rather than stopping at this file.\n"
)

# Wide enough that a rendered scalar wraps where the prose does, not where the
# dumper's default 80 columns happen to fall.
_YAML_WIDTH: Final[int] = 88

_MIDNIGHT_UTC_SUFFIX: Final[str] = "T00:00:00Z"


def _is_status(value: object) -> TypeIs[TriageStatus]:
    """Narrow a parsed scalar to a status."""
    return value in _STATUSES


def _is_justification(value: object) -> TypeIs[Justification]:
    """Narrow a parsed scalar to an OpenVEX justification."""
    return value in _JUSTIFICATIONS


def _status_coupling_problems(
    status: TriageStatus,
    purls: tuple[str, ...],
    justification: Justification | None,
    label: str,
) -> list[str]:
    """State the status/justification/purls coupling, once.

    Both the ledger parser and :meth:`TriageEntry.__post_init__` decide from
    this, so the rule cannot hold in one and quietly not in the other.
    """
    if status == STATUS_NOT_AFFECTED:
        problems: list[str] = []
        if not purls:
            problems.append(
                f"{label}: not_affected needs at least one purl, because an "
                f"OpenVEX statement addresses a product",
            )
        if justification is None:
            problems.append(f"{label}: not_affected needs a justification")
        return problems
    if justification is not None:
        return [
            (
                f"{label}: accepted must carry no justification; OpenVEX "
                f"justifications assert the product is not affected, which is "
                f"the opposite of accepting the risk"
            ),
        ]
    return []


# One row of Trivy's structured ignore file. `purls` is absent rather than
# empty when an assessment applies everywhere, which is how Trivy spells it.
class _IgnoreFinding(TypedDict):
    """One row of Trivy's structured ignore file."""

    id: str
    purls: NotRequired[list[str]]
    expired_at: str
    statement: str


# The OpenVEX wire shapes. Spelled functionally where a key is not a Python
# identifier, so the rendered JSON is the type rather than a comment about it.
_Product = TypedDict("_Product", {"@id": str})


class _Statement(TypedDict):
    """One OpenVEX statement."""

    vulnerability: dict[str, str]
    products: list[_Product]
    status: str
    justification: str
    impact_statement: str


_Document = TypedDict(
    "_Document",
    {
        "@context": str,
        "@id": str,
        "author": str,
        "timestamp": str,
        "version": int,
        "statements": list[_Statement],
    },
)


class VexTriageError(Exception):
    """The triage ledger is unreadable or does not satisfy its schema.

    Carries every problem found rather than the first, because a ledger with
    three malformed entries should cost one round trip to fix, not three.

    Attributes:
        problems: One entry per problem. Kept itemised alongside the joined
            message so a caller reporting a list does not have to re-split
            prose it did not format.
    """

    def __init__(self, message: str, problems: tuple[str, ...] = ()) -> None:
        super().__init__(message)
        self.problems: tuple[str, ...] = problems or (message,)


@dataclasses.dataclass(frozen=True, slots=True)
class TriageEntry:
    """One assessed vulnerability.

    Attributes:
        id: Vulnerability identifier, as a scanner reports it.
        purls: Package URLs the assessment applies to. Required for
            ``not_affected``, because an OpenVEX statement addresses a
            product; optional for ``accepted``, where an empty list keeps
            Trivy's suppress-everywhere behaviour.
        status: ``not_affected`` or ``accepted``.
        justification: OpenVEX justification, for ``not_affected`` only.
        re_review_by: Date the assessment stops being trusted.
        statement: Why the assessment holds.
    """

    id: str
    purls: tuple[str, ...]
    status: TriageStatus
    justification: Justification | None
    re_review_by: dt.date
    statement: str

    def __post_init__(self) -> None:
        """Refuse an entry the docstring above forbids.

        The parser reports the same rule as a collected list, for a ledger
        edit worth one round trip. This is the version that binds every other
        caller, so a hand-built entry cannot reach rendering claiming
        ``not_affected`` with nothing to attach the claim to.
        """
        problems = _status_coupling_problems(
            self.status, self.purls, self.justification, self.id
        )
        if problems:
            raise VexTriageError("; ".join(problems), problems=tuple(problems))


@dataclasses.dataclass(frozen=True, slots=True)
class Triage:
    """The ledger as a whole.

    Attributes:
        author: Who publishes the assessments, as it appears in OpenVEX.
        updated: When the ledger was last reviewed, in UTC.
        entries: Every assessed vulnerability.
    """

    author: str
    updated: dt.datetime
    entries: tuple[TriageEntry, ...]


def _require_mapping(raw: object, path: Path) -> dict[str, object]:
    """Narrow a parsed document to a mapping, or fail closed."""
    if not isinstance(raw, dict):
        msg = f"{path}: the ledger must be a mapping, found {type(raw).__name__}"
        raise VexTriageError(msg)
    return raw


def _parse_date(value: object, label: str, problems: list[str]) -> dt.date | None:
    """Parse an ISO date, recording a problem instead of raising."""
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    if isinstance(value, str):
        try:
            return dt.date.fromisoformat(value)
        except ValueError:
            problems.append(f"{label}: {value!r} is not an ISO date (YYYY-MM-DD)")
            return None
    problems.append(f"{label}: missing or not a date")
    return None


def _parse_updated(value: object, problems: list[str]) -> dt.datetime | None:
    """Parse the ledger's ``updated`` timestamp into an aware UTC datetime.

    A value carrying no offset is read as UTC, which is what the field is
    declared to be. A date with no time at all is midnight UTC: an unquoted
    ``updated: 2026-08-09`` reaches here as a :class:`datetime.date`, and
    writing it that way is what anyone would do having just written
    ``re_review_by`` in the same shape.
    """
    if isinstance(value, dt.datetime):
        return value if value.tzinfo else value.replace(tzinfo=dt.UTC)
    if isinstance(value, dt.date):
        return dt.datetime.combine(value, dt.time.min, tzinfo=dt.UTC)
    if isinstance(value, str):
        try:
            parsed = dt.datetime.fromisoformat(value)
        except ValueError:
            problems.append(f"updated: {value!r} is not an ISO 8601 timestamp")
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=dt.UTC)
    problems.append("updated: missing or not a timestamp")
    return None


def _parse_purls(raw: object, label: str, problems: list[str]) -> tuple[str, ...]:
    """Narrow an entry's ``purls`` to a tuple of non-empty strings."""
    if raw is None:
        return ()
    if not isinstance(raw, list):
        problems.append(f"{label}: purls must be a list")
        return ()
    purls: list[str] = []
    for item in raw:
        if not isinstance(item, str) or not item.strip():
            problems.append(f"{label}: purls entries must be non-empty strings")
            continue
        purls.append(item.strip())
    return tuple(purls)


def _check_status_rules(
    status: TriageStatus,
    purls: tuple[str, ...],
    justification: object,
    label: str,
    problems: list[str],
) -> Justification | None:
    """Validate the justification vocabulary and the per-status coupling."""
    resolved: Justification | None = None
    if _is_justification(justification):
        resolved = justification
    elif justification is not None:
        problems.append(
            f"{label}: justification must be one of {sorted(_JUSTIFICATIONS)}",
        )
    problems.extend(_status_coupling_problems(status, purls, resolved, label))
    return resolved


def _parse_entry(raw: object, index: int, problems: list[str]) -> TriageEntry | None:
    """Build one entry, recording every problem it carries."""
    label = f"entries[{index}]"
    if not isinstance(raw, dict):
        problems.append(f"{label}: must be a mapping")
        return None

    identifier = raw.get("id")
    if not isinstance(identifier, str) or not identifier.strip():
        problems.append(f"{label}: id is missing or empty")
        return None
    label = f"entries[{index}] ({identifier})"

    status = raw.get("status")
    if not _is_status(status):
        problems.append(f"{label}: status must be one of {sorted(_STATUSES)}")
        return None

    purls = _parse_purls(raw.get("purls"), label, problems)
    before_status_rules = len(problems)
    justification = _check_status_rules(
        status, purls, raw.get("justification"), label, problems
    )
    status_rules_ok = len(problems) == before_status_rules
    re_review_by = _parse_date(
        raw.get("re_review_by"), f"{label}: re_review_by", problems
    )

    statement = raw.get("statement")
    if not isinstance(statement, str) or not statement.strip():
        problems.append(f"{label}: statement is missing or empty")
        statement = ""

    # Constructing an entry that broke the coupling would raise from
    # ``__post_init__`` and cost the caller the rest of the ledger's problems,
    # which is the whole reason they are collected rather than raised one by
    # one. The problems are already recorded, so drop the entry instead.
    if re_review_by is None or not status_rules_ok:
        return None
    return TriageEntry(
        id=identifier.strip(),
        purls=purls,
        status=status,
        justification=justification,
        re_review_by=re_review_by,
        statement=statement.strip(),
    )


def load_triage(path: Path | None = None) -> Triage:
    """Read and validate the triage ledger.

    Args:
        path: Ledger file; defaults to :data:`TRIAGE_FILE`. Resolved here
            rather than as a parameter default, which would bind at import and
            leave this one early-bound while its sibling paths were not.

    Returns:
        The validated ledger.

    Raises:
        VexTriageError: The file is unreadable, unparseable, or violates the
            schema. The message lists every problem found.
    """
    path = path if path is not None else TRIAGE_FILE
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        msg = f"{path}: {exc}"
        raise VexTriageError(msg) from exc
    try:
        raw = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        msg = f"{path}: {exc}"
        raise VexTriageError(msg) from exc

    document = _require_mapping(raw, path)
    problems: list[str] = []

    author = document.get("author")
    if not isinstance(author, str) or not author.strip():
        problems.append("author: missing or empty")
        author = ""
    updated = _parse_updated(document.get("updated"), problems)

    raw_entries = document.get("entries")
    if raw_entries is None:
        raw_entries = []
    if not isinstance(raw_entries, list):
        problems.append("entries: must be a list")
        raw_entries = []

    entries: list[TriageEntry] = []
    for index, raw_entry in enumerate(raw_entries):
        entry = _parse_entry(raw_entry, index, problems)
        if entry is not None:
            entries.append(entry)

    seen: set[str] = set()
    for entry in entries:
        if entry.id in seen:
            problems.append(
                f"{entry.id}: appears more than once; one vulnerability gets "
                f"one assessment",
            )
        seen.add(entry.id)

    if problems or updated is None:
        collected = problems or ["updated: missing or not a timestamp"]
        joined = "\n  ".join(collected)
        msg = f"{path}:\n  {joined}"
        raise VexTriageError(msg, problems=tuple(collected))

    return Triage(author=author.strip(), updated=updated, entries=tuple(entries))


def _block_scalar_str(dumper: yaml.SafeDumper, data: str) -> yaml.ScalarNode:
    """Render a multi-line string as a block scalar so prose stays readable."""
    style = "|" if "\n" in data else None
    return dumper.represent_scalar("tag:yaml.org,2002:str", data, style=style)


class _TriageDumper(yaml.SafeDumper):
    """Dumper that keeps rendered prose readable.

    PyYAML puts a block sequence at its parent's indentation by default, so
    entries would sit at column 0 under ``vulnerabilities:``. Valid, but this
    file is read by a person mid-triage, and the hand-written form it renders
    into indents its entries.
    """

    @override
    def increase_indent(
        self,
        flow: bool = False,
        indentless: bool = False,
    ) -> None:
        # `indentless` is accepted to match PyYAML's signature and then
        # ignored: forcing it False is the entire reason this override exists.
        super().increase_indent(flow=flow, indentless=False)


_TriageDumper.add_representer(str, _block_scalar_str)


def render_trivyignore(triage: Triage) -> str:
    """Render the Trivy ignore file for the risk-accepted entries.

    Args:
        triage: Validated ledger.

    Returns:
        The full file contents, header included.
    """
    findings: list[_IgnoreFinding] = []
    for entry in triage.entries:
        if entry.status != STATUS_ACCEPTED:
            continue
        expired_at = f"{entry.re_review_by.isoformat()}{_MIDNIGHT_UTC_SUFFIX}"
        statement = f"{entry.statement}\n"
        # Spelled as two whole literals rather than one built key by key: the
        # rendered order is the file a person reads, and `purls` belongs next
        # to the id it narrows, not appended after the prose.
        finding: _IgnoreFinding = (
            {
                "id": entry.id,
                "purls": list(entry.purls),
                "expired_at": expired_at,
                "statement": statement,
            }
            if entry.purls
            else {
                "id": entry.id,
                "expired_at": expired_at,
                "statement": statement,
            }
        )
        findings.append(finding)

    body = yaml.dump(
        {"vulnerabilities": findings},
        Dumper=_TriageDumper,
        default_flow_style=False,
        sort_keys=False,
        allow_unicode=True,
        width=_YAML_WIDTH,
    )
    return f"{_GENERATED_HEADER}\n{body}"


def _openvex_statements(triage: Triage) -> list[_Statement]:
    """Build the OpenVEX statements for the not-affected entries.

    Each statement's product is the package purl rather than an image purl.
    That is the form the OpenVEX specification uses in its own minimal
    document, and it is the only form that matches at both ends: Trivy derives
    no purl for the root of a locally loaded image, so an image-scoped product
    would match nothing in the six scans that read this file from disk, while
    a package-scoped product matches the leaf component locally and remotely
    alike.

    Raises:
        VexTriageError: A not-affected entry reached rendering without the
            justification or the product its claim needs. ``TriageEntry``
            refuses to hold that combination, so reaching it means something
            bypassed the constructor, and publishing a claim with nothing to
            attach it to is worse than failing here.
    """
    statements: list[_Statement] = []
    for entry in triage.entries:
        if entry.status != STATUS_NOT_AFFECTED:
            continue
        if entry.justification is None or not entry.purls:
            missing = "justification" if entry.justification is None else "product"
            msg = f"{entry.id}: not_affected reached rendering with no {missing}"
            raise VexTriageError(msg)
        statements.append(
            {
                "vulnerability": {"name": entry.id},
                "products": [{"@id": purl} for purl in entry.purls],
                "status": entry.status,
                "justification": entry.justification,
                "impact_statement": entry.statement,
            },
        )
    return statements


def render_openvex(triage: Triage) -> str:
    """Render the OpenVEX document for the not-affected entries.

    Args:
        triage: Validated ledger.

    Returns:
        The full file contents, newline-terminated.
    """
    statements = _openvex_statements(triage)
    fingerprint = hashlib.sha256(
        json.dumps(statements, sort_keys=True, separators=(",", ":")).encode("utf-8"),
    ).hexdigest()
    document: _Document = {
        "@context": _OPENVEX_CONTEXT,
        "@id": f"{_DOC_ID_PREFIX}{fingerprint}",
        "author": triage.author,
        "timestamp": triage.updated.astimezone(dt.UTC)
        .isoformat()
        .replace("+00:00", "Z"),
        "version": _DOC_VERSION,
        "statements": statements,
    }
    return f"{json.dumps(document, indent=2)}\n"


def rendered_files(triage: Triage) -> dict[Path, str]:
    """Return every generated file and the contents it must hold."""
    return {
        TRIVYIGNORE_FILE: render_trivyignore(triage),
        OPENVEX_FILE: render_openvex(triage),
    }


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Render .github/vex/triage.yaml into the Trivy ignore file "
        "and the OpenVEX document.",
    )
    parser.parse_args(argv)

    try:
        triage = load_triage()
    except VexTriageError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1

    for path, contents in rendered_files(triage).items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(contents, encoding="utf-8", newline="\n")
        print(f"wrote {path.relative_to(REPO_ROOT).as_posix()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
