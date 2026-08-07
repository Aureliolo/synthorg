# module-kind: code
"""Environment hardening every git subprocess in the tree spawns under.

Two independent paths shell out to ``git``: the agent-facing tools and
the workspace git backends. Both spawn it against content an agent
authored, so both need the same four overrides, and stating them twice
is how one path ends up hardened and the other does not.

``GIT_TERMINAL_PROMPT=0`` stops a credential prompt turning a failed
clone into a subprocess blocked until its timeout. ``GIT_CONFIG_NOSYSTEM``
and ``GIT_CONFIG_GLOBAL`` cut the host's own git configuration out of the
picture, so an operator's ``insteadOf`` rewrite, alias or hook path cannot
change what a backend command does. ``GIT_PROTOCOL_FROM_USER=0`` marks
every URL as untrusted input, which confines the transports git will use
to the ones ``protocol.allow`` permits rather than the wider set it grants
a URL a human typed.
"""

import os
from collections.abc import Mapping
from types import MappingProxyType
from typing import Final

#: Applied on top of a sanitised environment, so they win over anything
#: inherited.
GIT_HARDENING_OVERRIDES: Final[MappingProxyType[str, str]] = MappingProxyType(
    {
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_PROTOCOL_FROM_USER": "0",
    }
)


def git_config_env(config: Mapping[str, str]) -> dict[str, str]:
    """Render *config* as the ``GIT_CONFIG_*`` variables git reads.

    The alternatives both persist the value somewhere it outlives the
    command: ``git -c`` puts it in the process arguments, which every
    other process on the host can read, and a repository-level write puts
    it in ``.git/config`` inside an agent-writable workspace a
    devcontainer build can copy into an image layer. The environment is
    the only channel scoped to the one invocation, and it survives
    :data:`GIT_HARDENING_OVERRIDES` disabling the system and global config
    files because git reads these regardless.

    Args:
        config: Fully-qualified git config keys mapped to their values,
            for example ``{"url.https://user:tok@host/.insteadOf":
            "https://host/"}``.

    Returns:
        The environment variables to merge into the child's environment.
        Empty when *config* is empty, so no ``GIT_CONFIG_COUNT`` is set.
    """
    if not config:
        return {}
    rendered = {"GIT_CONFIG_COUNT": str(len(config))}
    for index, (key, value) in enumerate(config.items()):
        rendered[f"GIT_CONFIG_KEY_{index}"] = key
        rendered[f"GIT_CONFIG_VALUE_{index}"] = value
    return rendered


__all__ = ["GIT_HARDENING_OVERRIDES", "git_config_env"]
