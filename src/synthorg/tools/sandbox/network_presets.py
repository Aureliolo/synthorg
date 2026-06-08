"""Pre-defined network rule sets for common development scenarios.

Each preset is a tuple of ``host:port`` strings that can be merged
into ``DockerSandboxConfig.allowed_hosts`` via the
``network_presets`` field.  The sidecar proxy only sees the final
flattened list -- presets are a Python-side convenience.
"""

from collections.abc import Mapping
from types import MappingProxyType
from typing import Final

PRESET_PYTHON_DEV: Final[tuple[str, ...]] = (
    "pypi.org:443",
    "files.pythonhosted.org:443",
)

PRESET_NODE_DEV: Final[tuple[str, ...]] = ("registry.npmjs.org:443",)

PRESET_GIT: Final[tuple[str, ...]] = (
    "github.com:443",
    "gitlab.com:443",
    "bitbucket.org:443",
)

PRESETS: Final[Mapping[str, tuple[str, ...]]] = MappingProxyType(
    {
        "python-dev": PRESET_PYTHON_DEV,
        "node-dev": PRESET_NODE_DEV,
        "git": PRESET_GIT,
    },
)
