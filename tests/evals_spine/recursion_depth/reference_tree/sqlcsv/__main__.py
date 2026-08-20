# module-kind: tests
"""The command-line surface."""

import argparse
import sys
from pathlib import Path

from sqlcsv.engine import execute
from sqlcsv.errors import InputError, SqlcsvError
from sqlcsv.parser import parse
from sqlcsv.render import render

_FORMATS = ("table", "csv", "json")


def _build_parser() -> argparse.ArgumentParser:
    """Build the argument parser.

    Returns:
        The parser.
    """
    parser = argparse.ArgumentParser(
        prog="sqlcsv",
        description="Answer SQL queries against CSV files on disk.",
    )
    parser.add_argument("--data", help="Directory of CSV files, one per table.")
    parser.add_argument("--format", choices=_FORMATS, default="table")
    parser.add_argument("sql", help="The statement, or - to read it from stdin.")
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run one query and print its result.

    Args:
        argv: Command-line arguments, defaulting to ``sys.argv[1:]``.

    Returns:
        The process exit code.
    """
    parser = _build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exit_request:
        # argparse exits 0 for --help and 2 for a usage error, which is the
        # code this CLI assigns unusable input anyway.
        return int(exit_request.code or 0)
    try:
        return _run(args)
    except SqlcsvError as error:
        print(f"sqlcsv: {error}", file=sys.stderr)
        return error.exit_code


def _run(args: argparse.Namespace) -> int:
    """Execute the parsed arguments.

    Returns:
        The process exit code.

    Raises:
        InputError: No data directory was given.
    """
    source = sys.stdin.read() if args.sql == "-" else args.sql
    statement = parse(source)
    if not args.data:
        msg = "--data is required"
        raise InputError(msg)
    result = execute(statement, Path(args.data))
    sys.stdout.write(render(result, args.format))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
