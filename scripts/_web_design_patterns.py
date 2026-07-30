"""Regex patterns, allowlists, and thresholds for design-system checks.

Extracted from :mod:`scripts.check_web_design_system` to keep that
module under the 800-line ceiling mandated by CLAUDE.md. The main
script imports from here; behavior is unchanged.
"""

import re

# -- Thresholds --------------------------------------------------------
_SHORT_HEX_LEN = 4  # #rgb (3 digits + hash)
_SHORT_HEX_ALPHA_LEN = 5  # #rgba (4 digits + hash)
_LONG_HEX_ALPHA_LEN = 9  # #rrggbbaa (8 digits + hash)
_MIN_PATH_DEPTH = 2  # web/<something>
_MAP_BLOCK_COMPLEXITY = 8  # lines in .map() before suggesting extraction
_REPEATED_PATTERN_MIN = 3  # icon+text rows before suggesting DataRow

# -- Allowed raw hex values --------------------------------------------
# Must match :root variables in web/src/styles/design-tokens.css.
# Any OTHER hex color in a .tsx/.ts/.css file is a violation.
ALLOWED_HEX_COLORS: frozenset[str] = frozenset(
    {
        "#38bdf8",
        "#0ea5e9",  # accent, accent-dim
        "#10b981",  # success
        "#f59e0b",  # warning
        "#ef4444",  # danger
        "#e2e8f0",
        "#94a3b8",
        "#8b95a5",  # text-primary, text-secondary, text-muted
        "#0a0a12",
        "#0f0f1a",
        "#13131f",
        "#181828",  # bg-base, bg-surface, bg-card, bg-card-hover
        "#1e1e2e",
        "#2a2a3e",  # border, border-bright
    }
)

# -- Patterns that should use design tokens ----------------------------
HARDCODED_COLOR_RE = re.compile(
    r"""(?x)
    (?:
        (?:text|bg|border|fill|stroke|ring|shadow|outline|from|to|via)
        -\[(?P<tw_hex>\#[0-9a-fA-F]{3,8})\]
    )
    |
    (?:
        (?:color|background(?:-color)?|border(?:-color)?|fill|stroke|outline-color)
        \s*:\s*(?P<css_hex>\#[0-9a-fA-F]{3,8})
    )
    |
    (?:
        (?:color|backgroundColor|borderColor|fill|stroke)
        \s*:\s*['"](?P<jsx_hex>\#[0-9a-fA-F]{3,8})['"]
    )
    """,
)

HARDCODED_RGBA_RE = re.compile(
    r"\brgba?\(\s*\d+\s*,\s*\d+\s*,\s*\d+",
)

HARDCODED_FONT_RE = re.compile(
    r"""(?x)
    font-family\s*:\s*(?!\s*var\()
    |
    fontFamily\s*:\s*['"](?!\s*var\()
    """,
)

# Motion inline transition durations (should use lib/motion presets).
HARDCODED_MOTION_DURATION_RE = re.compile(
    r"""(?x)
    transition\s*:\s*\{[^}]*\bduration\s*:\s*[\d.]+
    |
    transition\s*=\s*\{\s*\{[^}]*\bduration\s*:\s*[\d.]+
    """,
    re.DOTALL,
)

# Arbitrary sub-grid pixel sizing on a Tailwind size/width/height utility
# (``size-[5px]``, ``w-[6px]``). Anything this small is a marker dot, which has
# its own token (``size-dot``); the density-aware spacing scale starts at 4px, so
# an arbitrary value here is always a surface inventing its own odd pixel.
# The lookbehind keeps ``max-w-[6px]`` / ``min-h-[2px]`` out: a bound on a box is
# a layout constraint, not a glyph, so it is not the dot token's business.
HARDCODED_DOT_SIZE_RE = re.compile(
    r"(?<![\w-])(?:size|[wh])-\[(?P<px>[0-9](?:\.[0-9]+)?)px\]",
)

# BCP 47 tags used inside Intl or toLocale* calls. Covers the full
# language-(script?)-(region?)-(variant*) shape so bypasses like
# ``'zh-Hans-CN'``, ``'sr-Latn-RS'``, or ``'de-CH-1996'`` are caught,
# not just the two-part ``language-REGION`` form. Extension and
# private-use subtags are out of scope -- they are rare enough that
# the false-positive risk on incidental strings matching the pattern
# outweighs the catch rate.
HARDCODED_LOCALE_RE = re.compile(
    r"""(?x)
    ['"]
    (?P<locale>
        [a-z]{2,3}                                # language subtag
        (?:-[A-Z][a-z]{3})?                       # optional script (e.g. ``Hans``)
        (?:-(?:[A-Z]{2}|[0-9]{3}))?               # optional region
        (?:-[A-Za-z0-9]{2,8})*                    # zero or more variant/extension subtags
    )
    ['"]
    """,
)

# Bare `.toLocaleString(` / `.toLocaleDateString(` / `.toLocaleTimeString(`
# calls with no explicit locale argument.
BARE_TOLOCALE_RE = re.compile(
    r"\.toLocale(?:Date|Time)?String\(\s*\)",
)

# Any use of Intl.* or .toLocale*String(...) anywhere in the file.
LOCALE_USAGE_RE = re.compile(
    r"\bIntl\.|\.toLocale(?:Date|Time)?String\(",
)

# Allowlist files where `'en-US'` is legitimate (default constant or
# the centralized helper itself).
_LOCALE_SKIP_PATHS: frozenset[str] = frozenset(
    {
        "web/src/utils/locale.ts",
        "web/src/utils/format.ts",
    }
)

# ISO 4217 currency codes supported by the dashboard.
HARDCODED_CURRENCY_RE = re.compile(
    r"""['"](?P<currency>"""
    r"USD|EUR|GBP|JPY|CHF|CAD|AUD|CNY|INR|KRW|BRL|MXN|SGD|HKD|NZD|"
    r"SEK|NOK|DKK|PLN|CZK|HUF|TRY|ZAR|THB|TWD|ILS|IDR|VND"
    r""")['"]""",
)

# Allowlist files where ISO 4217 currency codes are legitimate (source
# of truth + format helpers that must reference codes to build the
# symbol map).
_CURRENCY_SKIP_PATHS: frozenset[str] = frozenset(
    {
        "web/src/utils/currencies.ts",
        "web/src/utils/format.ts",
    }
)

# Currency symbols adjacent to digits or template interpolation inside
# string literals. ``"$10"``, ``"\u20ac50"``, ``"\u00a3100"``, and
# ``` `$${value}` `` are flagged. A lone ``${...}`` template
# interpolation produces no literal ``$`` at render time, so it is NOT
# flagged. Symbol alone (e.g. ``"\u20ac"`` in a format helper) is fine.
HARDCODED_CURRENCY_SYMBOL_RE = re.compile(
    r"""(?x)
    (?:"[^"]*(?:\$\d|\$\$\{|[\u20ac\u00a3]\S)[^"]*")
    |
    (?:'[^']*(?:\$\d|\$\$\{|[\u20ac\u00a3]\S)[^']*')
    |
    (?:`[^`]*(?:\$\d|\$\$\{|[\u20ac\u00a3]\S)[^`]*`)
    """,
)

# Identifier suffix ``_usd`` (e.g. ``cost_usd``, ``total_cost_usd``).
HARDCODED_USD_FIELD_RE = re.compile(
    r"\b(?P<field>[a-z][a-z0-9_]*_usd)\b",
)

# Files where inline Motion durations are intentional (relative paths).
_MOTION_DURATION_SKIP_PATHS: frozenset[str] = frozenset(
    {
        "web/src/lib/motion.ts",
        "web/src/hooks/useAnimationPreset.ts",
        "web/src/pages/setup/ThemePreview.tsx",
    }
)

# -- Files to skip -----------------------------------------------------
_SKIP_PATHS: frozenset[str] = frozenset({"design-tokens.css", "global.css"})
_SKIP_DIRS: frozenset[str] = frozenset({"__tests__", "node_modules", ".storybook"})
_COMMENT_PREFIXES = ("//", "/*", "*")

# Regex to strip block comments (/* ... */) before regex scans.
_BLOCK_COMMENT_RE = re.compile(r"/\*[\s\S]*?\*/")

# Opt-out marker for the regional-defaults checks.
_REGIONAL_SUPPRESSION_MARKER = "lint-allow: regional-defaults"

_MIN_LINE_NUM_FOR_PRECEDING = 2
