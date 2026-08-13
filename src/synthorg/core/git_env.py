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

That last one costs the ``file`` transport, whose default policy is
``user`` rather than ``always``, and a local path is the file transport
too. The agent-facing tools give it up outright. The system-internal
backends cannot: an embedded workspace IS a bare repository at a path the
system computed, so :data:`LOCAL_TRANSPORT_GIT_CONFIG` restores that one
transport for them rather than dropping the override that confines every
other.
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

#: Re-permits the ``file`` transport for the paths whose repositories the
#: system itself names: the workspace git backends, the docs engine and the
#: project brain all address bare repositories and worktrees by local path,
#: and none of them takes a URL from an agent. Carried as git config through
#: :func:`git_config_env` rather than by relaxing
#: :data:`GIT_HARDENING_OVERRIDES`, so an exotic transport reached through a
#: URL stays refused on these paths and the agent-facing tools, which have no
#: local repository to address, keep the file transport closed as well.
LOCAL_TRANSPORT_GIT_CONFIG: Final[MappingProxyType[str, str]] = MappingProxyType(
    {"protocol.file.allow": "always"}
)

#: Makes git create group-writable files and setgid directories inside the
#: repositories the system provisions, which is the same contract
#: :mod:`synthorg.core.workspace_sharing` states for the files an agent
#: writes: a sandbox runs as its own uid and reaches the workspace through
#: the backend's group, so anything git leaves at the process umask (`.git`
#: itself, a worktree root, a checked-out tree) is a directory the sandbox
#: can read and traverse but never write. Set here rather than at each
#: ``mkdir`` because git creates most of these itself, and a rule applied to
#: the handful of directories this code makes would miss them.
SHARED_GROUP_GIT_CONFIG: Final[MappingProxyType[str, str]] = MappingProxyType(
    {"core.sharedRepository": "group"}
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


__all__ = [
    "GIT_HARDENING_OVERRIDES",
    "LOCAL_TRANSPORT_GIT_CONFIG",
    "SHARED_GROUP_GIT_CONFIG",
    "git_config_env",
]
