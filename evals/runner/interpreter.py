# module-kind: code
"""Resolve the interpreter placeholder in an executable brief's check commands.

Brief YAML writes ``{python}`` rather than a bare ``python`` because a hardcoded
interpreter name may be absent or point at the wrong environment; the token is
substituted for the running interpreter at grade time so the checks are portable
across platforms and virtual environments.
"""

import sys
from typing import Final

from evals.models.brief import ExecutableChecks, HiddenCheckSpec

#: Placeholder token brief authors write in place of an interpreter path.
INTERPRETER_PLACEHOLDER: Final[str] = "{python}"


def resolve_cmd(cmd: tuple[str, ...]) -> tuple[str, ...]:
    """Substitute the interpreter token for the running interpreter.

    Returns:
        The command with ``{python}`` resolved.
    """
    return tuple(
        sys.executable if token == INTERPRETER_PLACEHOLDER else token for token in cmd
    )


def resolve_checks(checks: ExecutableChecks) -> ExecutableChecks:
    """Resolve every check command's interpreter token in a copy of *checks*.

    Returns:
        The checks with ``{python}`` resolved in every hidden / build / lint cmd.
    """

    def _resolve(specs: tuple[HiddenCheckSpec, ...]) -> tuple[HiddenCheckSpec, ...]:
        return tuple(
            spec.model_copy(update={"cmd": resolve_cmd(spec.cmd)}) for spec in specs
        )

    return checks.model_copy(
        update={
            "hidden_tests": _resolve(checks.hidden_tests),
            "build": _resolve(checks.build),
            "lint": _resolve(checks.lint),
        }
    )


__all__ = ["INTERPRETER_PLACEHOLDER", "resolve_checks", "resolve_cmd"]
