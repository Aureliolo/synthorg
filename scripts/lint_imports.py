#!/usr/bin/env python3
"""UTF-8-safe entry point for the ``lint-imports`` pre-push hook.

import-linter renders its banner and contract report through ``rich``.
On a legacy Windows console ``rich`` drives the win32 console API and
encodes its box-drawing output with the console code page (cp1252),
which raises ``UnicodeEncodeError`` and aborts the hook even though the
contracts themselves pass. Running the CLI under Python UTF-8 mode makes
that write encode as UTF-8 instead, so the report renders cleanly.

The wrapper re-execs itself with ``-X utf8`` when UTF-8 mode is off, then
delegates to import-linter's own CLI so behaviour is otherwise identical
on every platform (a no-op re-exec where UTF-8 mode is already the
default, e.g. CI on Linux).
"""

import os
import sys

if sys.flags.utf8_mode == 0:
    # Re-exec the same interpreter under UTF-8 mode; not user input.
    os.execv(sys.executable, [sys.executable, "-X", "utf8", *sys.argv])  # noqa: S606

from importlinter.cli import lint_imports_command

if __name__ == "__main__":
    lint_imports_command()
