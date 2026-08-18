#!/usr/bin/env python3
"""Pre-push + CI gate: every DAST suppression carries its reasoning.

The ZAP rule actions live in ``.github/zap-rules.tsv``; the reasoning
behind each suppression lives in the DAST Tuning table in
``docs/security.md``, which is also what the "revisit this on a ZAP
upgrade" instruction points a reviewer at. Two copies, and until this
gate nothing compared them: the table recorded 10049 as ``Warn`` while
the file suppressed it outright, and carried no row at all for 10104.
A reviewer reading either file alone was told something false.

The gate holds four things:

* every ``IGNORE`` row has a documented rationale, so a suppression
  cannot be added as a bare line nobody has to justify;
* the two files agree on every action they both name, in both
  directions, so neither can drift or document a rule the scan no
  longer carries;
* every row is the shape the ZAP action parses, three tab-separated
  fields with an action from its vocabulary, since a malformed row is
  silently skipped at scan time and the rule it meant to pin reverts to
  its default;
* the docs table yields at least one row. Without that floor the gate
  fails open: rows are matched by shape, a table that is renamed,
  reformatted or deleted simply matches nothing, and the only check
  that would then notice is the rules-side loop, which is itself empty
  whenever no rule is currently suppressed. Two edits that are each
  reasonable alone would leave this gate certifying agreement between a
  file it read and a table it never found.

``FAIL`` and other non-suppressing actions need no docs row: they hide
nothing, and the file's own header explains them.

Exit codes
----------
* ``0`` -- the two files agree and every suppression is justified.
* ``1`` -- drift, a malformed row, or an unreadable input.
"""

import argparse
import re
import sys
from pathlib import Path
from typing import Final, NamedTuple

_REPO_ROOT_DEFAULT: Final[Path] = Path(__file__).resolve().parent.parent
_RULES_RELATIVE: Final[Path] = Path(".github") / "zap-rules.tsv"
_DOCS_RELATIVE: Final[Path] = Path("docs") / "security.md"

_VALID_ACTIONS: Final[frozenset[str]] = frozenset(
    {"PASS", "IGNORE", "INFO", "WARN", "FAIL"}
)
_SUPPRESSING_ACTIONS: Final[frozenset[str]] = frozenset({"IGNORE"})
_TSV_FIELD_COUNT: Final[int] = 3

# A DAST Tuning table row: | <name> | <rule id> | <action> | <rationale> |
_DOCS_ROW_RE: Final[re.Pattern[str]] = re.compile(
    r"^\|([^|]*)\|\s*(\d+)\s*\|([^|]*)\|(.*)\|\s*$"
)

_REMEDIATION: Final[str] = (
    "Every IGNORE row in .github/zap-rules.tsv needs a row in the DAST "
    "Tuning table in docs/security.md, with the same action and a "
    "rationale saying what trips the rule, why it is not a finding, and "
    "what still catches a real regression."
)


class Finding(NamedTuple):
    """One disagreement between the two files, or one malformed row."""

    location: str
    message: str


class DeclaredRule(NamedTuple):
    """A rule as ``.github/zap-rules.tsv`` declares it.

    ``action`` is validated against :data:`_VALID_ACTIONS` before this
    is constructed, so it is always one of the ZAP vocabulary.
    """

    action: str
    line_number: int


class DocumentedRule(NamedTuple):
    """A rule as the ``docs/security.md`` table documents it.

    Deliberately a separate type from :class:`DeclaredRule` despite the
    identical shape: ``action`` here is whatever prose the table holds,
    upper-cased but never checked against the vocabulary (an unknown one
    surfaces as a mismatch against the declaration instead), and
    ``line_number`` indexes a different file. Sharing one type invites a
    report that cites a docs line against the rules path.
    """

    action: str
    line_number: int


def _parse_rules(text: str) -> tuple[dict[str, DeclaredRule], list[Finding]]:
    """Parse the rules file.

    Returns:
        The declared rules by id, and a finding per malformed row.
    """
    rules: dict[str, DeclaredRule] = {}
    findings: list[Finding] = []
    for line_number, raw in enumerate(text.splitlines(), start=1):
        line = raw.rstrip("\n")
        if not line.strip() or line.lstrip().startswith("#"):
            continue

        fields = line.split("\t")
        if len(fields) != _TSV_FIELD_COUNT:
            findings.append(
                Finding(
                    f"{_RULES_RELATIVE}:{line_number}",
                    f"expected {_TSV_FIELD_COUNT} tab-separated fields, "
                    f"found {len(fields)}: {line!r}",
                )
            )
            continue

        rule_id, action, _description = (field.strip() for field in fields)
        if action not in _VALID_ACTIONS:
            findings.append(
                Finding(
                    f"{_RULES_RELATIVE}:{line_number}",
                    f"action {action!r} is not one of "
                    f"{', '.join(sorted(_VALID_ACTIONS))}",
                )
            )
            continue

        previous = rules.get(rule_id)
        if previous is not None:
            findings.append(
                Finding(
                    f"{_RULES_RELATIVE}:{line_number}",
                    f"rule {rule_id} already declared at line "
                    f"{previous.line_number}; one rule, one action",
                )
            )
            continue

        rules[rule_id] = DeclaredRule(action, line_number)
    return rules, findings


def _parse_docs(text: str) -> tuple[dict[str, DocumentedRule], list[Finding]]:
    """Parse the DAST Tuning table.

    Rows are matched by shape rather than by locating the table, so a
    heading rename cannot quietly empty the comparison. :func:`check`
    holds the floor that makes an empty result loud.

    Returns:
        The documented rules by id, and a finding per duplicate or
        unjustified row.
    """
    documented: dict[str, DocumentedRule] = {}
    findings: list[Finding] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        match = _DOCS_ROW_RE.match(line.strip())
        if match is None:
            continue

        rule_id = match.group(2)
        action = match.group(3).strip().upper()
        rationale = match.group(4).strip()

        previous = documented.get(rule_id)
        if previous is not None:
            findings.append(
                Finding(
                    f"{_DOCS_RELATIVE}:{line_number}",
                    f"rule {rule_id} already documented at line {previous.line_number}",
                )
            )
            continue

        if action in _SUPPRESSING_ACTIONS and not rationale:
            findings.append(
                Finding(
                    f"{_DOCS_RELATIVE}:{line_number}",
                    f"rule {rule_id} is suppressed with an empty rationale",
                )
            )

        documented[rule_id] = DocumentedRule(action, line_number)
    return documented, findings


def _reconcile(
    rules: dict[str, DeclaredRule],
    documented: dict[str, DocumentedRule],
) -> list[Finding]:
    """Compare the two parsed views in both directions.

    Returns:
        A finding per rule the two files disagree about.
    """
    findings: list[Finding] = []
    for rule_id, declared in sorted(rules.items()):
        if declared.action not in _SUPPRESSING_ACTIONS:
            continue
        if rule_id not in documented:
            findings.append(
                Finding(
                    f"{_RULES_RELATIVE}:{declared.line_number}",
                    f"rule {rule_id} is suppressed but has no row in the "
                    f"DAST Tuning table in {_DOCS_RELATIVE}",
                )
            )

    for rule_id, row in sorted(documented.items()):
        counterpart = rules.get(rule_id)
        if counterpart is None:
            findings.append(
                Finding(
                    f"{_DOCS_RELATIVE}:{row.line_number}",
                    f"rule {rule_id} is documented but {_RULES_RELATIVE} "
                    f"does not declare it",
                )
            )
        elif counterpart.action != row.action:
            findings.append(
                Finding(
                    f"{_DOCS_RELATIVE}:{row.line_number}",
                    f"rule {rule_id} is documented as {row.action} but "
                    f"declared as {counterpart.action}",
                )
            )
    return findings


def _read(repo_root: Path, relative: Path) -> tuple[str | None, list[Finding]]:
    """Read one input file.

    Returns:
        Its text (``None`` when missing or unreadable), and a finding
        when it could not be read.
    """
    try:
        return (repo_root / relative).read_text(encoding="utf-8"), []
    except OSError as exc:
        return None, [Finding(str(relative), f"cannot read: {type(exc).__name__}")]


def check(repo_root: Path) -> list[Finding]:
    """Return every disagreement between the rules file and the docs table.

    Returns:
        Findings, empty when the two files agree.
    """
    rules_text, rules_read_findings = _read(repo_root, _RULES_RELATIVE)
    docs_text, docs_read_findings = _read(repo_root, _DOCS_RELATIVE)
    findings = [*rules_read_findings, *docs_read_findings]
    if rules_text is None or docs_text is None:
        return findings

    rules, rule_findings = _parse_rules(rules_text)
    documented, doc_findings = _parse_docs(docs_text)
    findings += [*rule_findings, *doc_findings]

    if not documented:
        findings.append(
            Finding(
                str(_DOCS_RELATIVE),
                "parsed no rows from the DAST Tuning table; it has been "
                "renamed, reformatted or removed, so nothing here was "
                "actually compared",
            )
        )

    return findings + _reconcile(rules, documented)


def main(argv: list[str] | None = None) -> int:
    """Run the gate; return the process exit code.

    Returns:
        ``0`` when the two files agree, ``1`` otherwise.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=_REPO_ROOT_DEFAULT,
        help="Repository root to check (defaults to this checkout).",
    )
    args = parser.parse_args(argv)

    findings = check(args.repo_root)
    if not findings:
        return 0

    print("ZAP rule suppressions are not documented consistently:", file=sys.stderr)
    for finding in findings:
        print(f"  {finding.location}: {finding.message}", file=sys.stderr)
    print(f"\n{_REMEDIATION}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
