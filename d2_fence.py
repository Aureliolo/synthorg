"""Standalone D2 fence formatter for zensical builds.

Zensical does not run mkdocs plugin lifecycle hooks, so mkdocs-d2-plugin's
``on_config`` hook (which registers the D2 custom fence in pymdownx.superfences)
never fires. This module exposes ``validator`` and ``formatter`` at module level
so they can be referenced directly in ``mkdocs.yml`` via ``!!python/name:``.

D2 config is hardcoded here to match the values in mkdocs.yml's d2 plugin
section. Keep them in sync when changing D2 settings.
"""

import subprocess
from functools import partial

from d2.fence import D2CustomFence
from d2.plugin import render as _render

_D2_NOT_FOUND = (
    "D2 executable not found on PATH. Install from https://d2lang.com/tour/install"
)

_D2_CONFIG = {
    "layout": "dagre",
    "theme": 200,
    "dark_theme": -1,
    "sketch": False,
    "pad": 100,
    "scale": -1.0,
    "force_appendix": False,
    "target": "''",
}


def _build_fence(executable: str = "d2") -> D2CustomFence:
    """Build a D2CustomFence with hardcoded config matching mkdocs.yml.

    Args:
        executable: Name of the D2 binary to probe for the version check.

    Returns:
        A configured D2CustomFence ready to register in superfences.

    Raises:
        RuntimeError: If the D2 executable is missing, times out, or exits
            non-zero during the version check.
    """
    try:
        subprocess.run(  # noqa: S603
            [executable, "--version"],
            capture_output=True,
            check=True,
            timeout=5,
        )
    except FileNotFoundError:
        raise RuntimeError(_D2_NOT_FOUND) from None
    except subprocess.TimeoutExpired:
        msg = f"D2 executable '{executable}' timed out during version check"
        raise RuntimeError(msg) from None
    except subprocess.CalledProcessError as exc:
        msg = f"D2 executable '{executable}' exited with code {exc.returncode}"
        raise RuntimeError(msg) from exc

    renderer = partial(_render, executable, None)
    return D2CustomFence(dict(_D2_CONFIG), renderer)


_fence = _build_fence()
validator = _fence.validator
formatter = _fence.formatter
