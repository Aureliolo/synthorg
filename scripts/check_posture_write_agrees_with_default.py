#!/usr/bin/env python3
"""Pre-push / CI gate: a posture write never repeats its own default.

#2888 found ``("engine", "reasoning_effort_low", "none")`` in
``_posture_seeding.py``'s ``cost_disciplined`` bundle: a no-op write that
happened to equal the setting's registered default at the time it was
written. A no-op that still writes PINS the row, so a later default change
(the fix in #2888 itself, raising the registered default so the cheapest
stakes level stops paying the provider's own reasoning bill) never reaches
any deployment that already ran the posture. The module's own comment
claimed "a bundle only ever turns a flag on", which this exact row falsified.

This gate asks two questions of every triple a posture bundle declares,
neither of them enforceable by re-reading the module in isolation:

1. Does the write name a setting that actually exists in the registry? A
   renamed or mistyped key writes nothing at all and no test notices,
   because the write against a fake key still "succeeds" against whatever
   in-memory fake the unit tests hand the seeding function.
2. Does the write differ from that setting's registered default? Equal
   values are the exact shape of the shipped defect.

Detection
---------
The write population is read by IMPORTING
``synthorg.api.controllers.setup._posture_seeding`` and taking its real
module-level ``_POSTURE_FLAG_SETTINGS`` object, never by re-parsing the
module's AST. A bundle's value is not always a string literal
(``_ECONOMICAL_REASONING`` writes ``ReasoningEffort.LOW.value``, an enum
member reference), so an AST literal-evaluator would have to special-case
every expression shape the module happens to use today and silently miss
the next one; importing means Python has already resolved every value by
the time this gate reads it. The same reasoning applies to the registered
defaults: this gate imports ``synthorg.settings.definitions`` (which
populates the real registry as an import side effect) and queries it
directly, the same way the resolver itself does, rather than re-parsing the
``definitions/`` modules (a definition is registered through helper
functions in several of them, so a second parser would be one refactor from
disagreeing with the registrar). Twelve existing gates already import the
product for exactly this reason.

There is deliberately no ``--repo-root`` flag: this gate has no notion of
one. It always reads the one installed ``synthorg`` package's dispatch
table against that same package's registry, never a file on disk, so a
flag naming a directory would be dead surface pretending to parameterise
something that cannot vary.

Allowlist / opt-out
--------------------
None. A genuine exception to either rule is a bug in the posture bundle: the
fix is to delete the offending row, which is exactly what #2888 does.

Usage::

    uv run python scripts/check_posture_write_agrees_with_default.py

Exit codes:
    0 -- every declared write names a real setting and differs from its
         registered default.
    1 -- at least one write names an unregistered setting, or repeats one
         verbatim.
    2 -- configuration error (the posture module could not be imported,
         ``_POSTURE_FLAG_SETTINGS`` is not the expected shape, or the
         derived write population is empty -- fail-closed).
"""

import sys
from collections.abc import Callable
from dataclasses import dataclass
from typing import Final

_POSTURE_MODULE: Final[str] = "synthorg.api.controllers.setup._posture_seeding"
_DISPATCH_NAME: Final[str] = "_POSTURE_FLAG_SETTINGS"


class PostureConfigError(Exception):
    """Raised when the posture module cannot be imported or understood."""


@dataclass(frozen=True)
class _Write:
    """One ``(namespace, key, value)`` triple a posture bundle declares."""

    flag: str
    namespace: str
    key: str
    value: str


@dataclass(frozen=True)
class _Hit:
    """One posture write that fails a rule."""

    write: _Write
    reason: str

    def message(self) -> str:
        """Return the human-facing violation message."""
        w = self.write
        return (
            f"{_POSTURE_MODULE}.{_DISPATCH_NAME}: posture flag {w.flag!r} "
            f"writes {w.namespace}.{w.key} = {w.value!r}: {self.reason}"
        )


def _flatten_dispatch(dispatch: object) -> list[_Write]:
    """Flatten a ``_POSTURE_FLAG_SETTINGS``-shaped object into its writes.

    Pure over its input, so a test can hand this a fixture tuple without
    importing anything.

    Returns:
        Every write across every posture flag, in declaration order.

    Raises:
        PostureConfigError: If *dispatch* is not a tuple of
            ``(str, tuple[(str, str, str), ...])`` pairs.
    """
    if not isinstance(dispatch, tuple):
        msg = f"{_POSTURE_MODULE}: {_DISPATCH_NAME} not found or not a tuple"
        raise PostureConfigError(msg)

    writes: list[_Write] = []
    for entry in dispatch:
        if not (isinstance(entry, tuple) and len(entry) == 2):  # noqa: PLR2004
            msg = (
                f"{_POSTURE_MODULE}.{_DISPATCH_NAME}: an entry is not a "
                f"(flag, writes) pair"
            )
            raise PostureConfigError(msg)
        flag, entry_writes = entry
        if not isinstance(flag, str):
            msg = f"{_POSTURE_MODULE}.{_DISPATCH_NAME}: a flag name is not a string"
            raise PostureConfigError(msg)
        if not isinstance(entry_writes, tuple):
            msg = (
                f"{_POSTURE_MODULE}.{_DISPATCH_NAME}: flag {flag!r}'s writes "
                f"are not a tuple"
            )
            raise PostureConfigError(msg)
        for triple in entry_writes:
            if not (
                isinstance(triple, tuple)
                and len(triple) == 3  # noqa: PLR2004
                and all(isinstance(part, str) for part in triple)
            ):
                msg = (
                    f"{_POSTURE_MODULE}.{_DISPATCH_NAME}: flag {flag!r} carries "
                    f"a write that is not a (namespace, key, value) string triple"
                )
                raise PostureConfigError(msg)
            namespace, key, value = triple
            writes.append(_Write(flag=flag, namespace=namespace, key=key, value=value))
    return writes


def _check_writes(
    writes: list[_Write],
    get_default: Callable[[str, str], str | None],
) -> list[_Hit]:
    """Check every write against its setting's registration and default.

    Pure over its inputs, so a test can hand this a fake registry lookup.

    Args:
        writes: The declared posture writes.
        get_default: Resolves a ``(namespace, key)`` to its registered
            default, or ``None`` when the setting is not registered at all.

    Returns:
        A list of :class:`_Hit`.
    """
    hits: list[_Hit] = []
    for write in writes:
        default = get_default(write.namespace, write.key)
        if default is None:
            hits.append(_Hit(write=write, reason="no such setting is registered"))
            continue
        if default == write.value:
            hits.append(
                _Hit(
                    write=write,
                    reason=(
                        f"this equals the registered default ({default!r}); a "
                        f"no-op write pins the row against a future default change"
                    ),
                )
            )
    return hits


def _live_dispatch() -> object:
    """Import the posture module and return its real dispatch object.

    Returns:
        The module's ``_POSTURE_FLAG_SETTINGS`` value.

    Raises:
        PostureConfigError: If the module cannot be imported.
    """
    import importlib

    try:
        module = importlib.import_module(_POSTURE_MODULE)
    except Exception as exc:
        msg = f"{_POSTURE_MODULE}: could not import ({type(exc).__name__}: {exc})"
        raise PostureConfigError(msg) from exc
    return getattr(module, _DISPATCH_NAME, None)


def _live_default(namespace: str, key: str) -> str | None:
    """Resolve a setting's registered default from the real registry.

    Returns:
        The registered default, or ``None`` when unregistered or the
        registration carries no default.
    """
    import synthorg.settings.definitions  # noqa: F401
    from synthorg.settings.registry import get_registry

    definition = get_registry().get(namespace, key)
    return None if definition is None else definition.default


def _scan() -> list[_Hit]:
    """Return every posture write that fails registration or equals its default.

    Returns:
        A list of :class:`_Hit`.

    Raises:
        PostureConfigError: If the posture module cannot be understood, or
            declares no writes at all.
    """
    writes = _flatten_dispatch(_live_dispatch())
    if not writes:
        msg = f"{_POSTURE_MODULE}.{_DISPATCH_NAME} declares no writes"
        raise PostureConfigError(msg)
    return _check_writes(writes, _live_default)


def main(argv: list[str] | None = None) -> int:
    """CLI entry point.

    Args:
        argv: Present only so the signature matches every sibling gate's;
            this gate takes no arguments and ignores it.

    Returns:
        The gate exit code (0 clean, 1 violation, 2 configuration error).
    """
    del argv
    try:
        hits = _scan()
    except PostureConfigError as exc:
        print(f"check_posture_write_agrees_with_default: {exc}", file=sys.stderr)
        return 2

    if not hits:
        return 0
    for hit in hits:
        print(hit.message())
    print(
        f"\n{len(hits)} posture write(s) failed. Delete the offending row.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
