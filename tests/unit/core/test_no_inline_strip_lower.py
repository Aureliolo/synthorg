"""Convention guard: keep raw ``.strip().lower()`` count under control.

After A3-P4, every functional ``.strip().lower()`` site was migrated to a
named helper in :mod:`synthorg.core.normalization`. The remaining hits in
``src/synthorg/`` live inside the helper bodies themselves and their
docstrings; that count is allowed to grow modestly without regression
risk. New consumer-side ``.strip().lower()`` should go through a helper.
"""

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

# Acceptance bar from #1888: drop below 5 in src/synthorg/.
# Current state is zero; this threshold guards against any new
# consumer-side inline ``.strip().lower()`` leaking back in.
_MAX_INLINE_HITS: int = 4

_PATTERN = re.compile(r"\.strip\(\)\.lower\(\)|\.lower\(\)\.strip\(\)")


def _src_root() -> Path:
    here = Path(__file__).resolve()
    for parent in (here, *here.parents):
        candidate = parent / "src" / "synthorg"
        if candidate.is_dir():
            return candidate
    msg = "Could not locate src/synthorg/ from test path"
    raise RuntimeError(msg)


def test_inline_strip_lower_count_stays_below_threshold() -> None:
    """Inline ``.strip().lower()`` count must stay below the threshold.

    If this test fires, route the new site through a helper in
    ``synthorg.core.normalization`` (``normalize_ascii_lowercase``,
    ``normalize_ascii_lowercase_or_default``, ``extract_media_type``,
    ``extract_bearer_token``, or ``collapse_whitespace_lowercase``).
    """
    src_root = _src_root()
    hits: list[str] = []
    for path in src_root.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for line_no, line in enumerate(text.splitlines(), start=1):
            if _PATTERN.search(line):
                hits.append(f"{path.relative_to(src_root)}:{line_no}: {line.strip()}")

    actual = len(hits)
    assert actual <= _MAX_INLINE_HITS, (
        f"{actual} inline `.strip().lower()` sites under src/synthorg/ "
        f"(limit {_MAX_INLINE_HITS}). Route new sites through "
        f"synthorg.core.normalization helpers. Hits:\n" + "\n".join(hits)
    )
