"""Frozen dataclasses, Literal types, and constants for the lint.

Extracted from :mod:`scripts.check_setting_to_startup_trace` to keep
that module under the 800-line ceiling mandated by CLAUDE.md.
Behaviour is unchanged; the entry script re-exports the names below
so test fixtures and external callers see one logical module.
"""

import ast
import io
import tokenize
from dataclasses import dataclass
from typing import Final, Literal

_SUPPRESSION_MARKER: Final[str] = "lint-allow: bootstrap-wiring"

_BASELINE_FIELDS: Final[int] = 3

_LIFECYCLE_FILES: Final[tuple[str, ...]] = (
    "src/synthorg/api/app.py",
    "src/synthorg/api/lifecycle.py",
    "src/synthorg/api/lifecycle_builder.py",
    "src/synthorg/api/lifecycle_helpers/__init__.py",
    "src/synthorg/api/lifecycle_helpers/bootstrap.py",
    "src/synthorg/api/lifecycle_helpers/config_apply.py",
    "src/synthorg/api/lifecycle_helpers/settings_dispatcher.py",
    "src/synthorg/api/lifecycle_helpers/audit_retention.py",
    "src/synthorg/api/lifecycle_helpers/ticket_cleanup.py",
    "src/synthorg/api/auto_wire.py",
)

_BASELINE_HEADER = """\
# Frozen baseline of pre-existing settings → startup-wiring violations.
# Each line is `<setting_key>:<kind>:<owning_class>` sorted in
# deterministic order.
#
# scripts/check_setting_to_startup_trace.py reads this file to
# suppress violations at these exact entries. New violations NOT in
# this list will fail the pre-push hook.
#
# Regenerate (rare; requires explicit user approval) with:
#   uv run python scripts/check_setting_to_startup_trace.py --update-baseline
"""


_FactoryNode = ast.FunctionDef | ast.AsyncFunctionDef
"""Union of sync + async factory FunctionDef shapes. Both are
inspected the same way for return annotation and gating-namespace
extraction; using a union here lets callers stay agnostic."""


@dataclass(frozen=True)
class SettingRecord:
    """Metadata for one registered setting, extracted from definitions/."""

    namespace: str
    key: str
    setting_key: str
    default: str | None
    read_only_post_init: bool
    source_file: str
    source_line: int
    has_suppression: bool


_GhostKind = Literal["hardcoded-none", "factory-gated"]
"""Discriminator for :class:`GhostService`. ``hardcoded-none`` covers
``x: T | None = None`` paired with a conditional ``if x is not None:
x.start()``. ``factory-gated`` covers ``x = factory(...)`` where the
factory returns ``None`` on a default-disabled gating flag."""

_ViolationKind = Literal["ghost-wired"]
"""Currently the lint only emits one violation kind. Reserved as a
``Literal`` (rather than a bare string) so future kinds (e.g.
``unconsumed-setting``) can extend the union without silent drift in
baseline-file parsing."""


@dataclass(frozen=True)
class GhostService:
    """A class whose .start() never runs at boot.

    Invariant: ``gating_namespace`` is non-None iff
    ``kind == "factory-gated"``. The ``__post_init__`` enforces this
    so callers that construct a ``GhostService`` with a mismatched
    pair fail fast instead of silently producing nonsense violations.
    """

    class_name: str
    kind: _GhostKind
    gating_namespace: str | None
    source_file: str  # path to lifecycle/app file where the ghost was detected

    def __post_init__(self) -> None:
        """Reject invalid (kind, gating_namespace) pairs."""
        has_gating = self.gating_namespace is not None
        is_factory = self.kind == "factory-gated"
        if has_gating != is_factory:
            msg = (
                f"GhostService(kind={self.kind!r}) requires "
                f"gating_namespace="
                f"{'<non-None>' if is_factory else 'None'}, "
                f"got {self.gating_namespace!r}"
            )
            raise ValueError(msg)


@dataclass(frozen=True)
class Violation:
    """A single ghost-wired setting flagged by the lint.

    Invariant: ``setting_key`` and ``owning_class`` must not contain
    ``:`` because :meth:`baseline_key` joins fields with that
    delimiter. Setting names are dotted-lowercase (no colons by
    convention) and class names are bare identifiers; both
    invariants hold for every existing site, but the
    ``__post_init__`` makes the assumption explicit so a future
    rename that breaks the format fails fast.
    """

    setting_key: str
    kind: _ViolationKind
    owning_class: str
    source_file: str
    source_line: int
    reason: str

    def __post_init__(self) -> None:
        """Reject field values that would corrupt the baseline format."""
        if ":" in self.setting_key:
            msg = f"setting_key may not contain ':'; got {self.setting_key!r}"
            raise ValueError(msg)
        if ":" in self.owning_class:
            msg = f"owning_class may not contain ':'; got {self.owning_class!r}"
            raise ValueError(msg)

    def baseline_key(self) -> str:
        """Compact key used in the baseline file format."""
        return f"{self.setting_key}:{self.kind}:{self.owning_class}"


def _line_has_trailing_marker(line: str) -> bool:
    """Return True iff *line* carries the marker as a trailing ``#`` comment.

    The marker name (``lint-allow: bootstrap-wiring``) must be
    followed by `` -- `` (a separator with surrounding whitespace) and
    non-empty justification text -- the canonical form is
    ``# lint-allow: bootstrap-wiring -- <reason>``.

    Exception handler below uses the unparenthesized ``except A, B:``
    form (PEP 758, accepted for Python 3.14,
    https://peps.python.org/pep-0758/) -- this is the project-wide
    convention enforced by ruff on Python 3.14 and is NOT the Python
    2 binding form.
    """
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(line).readline))
    except tokenize.TokenError, IndentationError, SyntaxError:
        return False
    for tok in tokens:
        if tok.type != tokenize.COMMENT:
            continue
        comment = tok.string.lstrip("#").strip()
        if not comment.startswith(_SUPPRESSION_MARKER):
            continue
        suffix = comment[len(_SUPPRESSION_MARKER) :].strip()
        if suffix.startswith("--"):
            justification = suffix[2:].strip()
            if justification:
                return True
    return False
