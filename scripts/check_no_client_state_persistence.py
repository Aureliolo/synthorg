#!/usr/bin/env python3
"""Gate: the web dashboard persists NO application state client-side.

The dashboard is a pure API consumer (see web/CLAUDE.md "Pure API Consumer"):
the backend is the single source of truth, hydrated from a GET on mount and
written through the REST API. This gate flags ``localStorage`` /
``sessionStorage`` / ``indexedDB`` access and zustand ``persist`` middleware
usage in ``web/src/`` outside a small allowlist.

The only sanctioned client storage is non-domain transient transport/UX:
the auth-token + CSRF cookie shim, the build-version check, the test storage
shim, and genuinely per-device ephemeral state (canvas pan/zoom viewport and
in-progress form drafts). Everything an operator would expect to follow their
account MUST live backend-side.

Run from the repository root. Exits non-zero on any violation.
"""

import re
import sys
from pathlib import Path

WEB_SRC = Path("web/src")

# Files permitted to use client storage, each for a non-domain transient
# reason. Keep this list short; adding to it is an audit decision.
ALLOWLIST: frozenset[str] = frozenset(
    {
        # Auth token + active CSRF token transport shim (the cookie shim).
        "web/src/api/client.ts",
        # Build-version check: clears stale storage + hard-reloads on a new build.
        "web/src/utils/app-version.ts",
        # Test-only Storage subclass shim (lets test spies observe storage).
        "web/src/storage-shim.ts",
        # Per-device in-progress form drafts: kept local so unsaved work
        # survives a backend hiccup and does not sync across devices.
        "web/src/hooks/use-unsaved-changes-guard.ts",
        # Per-device canvas pan/zoom viewport (screen state, not a preference).
        "web/src/pages/WorkflowEditorPage.tsx",
        "web/src/pages/org/useOrgChartController.ts",
    }
)

# Path fragments marking a test / story / test-infra file (exempt).
_TEST_MARKERS = ("__tests__", ".test.", ".stories.", "test-setup", "test-infra")

_STORAGE_RE = re.compile(r"\b(localStorage|sessionStorage|indexedDB)\b")
# Zustand persist middleware: imported by name from ``zustand/middleware``.
# Matching the import (not a bare ``persist(`` token) avoids false positives on
# a locally-named ``persist`` helper.
_ZUSTAND_PERSIST_RE = re.compile(
    r"import\s*\{[^}]*\bpersist\b[^}]*\}\s*from\s*['\"]zustand/middleware['\"]"
)
_BLOCK_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)
_LINE_COMMENT_RE = re.compile(r"//.*$", re.MULTILINE)


def _strip_comments(text: str) -> str:
    """Remove block + line comments so prose mentioning storage is ignored.

    Returns:
        The source with comments blanked (line structure preserved).
    """
    without_blocks = _BLOCK_COMMENT_RE.sub(
        lambda m: "\n" * m.group(0).count("\n"), text
    )
    return _LINE_COMMENT_RE.sub("", without_blocks)


def _is_test(path: Path) -> bool:
    """Whether a path is a test / story / test-infra file.

    Returns:
        ``True`` when the path matches a test marker.
    """
    posix = path.as_posix()
    return any(marker in posix for marker in _TEST_MARKERS)


def _scan_file(path: Path) -> list[str]:
    """Collect client-storage violations in a single file.

    Returns:
        A list of human-readable violation strings (empty when clean).
    """
    code = _strip_comments(path.read_text(encoding="utf-8"))
    found: list[str] = []
    for idx, line in enumerate(code.splitlines(), start=1):
        match = _STORAGE_RE.search(line)
        if match is not None:
            found.append(f"{path.as_posix()}:{idx}: client storage `{match.group(1)}`")
    if _ZUSTAND_PERSIST_RE.search(code):
        found.append(f"{path.as_posix()}: zustand `persist` middleware")
    return found


def main() -> int:
    """Scan ``web/src`` and report client-state-persistence violations.

    Returns:
        Process exit code: 0 when clean, 1 on any violation.
    """
    if not WEB_SRC.is_dir():
        print(f"error: {WEB_SRC} not found (run from the repository root)")
        return 1

    violations: list[str] = []
    for path in sorted(WEB_SRC.rglob("*.ts")) + sorted(WEB_SRC.rglob("*.tsx")):
        if _is_test(path):
            continue
        if path.as_posix() in ALLOWLIST:
            continue
        violations.extend(_scan_file(path))

    if violations:
        print("Client-side state persistence is forbidden in the web dashboard.")
        print("The backend is the single source of truth (web/CLAUDE.md).")
        print("Move the state to a backend settings namespace + hydrate via the API,")
        print("or, for genuinely per-device transient UX, add the file to the")
        print(
            "allowlist in scripts/check_no_client_state_persistence.py with a reason.\n"
        )
        for violation in violations:
            print(f"  {violation}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
