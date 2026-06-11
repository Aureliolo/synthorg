"""Extract user-facing strings from the React dashboard into an i18n catalog.

Intermediate artefact for the i18n infrastructure work tracked in #1417.
Walks ``web/src/**/*.{ts,tsx}`` with a conservative regex pass that
captures three surface areas:

1. JSX text nodes at least 2 characters long (``>Text</``).
2. ``aria-label`` / ``aria-description`` / ``placeholder`` / ``title``
   / ``alt`` attribute string literals (``aria-label="..."``).
3. Toast ``title`` / ``description`` string-literal arguments.

The regex approach deliberately misses dynamic values -- those have to
be handled once a real i18n loader lands. The point is to seed a catalog
for curation, not to produce a drop-in translation bundle.

Output: ``web/src/i18n/_extracted_catalog.json`` (gitignored; intended
for one-shot hand-off, not a runtime dependency).

Usage:

    uv run python scripts/extract_web_strings.py

Idempotent: safe to re-run. Overwrites the output file.
"""

import json
import re
import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterator

_MIN_STRING_LENGTH = 2

_ROOT = Path(__file__).resolve().parent.parent
_SRC = _ROOT / "web" / "src"
_OUT = _SRC / "i18n" / "_extracted_catalog.json"

# JSX text node: open-tag close, whitespace, then a run that must contain
# at least one letter (filters out pure punctuation like ``>+<``).
_JSX_TEXT = re.compile(r">\s*([A-Za-z][^<>{}\n]{1,199}?)\s*<")

# Attribute string literals for accessibility-facing attributes.
_ATTR_KEYS: tuple[str, ...] = (
    "aria-label",
    "aria-description",
    "placeholder",
    "title",
    "alt",
)
_ATTR_RE = re.compile(
    r"\b(?P<key>"
    + "|".join(re.escape(k) for k in _ATTR_KEYS)
    + r')="([^"\n]{2,199}?)"',
)

# Toast payload fields `title: 'foo'` / `description: 'foo'` with single
# or double quotes. Keeps the regex narrow by requiring the `{` or `,`
# context so random object keys elsewhere don't match.
_TOAST_RE = re.compile(
    r"[{,]\s*(?P<key>title|description)\s*:\s*['\"]([^'\"\n]{2,199}?)['\"]",
)

# Entries that look like identifiers, icon names, or HTML/CSS tokens get
# dropped -- i18n only needs natural-language strings.
_SKIP_TOKEN = re.compile(r"^[a-z][a-z0-9_.-]*$|^[A-Z][A-Z0-9_-]+$|^#\w|^rgb")


def _should_include(text: str) -> bool:
    stripped = text.strip()
    if len(stripped) < _MIN_STRING_LENGTH:
        return False
    if _SKIP_TOKEN.match(stripped):
        return False
    # Require at least one alphabetic character; filters out numbers-only.
    return any(c.isalpha() for c in stripped)


def _iter_sources() -> Iterator[Path]:
    for path in _SRC.rglob("*.tsx"):
        if "__tests__" in path.parts or ".stories." in path.name:
            continue
        yield path
    for path in _SRC.rglob("*.ts"):
        if "__tests__" in path.parts or ".stories." in path.name:
            continue
        yield path


def _scan(path: Path) -> Iterator[dict[str, object]]:
    try:
        content = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        # Surface unreadable sources (permissions, disk errors,
        # non-UTF-8 encodings) on stderr so the operator sees which
        # files were skipped instead of silently getting a partial
        # catalog. The script is a one-shot seed -- this is a plain
        # ``print`` to stderr rather than structured logging because
        # the script has no persistence-backed observability stack.
        print(
            f"extract_web_strings: skipping unreadable file "
            f"{path.relative_to(_ROOT).as_posix()}: "
            f"{type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        return
    rel = path.relative_to(_ROOT).as_posix()

    for match in _JSX_TEXT.finditer(content):
        text = match.group(1).strip()
        if not _should_include(text):
            continue
        line = content[: match.start()].count("\n") + 1
        yield {
            "file": rel,
            "line": line,
            "kind": "jsx-text",
            "text": text,
        }

    for match in _ATTR_RE.finditer(content):
        text = match.group(2).strip()
        if not _should_include(text):
            continue
        line = content[: match.start()].count("\n") + 1
        yield {
            "file": rel,
            "line": line,
            "kind": f"attr:{match.group('key')}",
            "text": text,
        }

    for match in _TOAST_RE.finditer(content):
        text = match.group(2).strip()
        if not _should_include(text):
            continue
        line = content[: match.start()].count("\n") + 1
        yield {
            "file": rel,
            "line": line,
            "kind": f"toast:{match.group('key')}",
            "text": text,
        }


def main() -> int:
    """Walk the web source tree and emit ``_extracted_catalog.json``."""
    entries: list[dict[str, object]] = []
    for path in _iter_sources():
        entries.extend(_scan(path))

    # Sort for deterministic output.
    def _sort_key(entry: dict[str, object]) -> tuple[str, int]:
        line = entry.get("line", 0)
        return (str(entry.get("file", "")), line if isinstance(line, int) else 0)

    entries.sort(key=_sort_key)

    _OUT.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_at": None,
        "source": "scripts/extract_web_strings.py",
        "handoff_issue": 1417,
        "entry_count": len(entries),
        "note": (
            "Regex-based extraction; dynamic values and template "
            "literals are intentionally missed. Run again after "
            "adding an i18n loader to refresh. Gitignored."
        ),
        "catalog": entries,
    }
    _OUT.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(f"Wrote {len(entries)} entries to {_OUT.relative_to(_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
