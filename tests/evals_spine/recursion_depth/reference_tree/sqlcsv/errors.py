# module-kind: tests
"""Faults the CLI reports, each carrying the exit code the spec assigns it."""


class SqlcsvError(Exception):
    """A fault the CLI reports rather than a traceback.

    Attributes:
        exit_code: The status the process leaves with.
    """

    exit_code = 2


class InputError(SqlcsvError):
    """The input was not usable: a lexical, syntax or argument fault."""

    exit_code = 2


class NotFoundError(SqlcsvError):
    """The query was well formed but named something absent."""

    exit_code = 3
