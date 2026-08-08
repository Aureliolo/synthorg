#!/usr/bin/env python3
"""Gate: every rule the vale Google package ships is classified in ``.vale.ini``.

The prose gate's exit code reflects error-severity alerts only, and every
rule the Google package ships is a warning or a suggestion. So a rule that
appears in the package but nowhere in ``.vale.ini`` runs at its shipped
severity: it prints its findings and lets the push and the CI job through.
That is precisely how the whole package came to report hundreds of findings
on a green job, and the ``= error`` ledger only closes it for the rules that
existed when the ledger was written.

Nothing else re-checks it. ``.vale.ini`` pins the package to a release URL
and a Renovate custom manager raises each new release as a PR, but a bump
that ships a brand-new rule is green by construction: the new rule cannot
fail anything, so CI has no way to say "you have not triaged this yet".
Removing the version hold made that path automatic, which is why the check
has to exist rather than being a review habit.

Classification means one of exactly two dispositions, both already the
file's own idiom:

* ``Google.<Rule> = NO`` -- disabled, with the "Why:" comment the ledger
  requires above it;
* ``Google.<Rule> = error`` -- kept and blocking.

Anything else, including leaving the rule out entirely or assigning it a
non-blocking severity, is the fail-open state this gate rejects.

A project rule under ``.vale/styles/<Style>/`` that replaces a disabled
upstream one is checked the same way, so a replacement cannot be added and
left unregistered in ``BasedOnStyles``.

The synced package is gitignored, so the check needs it materialised. When
``.vale/styles/Google/`` is absent the gate says so and exits non-zero
rather than passing vacuously: a check that quietly skips when its input is
missing is the same class of defect it exists to catch. Run
``bash scripts/install_cli_tools.sh vale`` to materialise it.

Exit codes:
    0 -- every shipped rule is classified.
    1 -- a rule is unclassified, a ledger entry names a rule the package no
         longer ships, or the package is not materialised.
"""

import re
import sys
from pathlib import Path
from typing import Final

_REPO_ROOT: Final[Path] = Path(__file__).resolve().parent.parent
_VALE_CONFIG: Final[Path] = _REPO_ROOT / ".vale.ini"
_STYLES_DIR: Final[Path] = _REPO_ROOT / ".vale" / "styles"
_UPSTREAM_STYLE: Final[str] = "Google"

# A ledger line: ``Google.RuleName = <disposition>``. The disposition is
# captured loosely rather than restricted to NO|error, so a line assigning
# anything else is caught and named by the validity check below instead of
# failing to match and being reported as if the rule were simply absent.
_LEDGER_ENTRY: Final[re.Pattern[str]] = re.compile(
    r"^\s*(?P<style>[A-Za-z][A-Za-z0-9]*)\.(?P<rule>[A-Za-z][A-Za-z0-9]*)"
    r"\s*=\s*(?P<disposition>\S+)\s*$",
    re.MULTILINE,
)

_BASED_ON_STYLES: Final[re.Pattern[str]] = re.compile(
    r"^\s*BasedOnStyles\s*=\s*(?P<styles>.+?)\s*$", re.MULTILINE
)

_ACCEPTED_DISPOSITIONS: Final[frozenset[str]] = frozenset({"NO", "error"})


def _shipped_rules(style: str) -> set[str]:
    """Return the rule names a style directory ships, by filename."""
    style_dir = _STYLES_DIR / style
    return {path.stem for path in style_dir.glob("*.yml")}


def _ledger(config_text: str) -> dict[tuple[str, str], str]:
    """Return ``{(style, rule): disposition}`` for every ledger line."""
    return {
        (match["style"], match["rule"]): match["disposition"]
        for match in _LEDGER_ENTRY.finditer(config_text)
    }


def _declared_styles(config_text: str) -> set[str]:
    """Return the styles named by ``BasedOnStyles``."""
    match = _BASED_ON_STYLES.search(config_text)
    if match is None:
        return set()
    return {name.strip() for name in match["styles"].split(",") if name.strip()}


def _style_failures(
    style: str, ledger: dict[tuple[str, str], str]
) -> tuple[list[str], int]:
    """Return the classification failures for a style, plus its shipped count."""
    shipped = _shipped_rules(style)
    classified = {rule for (owner, rule) in ledger if owner == style}
    failures = [
        f"{style}.{rule} is shipped by the style package but is neither "
        f"disabled ('{style}.{rule} = NO') nor kept ('{style}.{rule} = "
        f"error') in .vale.ini. Left unclassified it runs at its shipped "
        f"severity, which never fails the gate."
        for rule in sorted(shipped - classified)
    ]
    failures.extend(
        f"{style}.{rule} is classified in .vale.ini but the style package "
        f"no longer ships it. Drop the stale ledger line."
        for rule in sorted(classified - shipped)
    )
    return failures, len(shipped)


def _entry_failures(
    ledger: dict[tuple[str, str], str], declared: set[str]
) -> list[str]:
    """Return one failure per malformed disposition or unloaded style."""
    failures: list[str] = []
    for (style, rule), disposition in sorted(ledger.items()):
        if disposition not in _ACCEPTED_DISPOSITIONS:
            failures.append(
                f"{style}.{rule} = {disposition} is neither 'NO' nor 'error'. "
                f"Only those two block; any other severity reports and lets "
                f"the push through."
            )
        if style not in declared:
            failures.append(
                f"{style}.{rule} is classified in .vale.ini but '{style}' is "
                f"not in BasedOnStyles, so the rule never loads."
            )
    return failures


def main() -> int:
    """Check the ledger against the styles actually on disk."""
    config_text = _VALE_CONFIG.read_text(encoding="utf-8")

    if not (_STYLES_DIR / _UPSTREAM_STYLE).is_dir():
        print(
            f"error: {_UPSTREAM_STYLE} style package is not materialised at "
            f"{_STYLES_DIR / _UPSTREAM_STYLE}",
            file=sys.stderr,
        )
        print(
            "       run 'bash scripts/install_cli_tools.sh vale' first; this gate "
            "refuses to pass without the package rather than skip silently",
            file=sys.stderr,
        )
        return 1

    ledger = _ledger(config_text)
    declared = _declared_styles(config_text)

    # Project styles are tracked, so any style in BasedOnStyles that has a
    # directory is checked. Vale itself is the built-in style (Vale.Terms,
    # Vale.Avoid, Vale.Spelling), which ships no rule files to enumerate.
    checkable = sorted(style for style in declared if (_STYLES_DIR / style).is_dir())

    failures: list[str] = []
    shipped_totals: dict[str, int] = {}
    for style in checkable:
        style_failures, shipped_totals[style] = _style_failures(style, ledger)
        failures.extend(style_failures)
    failures.extend(_entry_failures(ledger, declared))

    if failures:
        print("vale ledger is incomplete:", file=sys.stderr)
        for failure in failures:
            print(f"  - {failure}", file=sys.stderr)
        print(
            "\nEvery rule the package ships must be triaged into the .vale.ini "
            "ledger, disabled with a reason or kept at error severity.",
            file=sys.stderr,
        )
        return 1

    print(
        f"vale ledger complete: {sum(shipped_totals.values())} rules across "
        f"{', '.join(checkable)} all classified"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
