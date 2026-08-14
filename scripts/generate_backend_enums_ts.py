#!/usr/bin/env python3
"""Generate ``web/src/api/types/backend-enums.gen.ts``.

Mirrors backend enums the dashboard previously hand-maintained into a
TypeScript module that stays in lockstep with the Python source:

* ``WsEventType`` from ``synthorg.api.ws_models`` (the WS event tuple),
* ``NotificationSeverity`` from ``synthorg.notifications.models``,
* ``LogLevel`` from ``synthorg.observability.enums``,
* ``ProviderOutcomeClass`` from ``synthorg.providers.health``. It keys the
  serviceability view's ``outcome_counts``, and a mapping key erases to
  ``string`` on the wire, so this is the only path by which the dashboard
  learns a new outcome class rather than silently omitting it.

Each emits a ``readonly`` value tuple plus the derived union type, so the
frontend keeps narrowing against the same allowlist the backend ships.

The accompanying gate at ``scripts/check_backend_enums_ts_in_sync.py``
re-runs this generator in ``--check`` mode and fails on drift.

Usage::

    uv run python scripts/generate_backend_enums_ts.py
    uv run python scripts/generate_backend_enums_ts.py --check
    uv run python scripts/generate_backend_enums_ts.py --stdout
"""

import argparse
import sys
from enum import StrEnum
from pathlib import Path
from typing import Final

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from synthorg.api.ws_models import WsEventType
from synthorg.notifications.models import NotificationSeverity
from synthorg.observability.enums import LogLevel
from synthorg.providers.health import ProviderOutcomeClass

_OUTPUT_REL: Final[str] = "web/src/api/types/backend-enums.gen.ts"

_HEADER: Final[str] = (
    "// AUTO-GENERATED: do not edit by hand.\n"
    "// Regenerate with: uv run python scripts/generate_backend_enums_ts.py\n"
    "// Drift check (pre-push): "
    "uv run python scripts/check_backend_enums_ts_in_sync.py\n"
    "// Sources: src/synthorg/api/ws_models.py,"
    " src/synthorg/notifications/models.py,"
    " src/synthorg/observability/enums.py,"
    " src/synthorg/providers/health.py\n"
)


def _render_block(const_name: str, type_name: str, members: type[StrEnum]) -> str:
    """Render one ``const`` value tuple plus its derived union type."""
    values = "\n".join(f'  "{member.value}",' for member in members)
    return (
        f"export const {const_name} = [\n"
        f"{values}\n"
        "] as const\n"
        f"export type {type_name} = (typeof {const_name})[number]\n"
    )


def _render() -> str:
    """Return the full TypeScript module source (LF line endings)."""
    blocks = "\n".join(
        [
            _render_block("WS_EVENT_TYPE_VALUES", "WsEventType", WsEventType),
            _render_block(
                "NOTIFICATION_SEVERITY_VALUES",
                "NotificationSeverity",
                NotificationSeverity,
            ),
            _render_block("LOG_LEVEL_VALUES", "LogLevel", LogLevel),
            _render_block(
                "PROVIDER_OUTCOME_CLASS_VALUES",
                "ProviderOutcomeClass",
                ProviderOutcomeClass,
            ),
        ]
    )
    return f"{_HEADER}\n{blocks}"


def main() -> int:
    """Write the generated file (or compare against it)."""
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--check",
        action="store_true",
        help="Compare against the committed file; exit 1 on drift.",
    )
    group.add_argument(
        "--stdout",
        action="store_true",
        help="Print the generated module to stdout instead of writing it.",
    )
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parents[1]
    output_path = repo_root / _OUTPUT_REL
    rendered = _render()

    if args.stdout:
        sys.stdout.write(rendered)
        return 0

    if args.check:
        if not output_path.exists():
            print(f"missing generated file: {output_path}", file=sys.stderr)
            return 1
        if output_path.read_text(encoding="utf-8") != rendered:
            print(
                f"\n{output_path.relative_to(repo_root).as_posix()} is out of "
                f"sync with its Python enum sources.\n"
                f"\nRun: uv run python scripts/generate_backend_enums_ts.py",
                file=sys.stderr,
            )
            return 1
        return 0

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8", newline="\n") as fh:
        fh.write(rendered)
    print(
        f"wrote {len(WsEventType)} WsEventType + "
        f"{len(NotificationSeverity)} NotificationSeverity + "
        f"{len(LogLevel)} LogLevel + "
        f"{len(ProviderOutcomeClass)} ProviderOutcomeClass entries to "
        f"{output_path.relative_to(repo_root).as_posix()}",
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
