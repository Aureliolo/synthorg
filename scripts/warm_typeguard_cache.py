#!/usr/bin/env python3
"""Pre-build typeguard's instrumented bytecode for the whole package.

The test suite runs under ``--typeguard-packages=synthorg``, so every
``synthorg`` module is AST-rewritten and recompiled before it can be
imported. Measured cold on this tree: ``import synthorg.api.app`` costs
7.5s plain and 24.5s with the hook installed, i.e. **17s of pure
instrumentation, per process**.

typeguard caches the instrumented bytecode, so that cost should be paid
once. It is not, for two reasons:

* The cache tag embeds typeguard's version (``opt-typeguard452``), so a
  version bump silently invalidates every cached file at once. After the
  4.5.2 to 4.6.0 bump this tree held 3797 stale files and zero current
  ones, and nothing said so.
* Each pytest-xdist worker is its own process, so a cold cache is paid by
  all of them at once, competing for the same cores. CI caches uv but not
  ``__pycache__``, so every shard of every run pays it.

Warming once, serially, replaces N concurrent cold instrumentation passes
with one. Run it after a dependency sync and before a test run; it is a
no-op when the cache is already current.

Usage::

    python scripts/warm_typeguard_cache.py
    python scripts/warm_typeguard_cache.py --quiet
    python scripts/warm_typeguard_cache.py --quiet --mark-failures
"""

import argparse
import contextlib
import importlib
import pkgutil
import sys
import time
from pathlib import Path
from typing import Final

from typeguard import install_import_hook

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from _prepush_scope import hooks_dir  # type: ignore[import-not-found]
else:
    from scripts._prepush_scope import hooks_dir

# Read and cleared by ``run_affected_tests.py``: the warm that writes it runs
# detached, so this is the only route from its failure to the developer who
# is about to pay for it.
WARM_FAILED_MARKER: Final[str] = "typeguard-warm-FAILED"

# Below this, the walk plainly did not reach the package: an ImportError
# swallowed at the root would otherwise leave a silent no-op that still
# exits 0 while every worker keeps paying full price.
_MIN_EXPECTED_MODULES: Final[int] = 500

# Importing this first primes the graph in the order the app itself uses,
# which tests/conftest.py does for the same reason: the hub __init__
# modules are deliberately empty, so an arbitrary import order can hit a
# partially-initialised package.
_ROOT_MODULE: Final[str] = "synthorg.api.app"


def _walk_package(*, quiet: bool) -> tuple[int, list[str]]:
    """Import every ``synthorg`` submodule under the instrumentation hook.

    Args:
        quiet: Suppress the per-module failure lines.

    Returns:
        The number of modules imported and the names that could not be.
    """
    import synthorg

    skipped: list[str] = []

    def _record(name: str, exc: BaseException) -> None:
        skipped.append(f"{name}: {type(exc).__name__}: {exc}")
        if not quiet:
            print(f"  skipped {name} ({type(exc).__name__})")

    def _on_walk_error(name: str) -> None:
        # walk_packages imports each PACKAGE itself to read its __path__ and
        # recurse. Without this callback it re-raises anything that is not an
        # ImportError straight out of the generator, where the loop body's own
        # handler below cannot see it -- one subpackage raising at import would
        # end the whole warm rather than being skipped like any other module.
        exc = sys.exception()
        _record(name, exc if exc is not None else RuntimeError("import failed"))

    walk = pkgutil.walk_packages(
        synthorg.__path__, prefix="synthorg.", onerror=_on_walk_error
    )
    for info in walk:
        if info.name in sys.modules:
            continue
        try:
            importlib.import_module(info.name)
        except MemoryError, RecursionError:
            # Resource exhaustion is not an optional dependency; let it out.
            raise
        except (Exception, SystemExit) as exc:
            # A module may be unimportable for reasons that have nothing to
            # do with warming: an optional extra absent from this env, or a
            # module that exits on import (SystemExit is not an Exception, so
            # it is named). Neither should fail the warm, and every one of
            # them simply stays uncached. KeyboardInterrupt is deliberately
            # NOT caught: this walks hundreds of modules, and swallowing it
            # would leave the run unstoppable from the terminal.
            _record(info.name, exc)
    return sum(1 for name in sys.modules if name.startswith("synthorg")), skipped


def _leave_failure_marker(reason: str) -> None:
    """Record that a detached warm failed, for the next test run to report.

    Detached, nothing reads this process's exit code and nothing reads its
    log unless already suspicious, so a repeatedly-failing warm is invisible
    while every test process quietly pays the full instrumentation cost.
    The dmypy re-warm has the same problem and solves it the same way.

    Args:
        reason: One line explaining what the warm could not do.
    """
    directory = hooks_dir()
    if directory is None:
        return
    with contextlib.suppress(OSError):
        directory.mkdir(parents=True, exist_ok=True)
        (directory / WARM_FAILED_MARKER).write_text(reason, encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    """Warm the instrumented-bytecode cache; report what it covered."""
    parser = argparse.ArgumentParser(description="Warm typeguard's bytecode cache.")
    parser.add_argument("--quiet", action="store_true")
    parser.add_argument(
        "--mark-failures",
        action="store_true",
        help=(
            "Leave a marker the next test run reports. For the detached "
            "post-sync warm, whose exit code nothing reads."
        ),
    )
    args = parser.parse_args(argv)

    if sys.dont_write_bytecode:
        reason = (
            "PYTHONDONTWRITEBYTECODE is set, so nothing can be cached; "
            "unset it and re-run.\n"
        )
        print(reason, file=sys.stderr)
        if args.mark_failures:
            _leave_failure_marker(reason)
        return 1

    started = time.monotonic()
    install_import_hook(["synthorg"])
    importlib.import_module(_ROOT_MODULE)
    count, skipped = _walk_package(quiet=args.quiet)
    elapsed = time.monotonic() - started

    if count < _MIN_EXPECTED_MODULES:
        reason = (
            f"only {count} synthorg modules were instrumented, expected at "
            f"least {_MIN_EXPECTED_MODULES}: the warm did not cover the "
            f"package and every test process will still pay for it.\n"
        )
        print(reason, file=sys.stderr)
        if args.mark_failures:
            _leave_failure_marker(reason)
        return 1
    if args.mark_failures:
        # A warm that now succeeds retires the previous failure itself; the
        # reporter would otherwise only clear it after warning about a state
        # that no longer holds.
        directory = hooks_dir()
        if directory is not None:
            with contextlib.suppress(OSError):
                (directory / WARM_FAILED_MARKER).unlink(missing_ok=True)
    if not args.quiet:
        suffix = f", {len(skipped)} unimportable" if skipped else ""
        print(f"typeguard cache warm: {count} modules in {elapsed:.1f}s{suffix}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
