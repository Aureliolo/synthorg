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

from synthorg.core.npm_version import is_exact_npm_version

# Commands that resolve+run an npm package on the fly. A hand-authored
# stdio server using one MUST pin an explicit version so a reconnect can
# never silently pull a newer (and un-reviewed) package version.
_NPX_COMMANDS: Final[frozenset[str]] = frozenset({"npx", "npx.cmd", "pnpm", "bunx"})
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


def _pnpm_dlx_args(rest: list[str]) -> list[str] | None:
    """Strip a ``pnpm`` invocation down to its ``dlx`` arguments.

    pnpm accepts its own options before the subcommand, so
    ``pnpm --package=floating dlx bin`` is a resolve-and-run launcher even
    though ``dlx`` is not the first token. Matching only ``rest[0]`` let
    that form skip the pin check entirely.

    Returns:
        The arguments after ``dlx`` (with any leading pnpm options kept,
        since a ``--package`` among them still names an installed
        package), or ``None`` when this is not a ``dlx`` invocation and
        so resolves nothing on the fly.
    """
    skip_next = False
    for index, arg in enumerate(rest):
        if skip_next:
            skip_next = False
            continue
        if arg == "dlx":
            return rest[:index] + rest[index + 1 :]
        # A split option (``-p <spec>`` / ``--call <cmd>``) carries its value
        # in the next token, which must not be mistaken for the subcommand.
        if arg in _PACKAGE_OPTS or arg in _CALL_OPTS:
            skip_next = True
            continue
        # The first remaining non-option token is the subcommand. Anything
        # other than ``dlx`` (``run``, ``install``, ...) executes code that
        # is already installed, so there is nothing resolved at spawn time.
        if not arg.startswith("-"):
            return None
    return None


def npm_package_specs(command: str, args: tuple[str, ...]) -> tuple[str, ...]:
    """Return every npm package spec an ``npx``-style command installs.

    ``--package`` is repeatable and npx installs each one, so stopping at
    the first spec would clear
    ``npx --package=safe@1.0.0 --package=whatever`` on the strength of the
    pinned half while the floating half still resolves fresh on every
    reconnect. Every operand is returned so the caller can reject on any
    of them.

    ``npx --package=foo bar`` installs ``foo`` and runs the ``bar`` binary
    from it, so once any ``--package`` is present the positional is a
    binary name, not a package.

    Returns:
        Each package operand (e.g. ``@scope/pkg@2.1.0``), or an empty
        tuple when the command is not an npx-style launcher or names no
        package.
    """
    # Case-folded: Windows resolves ``NPX``/``Npx.cmd`` to the same launcher,
    # so a case-varied command must not slip past the pin check.
    base = command.rsplit("/", 1)[-1].rsplit("\\", 1)[-1].lower()
    if base not in _NPX_COMMANDS:
        return ()
    rest = list(args)
    if base == "pnpm":
        dlx_args = _pnpm_dlx_args(rest)
        if dlx_args is None:
            return ()
        rest = dlx_args
    named, positional = _package_operands(rest)
    if named:
        return tuple(named)
    return (positional,) if positional is not None else ()


def _package_operands(rest: list[str]) -> tuple[list[str], str | None]:
    """Split arguments into explicit package operands and the positional.

    Scanning stops at the first bare argument: npx's own options end
    there and everything after is forwarded to the spawned binary, so
    ``npx pkg@1.2.3 --package=floating`` passes ``--package=floating`` to
    ``pkg`` rather than installing a second package. Reading past the
    positional would both miss the real package and reject a launcher
    over an argument npx never interprets.

    Returns:
        Every ``--package`` operand, and the first bare argument (the
        package only when no ``--package`` was given).
    """
    named: list[str] = []
    positional: str | None = None
    expect_package = False
    skip_next = False
    for arg in rest:
        if skip_next:
            skip_next = False
            continue
        if expect_package:
            named.append(arg)
            expect_package = False
            continue
        if arg in _PACKAGE_OPTS:
            expect_package = True
            continue
        value = _option_value(arg, _PACKAGE_OPTS)
        if value is not None:
            named.append(value)
            continue
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
        positional = arg
        break
    return named, positional


def unpinned_npm_packages(command: str, args: tuple[str, ...]) -> tuple[str, ...]:
    """Return the package specs *command* installs without a version pin.

    Returns:
        Every floating or unpinned spec, in command-line order; empty when
        the command pins everything it installs (or installs nothing).
    """
    specs = npm_package_specs(command, args)
    return tuple(spec for spec in specs if not npm_spec_is_pinned(spec))


def npm_spec_is_pinned(spec: str) -> bool:
    """Whether an npm package spec carries an explicit, non-floating pin.

    Returns:
        ``True`` when the spec ends in ``@<version>`` with an exact semver
        version; ``False`` for a dist-tag, a range, or a bare name.
    """
    remainder = spec.removeprefix("@")
    if "@" not in remainder:
        return False
    return is_exact_npm_version(remainder.rsplit("@", 1)[1])


def npm_spec_name(spec: str) -> str:
    """Return the package name of *spec*, without its version selector.

    The leading ``@`` of a scoped package is not a version separator, so
    only a later ``@`` splits the name from the selector.

    Returns:
        The package name (e.g. ``@scope/pkg`` for ``@scope/pkg@latest``).
    """
    scope = "@" if spec.startswith("@") else ""
    remainder = spec.removeprefix("@")
    return scope + remainder.rsplit("@", 1)[0] if "@" in remainder else spec
