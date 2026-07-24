"""npx command-line parsing for the MCP server version-pin check.

An ``npx``-style launcher resolves its package at spawn time, so an
unpinned spec silently runs whatever is newest on every reconnect. Which
token in the command line is actually the package is not obvious:
``npx --package=foo bar`` installs ``foo`` and runs ``bar``'s binary, and
``npx -c '<shell>'`` names no package at all. This module owns that
parsing so ``MCPServerConfig._validate_npm_pin`` reads as the policy it
is rather than as an argv walker.
"""

from typing import Final

# Commands that resolve+run an npm package on the fly. A hand-authored
# stdio server using one MUST pin an explicit version so a reconnect can
# never silently pull a newer (and un-reviewed) package version.
_NPX_COMMANDS: Final[frozenset[str]] = frozenset({"npx", "npx.cmd", "pnpm", "bunx"})
# npm dist-tags that float to whatever is newest: not a pin.
_FLOATING_NPM_TAGS: Final[frozenset[str]] = frozenset({"latest", "next", "canary", ""})
# ``npx`` options that name the package to install independently of the
# command to run, so THEY carry the spec that must be pinned.
_PACKAGE_OPTS: Final[frozenset[str]] = frozenset({"-p", "--package"})
# ``npx`` options whose operand is a shell command line, never a package.
_CALL_OPTS: Final[frozenset[str]] = frozenset({"-c", "--call"})


def _option_value(arg: str, options: frozenset[str]) -> str | None:
    """Return the value of an ``--opt=<value>`` argument for *options*.

    Returns:
        The inline value, or ``None`` when ``arg`` is not that form.
    """
    for opt in options:
        prefix = f"{opt}="
        if arg.startswith(prefix):
            return arg[len(prefix) :]
    return None


def npm_package_spec(command: str, args: tuple[str, ...]) -> str | None:
    """Return the npm package spec an ``npx``-style command will run.

    ``npx --package=foo bar`` installs ``foo`` and runs the ``bar`` binary
    from it, so the package option (not the first positional) is what has
    to be pinned; taking the positional would let an unpinned ``foo``
    through unchecked.

    Returns:
        The package operand (e.g. ``@scope/pkg@2.1.0``), or ``None`` when
        the command is not an npx-style launcher.
    """
    # Case-folded: Windows resolves ``NPX``/``Npx.cmd`` to the same launcher,
    # so a case-varied command must not slip past the pin check.
    base = command.rsplit("/", 1)[-1].rsplit("\\", 1)[-1].lower()
    if base not in _NPX_COMMANDS:
        return None
    rest = list(args)
    # ``pnpm dlx`` / ``bunx`` variants: drop a leading ``dlx`` subcommand.
    if base == "pnpm" and rest and rest[0] == "dlx":
        rest = rest[1:]
    elif base == "pnpm":
        return None
    return _first_package_operand(rest)


def _first_package_operand(rest: list[str]) -> str | None:
    """Return the first argument that names a package, if any.

    Returns:
        The package spec, or ``None`` when the arguments name none.
    """
    expect_package = False
    skip_next = False
    for arg in rest:
        if skip_next:
            skip_next = False
            continue
        if expect_package:
            return arg
        if arg in _PACKAGE_OPTS:
            expect_package = True
            continue
        named = _option_value(arg, _PACKAGE_OPTS)
        if named is not None:
            return named
        # ``npx -c '<shell command>'`` runs a command in the npx-augmented
        # shell: the operand is a command line, not a package spec, so
        # treating it as one would reject a legitimate launcher. Any
        # package it needs must be named by an explicit --package, which a
        # later iteration still picks up.
        if arg in _CALL_OPTS:
            skip_next = True
            continue
        if _option_value(arg, _CALL_OPTS) is not None:
            continue
        # Every other npx flag (-y, --yes, --) starts with a dash; the
        # package operand is the first non-flag argument.
        if arg.startswith("-"):
            continue
        return arg
    return None


def npm_spec_is_pinned(spec: str) -> bool:
    """Whether an npm package spec carries an explicit, non-floating pin.

    Returns:
        ``True`` when the spec ends in ``@<version>`` with a concrete
        (non-floating-tag) version.
    """
    remainder = spec.removeprefix("@")
    if "@" not in remainder:
        return False
    version = remainder.rsplit("@", 1)[1]
    return version.lower() not in _FLOATING_NPM_TAGS
