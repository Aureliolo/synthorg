"""Design system validation for web dashboard files.

Checks web/src/ files for design system violations and proposes shared
building blocks when duplicate patterns are detected. Runs as a
PostToolUse hook on Edit/Write operations targeting web/src/ files.

When invoked as a hook, reads JSON from stdin with the tool input.
Also supports direct CLI invocation for testing.

Exit codes:
    0 -- no issues found, file not in web/src/, or file could not be read
         (warnings printed to stderr for read/parse failures)
    1 -- violations found (prints warnings to stdout)

Usage (hook mode -- reads JSON from stdin):
    echo '{"tool_input":{"file_path":"web/src/pages/Foo.tsx"}}' |
        python scripts/check_web_design_system.py

Usage (CLI mode -- for testing):
    python scripts/check_web_design_system.py <file_path> [--project-root <root>]
"""

import argparse
import json
import re
import sys
from pathlib import Path

# When invoked as a script, the ``scripts`` directory itself is the
# entry-point directory (not a package), so ``from scripts.<x>``
# fails. Add the enclosing ``scripts/`` folder to ``sys.path`` so the
# sibling pattern module is importable both when run standalone
# (PostToolUse hook) and when imported by tests (``scripts.`` prefix).
if __package__ in {None, ""}:  # standalone invocation
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from _web_design_patterns import (  # type: ignore[import-not-found]
        _BLOCK_COMMENT_RE,
        _COMMENT_PREFIXES,
        _CURRENCY_SKIP_PATHS,
        _LOCALE_SKIP_PATHS,
        _LONG_HEX_ALPHA_LEN,
        _MAP_BLOCK_COMPLEXITY,
        _MIN_LINE_NUM_FOR_PRECEDING,
        _MIN_PATH_DEPTH,
        _MOTION_DURATION_SKIP_PATHS,
        _REGIONAL_SUPPRESSION_MARKER,
        _REPEATED_PATTERN_MIN,
        _SHORT_HEX_ALPHA_LEN,
        _SHORT_HEX_LEN,
        _SKIP_DIRS,
        _SKIP_PATHS,
        ALLOWED_HEX_COLORS,
        BARE_TOLOCALE_RE,
        HARDCODED_COLOR_RE,
        HARDCODED_CURRENCY_RE,
        HARDCODED_CURRENCY_SYMBOL_RE,
        HARDCODED_FONT_RE,
        HARDCODED_LOCALE_RE,
        HARDCODED_MOTION_DURATION_RE,
        HARDCODED_RGBA_RE,
        HARDCODED_USD_FIELD_RE,
        LOCALE_USAGE_RE,
    )
else:
    from scripts._web_design_patterns import (
        _BLOCK_COMMENT_RE,
        _COMMENT_PREFIXES,
        _CURRENCY_SKIP_PATHS,
        _LOCALE_SKIP_PATHS,
        _LONG_HEX_ALPHA_LEN,
        _MAP_BLOCK_COMPLEXITY,
        _MIN_LINE_NUM_FOR_PRECEDING,
        _MIN_PATH_DEPTH,
        _MOTION_DURATION_SKIP_PATHS,
        _REGIONAL_SUPPRESSION_MARKER,
        _REPEATED_PATTERN_MIN,
        _SHORT_HEX_ALPHA_LEN,
        _SHORT_HEX_LEN,
        _SKIP_DIRS,
        _SKIP_PATHS,
        ALLOWED_HEX_COLORS,
        BARE_TOLOCALE_RE,
        HARDCODED_COLOR_RE,
        HARDCODED_CURRENCY_RE,
        HARDCODED_CURRENCY_SYMBOL_RE,
        HARDCODED_FONT_RE,
        HARDCODED_LOCALE_RE,
        HARDCODED_MOTION_DURATION_RE,
        HARDCODED_RGBA_RE,
        HARDCODED_USD_FIELD_RE,
        LOCALE_USAGE_RE,
    )


def _marker_in_comment(line: str) -> bool:
    """Return True when the regional-suppression marker is inside a comment.

    The marker may appear in string literals (test fixtures, docs about
    the lint rule, etc.), and those must NOT silently disable the check.
    We require the marker to be preceded by a ``//``/``/*``/``*`` comment
    opener on the same line, using :func:`_is_in_comment_context` which
    already tracks string boundaries.
    """
    pos = line.find(_REGIONAL_SUPPRESSION_MARKER)
    return pos >= 0 and _is_in_comment_context(line, pos)


def _is_regional_suppressed(lines: list[str], line_num: int) -> bool:
    """Return True when line ``line_num`` opts out of regional checks.

    Checks the same line and the immediately preceding line for the
    ``_REGIONAL_SUPPRESSION_MARKER`` string inside any comment.  A
    marker embedded in a string literal does not suppress the check.
    """
    if line_num < 1 or line_num > len(lines):
        return False
    if _marker_in_comment(lines[line_num - 1]):
        return True
    return line_num >= _MIN_LINE_NUM_FOR_PRECEDING and _marker_in_comment(
        lines[line_num - 2]
    )


def _normalize_hex(h: str) -> str:
    """Normalize hex color to lowercase 6-digit form, stripping alpha."""
    h = h.lower()
    if len(h) == _SHORT_HEX_LEN:  # #rgb -> #rrggbb
        return f"#{h[1] * 2}{h[2] * 2}{h[3] * 2}"
    if len(h) == _SHORT_HEX_ALPHA_LEN:  # #rgba -> #rrggbb (drop alpha)
        return f"#{h[1] * 2}{h[2] * 2}{h[3] * 2}"
    if len(h) == _LONG_HEX_ALPHA_LEN:  # #rrggbbaa -> #rrggbb (drop alpha)
        return h[:7]
    return h


def _is_allowed_file(file_path: Path, project_root: Path) -> bool:
    """Check if the file should be validated."""
    if not file_path.is_relative_to(project_root):
        return False
    rel = file_path.relative_to(project_root)
    parts = rel.parts

    if file_path.name in _SKIP_PATHS:
        return False
    if any(skip_dir in parts for skip_dir in _SKIP_DIRS):
        return False
    if not (len(parts) >= _MIN_PATH_DEPTH and parts[0] == "web"):
        return False
    return file_path.suffix in {".tsx", ".ts", ".css"}


def _is_in_comment_context(line: str, match_start: int) -> bool:
    """Rough heuristic: skip matches inside single-line comments.

    Tracks string boundaries for both ``//`` and ``/*`` detection.
    Does not handle multi-line block comments or double-escaped
    backslashes -- sufficient for typical JSX/TSX patterns.
    """
    prefix = line[:match_start]
    in_string = False
    for i, ch in enumerate(prefix):
        if ch in ("'", '"', "`") and (i == 0 or prefix[i - 1] != "\\"):
            in_string = not in_string
        if not in_string and prefix[i : i + 2] == "//":
            return True
    if not in_string and "/*" in prefix:
        last_open = prefix.rfind("/*")
        return "*/" not in prefix[last_open:]
    return False


def check_hardcoded_colors(
    content: str,
    file_path: Path,
    project_root: Path,
) -> list[str]:
    """Find hardcoded color values that should use design tokens."""
    warnings: list[str] = []
    rel_path = file_path.relative_to(project_root)

    for line_num, line in enumerate(content.splitlines(), 1):
        stripped = line.strip()
        if stripped.startswith(_COMMENT_PREFIXES):
            continue

        for m in HARDCODED_COLOR_RE.finditer(line):
            hex_val = m.group("tw_hex") or m.group("css_hex") or m.group("jsx_hex")
            if (
                hex_val
                and _normalize_hex(hex_val) not in ALLOWED_HEX_COLORS
                and not _is_in_comment_context(line, m.start())
            ):
                warnings.append(
                    f"  {rel_path}:{line_num}: Hardcoded color `{hex_val}` "
                    f"-- use a design token (--so-*) or Tailwind semantic class instead.\n"
                    f"    {stripped}"
                )

        for m in HARDCODED_RGBA_RE.finditer(line):
            if _is_in_comment_context(line, m.start()):
                continue
            # Skip rgba() inside a var() fallback (unclosed var( before match)
            before = line[: m.start()]
            var_pos = before.rfind("var(")
            if var_pos >= 0:
                between = before[var_pos : m.start()]
                if between.count("(") > between.count(")"):
                    continue
            warnings.append(
                f"  {rel_path}:{line_num}: Hardcoded rgba() "
                f"-- use a design token variable instead.\n"
                f"    {stripped}"
            )

    return warnings


def check_hardcoded_fonts(
    content: str,
    file_path: Path,
    project_root: Path,
) -> list[str]:
    """Find hardcoded font-family declarations."""
    warnings: list[str] = []
    rel_path = file_path.relative_to(project_root)

    for line_num, line in enumerate(content.splitlines(), 1):
        stripped = line.strip()
        if stripped.startswith(_COMMENT_PREFIXES):
            continue

        warnings.extend(
            f"  {rel_path}:{line_num}: Hardcoded font-family "
            f"-- use `font-sans` or `font-mono` (maps to Geist tokens).\n"
            f"    {stripped}"
            for m in HARDCODED_FONT_RE.finditer(line)
            if not _is_in_comment_context(line, m.start())
        )

    return warnings


def check_hardcoded_motion_transitions(
    content: str,
    file_path: Path,
    project_root: Path,
) -> list[str]:
    """Find hardcoded Motion transition durations.

    Components should use presets from ``lib/motion.ts`` (e.g.
    ``tweenDefault``, ``tweenFast``, ``tweenExitFast``) or the
    ``useAnimationPreset()`` hook instead of inline duration values.
    """
    rel_str = file_path.relative_to(project_root).as_posix()
    if rel_str in _MOTION_DURATION_SKIP_PATHS:
        return []
    if ".stories." in file_path.name:
        return []
    if file_path.suffix not in {".tsx", ".ts"}:
        return []

    warnings: list[str] = []
    rel_path = file_path.relative_to(project_root)
    lines = content.splitlines()

    # Mask block comments with spaces (preserve newlines) so the regex
    # won't match inside /* ... */ while keeping offsets aligned with content.
    stripped = _BLOCK_COMMENT_RE.sub(
        lambda cm: "".join(" " if c != "\n" else "\n" for c in cm.group()),
        content,
    )

    # Run regex on masked content so multiline transition objects are caught.
    for m in HARDCODED_MOTION_DURATION_RE.finditer(stripped):
        line_num = stripped[: m.start()].count("\n") + 1
        col = m.start() - stripped.rfind("\n", 0, m.start()) - 1
        original_line = lines[line_num - 1]
        line_text = original_line.strip()
        if line_text.startswith(_COMMENT_PREFIXES):
            continue
        if _is_in_comment_context(original_line, col):
            continue
        warnings.append(
            f"  {rel_path}:{line_num}: Hardcoded Motion duration "
            f"-- use a preset from `@/lib/motion` or "
            f"`useAnimationPreset()` hook.\n"
            f"    {line_text}"
        )

    return warnings


def _locate(stripped: str, lines: list[str], start: int) -> tuple[int, int, str, str]:
    """Resolve a match offset to (line_num, col, original_line, line_text)."""
    line_num = stripped[:start].count("\n") + 1
    col = start - stripped.rfind("\n", 0, start) - 1
    original_line = lines[line_num - 1]
    return line_num, col, original_line, original_line.strip()


def _collect_hardcoded_locale_literals(
    stripped: str,
    lines: list[str],
    rel_path: Path,
) -> list[str]:
    """Flag BCP 47 literals (matches already come from Intl-using files)."""
    warnings: list[str] = []
    for m in HARDCODED_LOCALE_RE.finditer(stripped):
        line_num, col, original_line, line_text = _locate(stripped, lines, m.start())
        if line_text.startswith(_COMMENT_PREFIXES):
            continue
        if _is_in_comment_context(original_line, col):
            continue
        warnings.append(
            f"  {rel_path}:{line_num}: Hardcoded locale `{m.group('locale')}` "
            f"-- use a helper from `@/utils/format` (reads `getLocale()`) "
            f"or pass the locale explicitly via the helper parameter.\n"
            f"    {line_text}"
        )
    return warnings


def _collect_bare_tolocale_calls(
    stripped: str,
    lines: list[str],
    rel_path: Path,
) -> list[str]:
    """Flag ``.toLocale*String()`` calls that omit an explicit locale."""
    warnings: list[str] = []
    for m in BARE_TOLOCALE_RE.finditer(stripped):
        line_num, col, original_line, line_text = _locate(stripped, lines, m.start())
        if line_text.startswith(_COMMENT_PREFIXES):
            continue
        if _is_in_comment_context(original_line, col):
            continue
        warnings.append(
            f"  {rel_path}:{line_num}: Bare `.toLocaleString()` call "
            f"-- use `formatNumber`, `formatDateTime`, `formatDateOnly`, "
            f"`formatTime`, `formatTokenCount`, or another helper from "
            f"`@/utils/format` so output is deterministic and locale-aware.\n"
            f"    {line_text}"
        )
    return warnings


def check_hardcoded_locale(
    content: str,
    file_path: Path,
    project_root: Path,
) -> list[str]:
    """Find hardcoded BCP 47 locale literals and bare ``.toLocale*String`` calls.

    Formatters live in ``@/utils/format`` and accept an optional
    ``locale?: string`` parameter that defaults to ``getLocale()``.
    Hardcoded ``'en-US'`` literals and locale-less ``.toLocaleString()``
    calls bypass the i18n-ready pipeline. Any BCP 47 literal in a file
    that uses ``Intl.*`` or ``.toLocale*String(...)`` anywhere is treated
    as suspicious (catches ``const locale = 'en-US'; Intl.X(locale)``
    patterns the previous heuristic missed).
    """
    rel_str = file_path.relative_to(project_root).as_posix()
    if rel_str in _LOCALE_SKIP_PATHS:
        return []
    if file_path.suffix not in {".tsx", ".ts"}:
        return []
    if not LOCALE_USAGE_RE.search(content):
        return []

    rel_path = file_path.relative_to(project_root)
    lines = content.splitlines()
    stripped = _BLOCK_COMMENT_RE.sub(
        lambda cm: "".join(" " if c != "\n" else "\n" for c in cm.group()),
        content,
    )

    warnings = _collect_hardcoded_locale_literals(stripped, lines, rel_path)
    warnings.extend(_collect_bare_tolocale_calls(stripped, lines, rel_path))
    return warnings


def check_hardcoded_currency(
    content: str,
    file_path: Path,
    project_root: Path,
) -> list[str]:
    """Flag hardcoded ISO 4217 currency codes outside the allowlist.

    The dashboard renders every money value in the operator's
    configured currency (``useSettingsStore().currency``, defaulting
    to ``DEFAULT_CURRENCY`` from ``@/utils/currencies``). Hardcoding
    ``'USD'`` / ``'EUR'`` / etc. bakes a specific currency into the
    rendered output and contradicts the runtime-resolved pattern.
    """
    rel_str = file_path.relative_to(project_root).as_posix()
    if rel_str in _CURRENCY_SKIP_PATHS:
        return []
    if file_path.suffix not in {".ts", ".tsx"}:
        return []

    rel_path = file_path.relative_to(project_root)
    lines = content.splitlines()
    stripped = _BLOCK_COMMENT_RE.sub(
        lambda cm: "".join(" " if c != "\n" else "\n" for c in cm.group()),
        content,
    )

    warnings: list[str] = []
    for m in HARDCODED_CURRENCY_RE.finditer(stripped):
        line_num, col, original_line, line_text = _locate(stripped, lines, m.start())
        if line_text.startswith(_COMMENT_PREFIXES):
            continue
        if _is_in_comment_context(original_line, col):
            continue
        if _is_regional_suppressed(lines, line_num):
            continue
        warnings.append(
            f"  {rel_path}:{line_num}: Hardcoded currency code `{m.group('currency')}` "
            f"-- import `DEFAULT_CURRENCY` from `@/utils/currencies` or read "
            f"`useSettingsStore().currency`.\n"
            f"    {line_text}",
        )
    return warnings


def check_hardcoded_currency_symbol(
    content: str,
    file_path: Path,
    project_root: Path,
) -> list[str]:
    """Flag currency symbols adjacent to digits/expressions in string literals.

    ``"$10"`` / ``"\u20ac50"`` / ``` `$${value}` ``` bake a regional symbol
    into rendered output. Use ``formatCurrency(value, DEFAULT_CURRENCY)``
    or ``formatCurrencyCompact`` from ``@/utils/format`` instead.

    Skip files under ``__tests__`` (test fixtures legitimately include
    currency symbols in setup strings) to match the file-level skip in
    :func:`check_file`.
    """
    if file_path.suffix not in {".ts", ".tsx"}:
        return []
    if any(skip in file_path.relative_to(project_root).parts for skip in _SKIP_DIRS):
        return []

    rel_path = file_path.relative_to(project_root)
    lines = content.splitlines()
    stripped = _BLOCK_COMMENT_RE.sub(
        lambda cm: "".join(" " if c != "\n" else "\n" for c in cm.group()),
        content,
    )

    warnings: list[str] = []
    for m in HARDCODED_CURRENCY_SYMBOL_RE.finditer(stripped):
        line_num, col, original_line, line_text = _locate(stripped, lines, m.start())
        if line_text.startswith(_COMMENT_PREFIXES):
            continue
        if _is_in_comment_context(original_line, col):
            continue
        if _is_regional_suppressed(lines, line_num):
            continue
        warnings.append(
            f"  {rel_path}:{line_num}: Hardcoded currency symbol with value "
            f"-- route through `formatCurrency(value, DEFAULT_CURRENCY)` or "
            f"`formatCurrencyCompact` from `@/utils/format`.\n"
            f"    {line_text}",
        )
    return warnings


def check_usd_field_names(
    content: str,
    file_path: Path,
    project_root: Path,
) -> list[str]:
    """Flag identifiers with a ``_usd`` suffix.

    Money fields carry the operator's configured currency; the field
    name should not encode a specific currency. Rename to the neutral
    form (e.g. ``cost_usd`` -> ``cost``).
    """
    if file_path.suffix not in {".ts", ".tsx"}:
        return []

    rel_path = file_path.relative_to(project_root)
    lines = content.splitlines()
    stripped = _BLOCK_COMMENT_RE.sub(
        lambda cm: "".join(" " if c != "\n" else "\n" for c in cm.group()),
        content,
    )

    warnings: list[str] = []
    for m in HARDCODED_USD_FIELD_RE.finditer(stripped):
        line_num, col, original_line, line_text = _locate(stripped, lines, m.start())
        if line_text.startswith(_COMMENT_PREFIXES):
            continue
        if _is_in_comment_context(original_line, col):
            continue
        if _is_regional_suppressed(lines, line_num):
            continue
        warnings.append(
            f"  {rel_path}:{line_num}: Identifier `{m.group('field')}` ends in `_usd` "
            f"-- rename to the currency-neutral form; stored value is in the "
            f"operator's configured currency.\n"
            f"    {line_text}",
        )
    return warnings


def check_missing_story(file_path: Path, project_root: Path) -> list[str]:
    """Check that new components in components/ui/ have a .stories.tsx file."""
    rel_path = file_path.relative_to(project_root)
    parts = rel_path.parts

    if not (
        "components" in parts
        and "ui" in parts
        and file_path.suffix == ".tsx"
        and ".stories." not in file_path.name
        and file_path.name != "index.tsx"
        and "__tests__" not in parts
    ):
        return []

    story_path = file_path.with_suffix("").with_suffix(".stories.tsx")
    if story_path.exists():
        return []
    return [
        f"  {rel_path}: New component without Storybook story "
        f"-- create `{story_path.name}` alongside it."
    ]


def _check_status_dot(content: str, content_lower: str, rel_path: Path) -> list[str]:
    """Check for inline status dot patterns that should use StatusBadge."""
    patterns = [
        r"(?:size-[12]\.?5?|w-[23]|h-[23])\s+.*rounded-full.*(?:bg-success|bg-danger|bg-warning|bg-accent)",
        r"(?:bg-success|bg-danger|bg-warning|bg-accent).*rounded-full.*(?:size-[12]\.?5?|w-[23]|h-[23])",
    ]
    for pattern in patterns:
        if (
            re.search(pattern, content)
            and "statusbadge" not in content_lower
            and "status-badge" not in content_lower
        ):
            return [
                f"  {rel_path}: Inline status dot pattern detected "
                f"-- use `<StatusBadge>` from `@/components/ui/status-badge` instead."
            ]
    return []


def _check_avatar(content: str, rel_path: Path) -> list[str]:
    """Check for inline avatar patterns that should use Avatar.

    Heuristic: matches rounded-full + flex-center + small-text, which is
    the typical initials-circle recipe. May false-positive on non-avatar
    circular flex elements (e.g. icon badges). Kept as a soft suggestion.
    """
    pattern = r"rounded-full.*(?:flex|inline-flex).*(?:items-center|justify-center).*(?:text-xs|text-sm|text-micro)"
    has_avatar_import = (
        "<Avatar" in content
        or "from './avatar'" in content
        or "from '@/components/ui/avatar'" in content
    )
    if re.search(pattern, content) and not has_avatar_import:
        return [
            f"  {rel_path}: Possible inline avatar/initials circle detected "
            f"-- consider using `<Avatar>` from `@/components/ui/avatar` "
            f"or ignore if not applicable."
        ]
    return []


def _check_metric(content: str, content_lower: str, rel_path: Path) -> list[str]:
    """Check for inline metric patterns that should use MetricCard."""
    patterns = [
        r"text-metric.*font-bold.*font-mono",
        r"font-mono.*text-metric.*font-bold",
    ]
    for pattern in patterns:
        if (
            re.search(pattern, content)
            and "metriccard" not in content_lower
            and "metric-card" not in content_lower
        ):
            return [
                f"  {rel_path}: Inline metric display pattern detected "
                f"-- use `<MetricCard>` from `@/components/ui/metric-card` instead."
            ]
    return []


def check_duplicate_patterns(
    content: str,
    file_path: Path,
    project_root: Path,
) -> list[str]:
    """Detect patterns that duplicate existing shared components."""
    rel_path = file_path.relative_to(project_root)
    parts = rel_path.parts

    if "components" in parts and "ui" in parts:
        return []

    content_lower = content.lower()
    warnings: list[str] = []

    warnings.extend(_check_status_dot(content, content_lower, rel_path))
    warnings.extend(_check_avatar(content, rel_path))
    warnings.extend(_check_metric(content, content_lower, rel_path))

    # Check for card patterns that should use SectionCard
    if (
        "border border-border bg-card" in content
        and "border-b border-border" in content
        and "SectionCard" not in content
        and "section-card" not in content
        and file_path.name
        not in {"section-card.tsx", "metric-card.tsx", "agent-card.tsx"}
    ):
        warnings.append(
            f"  {rel_path}: Card-with-header pattern detected "
            f"-- use `<SectionCard>` from `@/components/ui/section-card` instead."
        )

    return warnings


def propose_shared_components(
    content: str,
    file_path: Path,
    project_root: Path,
) -> list[str]:
    """Propose new shared building blocks when repeated patterns are found."""
    rel_path = file_path.relative_to(project_root)
    parts = rel_path.parts

    if "components" in parts and "ui" in parts:
        return []

    proposals: list[str] = []

    # Detect complex list items in .map() that should be extracted.
    # Only matches parenthesized returns: .map((x) => (...)).
    # Misses block bodies and bare JSX returns -- acceptable heuristic.
    map_blocks = re.findall(
        r"\.map\(\s*\([^)]*\)\s*=>\s*\(([\s\S]*?)\)\s*\)",
        content,
    )
    for block in map_blocks:
        line_count = block.count("\n")
        if line_count > _MAP_BLOCK_COMPLEXITY and (
            ("className" in block and "border" in block) or "rounded" in block
        ):
            proposals.append(
                f"  {rel_path}: Complex list item ({line_count} lines) in .map() "
                f"-- consider extracting to a shared component in "
                f"`web/src/components/ui/` with a Storybook story."
            )

    # Detect repeated icon+text row layouts
    icon_label_value = re.findall(
        r'<(?:div|span)[^>]*className="[^"]*(?:flex|inline-flex)[^"]*items-center[^"]*gap[^"]*"[^>]*>'
        r"[\s\S]*?<(?:Icon|Lucide|\w+Icon)",
        content,
    )
    if len(icon_label_value) >= _REPEATED_PATTERN_MIN:
        proposals.append(
            f"  {rel_path}: {len(icon_label_value)} repeated icon+text row patterns "
            f"-- consider a `<DataRow icon label value />` shared component."
        )

    return proposals


def check_file(file_path: Path, project_root: Path) -> list[str]:
    """Run all checks on a single file."""
    if not _is_allowed_file(file_path, project_root):
        return []

    try:
        content = file_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        # Deliberate fail-open: this is a best-effort PostToolUse hook that
        # scans only the single file the Edit/Write just produced, so a
        # transient read failure must not block that edit (same rationale
        # as check_backend_regional_defaults). It is advisory only -- there
        # is no whole-tree CI gate for these design-token rules, so the
        # skip cannot mask a violation that some authoritative gate would
        # otherwise catch.
        print(
            f"WARNING: Could not read {file_path}: {exc} "
            f"-- design system check skipped.",
            file=sys.stderr,
        )
        return []

    all_warnings: list[str] = []
    all_warnings.extend(check_hardcoded_colors(content, file_path, project_root))
    all_warnings.extend(check_hardcoded_fonts(content, file_path, project_root))
    all_warnings.extend(
        check_hardcoded_motion_transitions(content, file_path, project_root),
    )
    all_warnings.extend(check_hardcoded_locale(content, file_path, project_root))
    all_warnings.extend(check_hardcoded_currency(content, file_path, project_root))
    all_warnings.extend(
        check_hardcoded_currency_symbol(content, file_path, project_root),
    )
    all_warnings.extend(check_usd_field_names(content, file_path, project_root))
    all_warnings.extend(check_missing_story(file_path, project_root))
    all_warnings.extend(check_duplicate_patterns(content, file_path, project_root))
    all_warnings.extend(propose_shared_components(content, file_path, project_root))

    return all_warnings


def _resolve_project_root(file_path: Path, explicit_root: str | None) -> Path:
    """Find the project root directory."""
    if explicit_root:
        return Path(explicit_root).resolve()
    candidate = file_path.parent
    while candidate != candidate.parent:
        if (candidate / "pyproject.toml").exists() or (candidate / ".git").exists():
            return candidate
        candidate = candidate.parent
    cwd = Path.cwd()
    print(
        f"WARNING: Could not detect project root from {file_path}; "
        f"falling back to cwd ({cwd}).",
        file=sys.stderr,
    )
    return cwd


def _get_file_path_from_stdin() -> str | None:
    """Try to read the file path from hook JSON on stdin."""
    if sys.stdin.isatty():
        return None
    try:
        data = json.load(sys.stdin)
        raw = data.get("tool_input", {}).get("file_path")
        return str(raw) if isinstance(raw, str) else None
    except (json.JSONDecodeError, AttributeError, TypeError) as exc:
        print(
            f"WARNING: Failed to parse hook JSON from stdin: {exc}",
            file=sys.stderr,
        )
        return None


def main() -> int:
    """Entry point: hook mode (stdin JSON) or CLI mode (positional arg)."""
    stdin_path = _get_file_path_from_stdin()

    if stdin_path:
        file_path = Path(stdin_path).resolve()
        project_root = _resolve_project_root(file_path, None)
    else:
        parser = argparse.ArgumentParser(
            description="Check web design system adherence",
        )
        parser.add_argument("file_path", help="File to check")
        parser.add_argument(
            "--project-root",
            default=None,
            help="Project root (default: auto-detect)",
        )
        args = parser.parse_args()
        file_path = Path(args.file_path).resolve()
        project_root = _resolve_project_root(file_path, args.project_root)

    warnings = check_file(file_path, project_root)

    if warnings:
        print("DESIGN SYSTEM VIOLATIONS:")
        print()
        for w in warnings:
            print(w)
        print()
        print(
            "Reference: CLAUDE.md 'Web Dashboard' section and "
            "docs/design/brand-and-ux.md 'Component Inventory'."
        )
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
