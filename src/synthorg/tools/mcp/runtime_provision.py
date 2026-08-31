# module-kind: code
"""What the MCP runtime image can actually launch.

A catalog entry's launch names a program (``npx``, and one day ``uvx`` or
``deno``). The container it runs in is the sandbox image, so an entry naming a
program that image does not carry is not "misconfigured": it is unlaunchable,
and every attempt ends in the same ``FileNotFoundError`` at connect time, on
every boot, in a log line nobody reads.

The mapping below is the declaration an operator's install is checked against,
and ``check_mcp_catalog_launchable.py`` holds it to ``docker/sandbox/apko.yaml``:
a program declared here with no package installing it fails the gate, so
dropping a package from the image breaks the build rather than the next
reconnect.
"""

from collections.abc import Mapping
from types import MappingProxyType
from typing import Final

#: Program a launch may name, mapped to the apko package that installs it in
#: the sandbox image. Interpreters and package runners only: this answers
#: "can this image start the server at all", not what the server may then do.
RUNTIME_PROGRAMS: Final[Mapping[str, str]] = MappingProxyType(
    {
        "bash": "bash",
        "git": "git",
        "node": "nodejs-26",
        "npm": "npm-12",
        "npx": "npm-12",
        "python": "python-3.14",
        "python3": "python-3.14",
        "sh": "busybox",
    }
)


def image_provides(command: str) -> bool:
    """Whether the MCP runtime image can exec *command*.

    Returns:
        ``True`` when the image ships the program.
    """
    return command in RUNTIME_PROGRAMS


def provided_programs() -> str:
    """Render the launchable programs for an operator-facing message.

    Returns:
        The programs, comma-separated and sorted.
    """
    return ", ".join(sorted(RUNTIME_PROGRAMS))


__all__ = ["RUNTIME_PROGRAMS", "image_provides", "provided_programs"]
