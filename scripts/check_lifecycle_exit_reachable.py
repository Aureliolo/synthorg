#!/usr/bin/env python3
"""Pre-push / CI gate: every lifecycle state has a reachable exit.

A state whose only routes out pass through a hop the entity may be unable to
take is a state with no exit. The row cannot be finished, cancelled or
deleted, and everything that cascades off it is stuck too.

That is not hypothetical. The task lifecycle let ``FAILED`` reach a terminal
only through ``ASSIGNED``, on the premise that a task keeps its assignee. A
task that failed before it was ever assigned has none, so the hop failed the
``Task`` validator, the raw error surfaced as a 422 that repeated on every
retry, and with the task unresolvable its plan and its project could not be
deleted either. One live project was left undeletable by any route.

Terminal reachability alone would not have caught it: ``FAILED -> ASSIGNED ->
CANCELLED`` is a path, and it passes. So the gate walks only hops the machine
declares **unconditional**: targets a writer can always reach with nothing but
the entity itself and a reason it authors. ``ASSIGNED`` is not one, because it
needs an assignee. ``SUPERSEDED`` is not one for a plan, because it needs a
non-empty item DAG.

The declaration lives on the machine (``StateMachine(unconditional_targets=)``)
rather than being derived here, because the condition lives in the entity's own
validators, which no transition table records.

There is deliberately no baseline and no per-line opt-out. A machine that needs
an exception changes its declaration, in the open, where the next reader sees
which hops the product promises are always available.

Usage::

    python scripts/check_lifecycle_exit_reachable.py
    python scripts/check_lifecycle_exit_reachable.py --repo-root /path/to/repo
"""

import argparse
import importlib
import pkgutil
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from synthorg.core.state_machine import HasStateValue, StateMachine

_PACKAGE = "synthorg.core"


@dataclass(frozen=True, slots=True)
class Violation:
    """A state with no unconditional route to a terminal.

    Attributes:
        machine: The state machine's name, e.g. ``task_status``.
        state: The value of the state that cannot exit.
        allowed: The targets the state does offer, for the message.
    """

    machine: str
    state: str
    allowed: tuple[str, ...]


def _core_modules() -> Iterator[str]:
    """Yield every module under ``synthorg.core``.

    Yields:
        Dotted module names, so each can be imported and inspected.
    """
    package = importlib.import_module(_PACKAGE)
    for info in pkgutil.walk_packages(package.__path__, prefix=f"{_PACKAGE}."):
        yield info.name


def scan_machines() -> tuple[Violation, ...]:
    """Find every declared state with no unconditional exit.

    Machines are found by import rather than by parsing, because the
    declaration is a live object: a table assembled across several literals,
    or one built from an enum, reads correctly only once constructed.

    A machine declaring no unconditional targets is skipped. That is the
    honest reading of an absent declaration: nothing has been promised, so
    there is nothing to check. Adding the declaration is what opts a machine
    in, and the three lifecycles that can strand a row (task, plan, project)
    all carry it.

    Returns:
        The violations, in machine then state order.
    """
    violations: list[Violation] = []
    seen: set[str] = set()
    for module_name in _core_modules():
        module = importlib.import_module(module_name)
        for value in vars(module).values():
            if not isinstance(value, StateMachine):
                continue
            if value.name in seen or not value.unconditional_targets:
                continue
            seen.add(value.name)
            violations.extend(_machine_violations(value))
    return tuple(sorted(violations, key=lambda v: (v.machine, v.state)))


def _machine_violations[S: HasStateValue](
    machine: StateMachine[S],
) -> Iterator[Violation]:
    """Yield one violation per state of *machine* that cannot exit.

    Args:
        machine: The state machine to check.

    Yields:
        A violation per stranded state.
    """
    for state in machine.states:
        if machine.unconditional_exit_reachable(state):
            continue
        yield Violation(
            machine=machine.name,
            state=state.value,
            allowed=tuple(sorted(s.value for s in machine.allowed(state))),
        )


def main(argv: list[str] | None = None) -> int:
    """CLI entry point.

    Returns:
        ``0`` when every declared state can reach a terminal unconditionally.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    # Accepted and ignored: every consolidated gate takes it, and the machines
    # are read from the installed package rather than from a path.
    parser.add_argument("--repo-root", type=Path, default=None)
    parser.parse_args(argv)

    violations = scan_machines()
    for violation in violations:
        print(
            f"{violation.machine}: {violation.state!r} cannot reach a terminal"
            " state through unconditional hops only; it offers"
            f" {list(violation.allowed)}"
        )
    if violations:
        print(
            f"\n{len(violations)} state(s) with no reachable exit. Give each an"
            " unconditional route to a terminal, or declare the hop it already"
            " has in the machine's 'unconditional_targets'.",
            file=sys.stderr,
        )
        return 1
    print("OK: every declared lifecycle state has a reachable exit.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
