# module-kind: code
"""Stable per-deployment identity carried on sandbox container labels.

Two SynthOrg backends can share one Docker daemon: the shipped stack, and a
development backend swapped into that same stack. ``synthorg.managed=true``
alone therefore cannot separate "a container this deployment created" from
"a container another deployment is still using", and the boot reconciliation
pass acts on exactly that distinction: what it reads as an orphan it stops
and removes. Getting it wrong kills live agent work rather than reclaiming
debris.

The identity is derived from the resolved agent workspace root rather than
stored anywhere. That root is what a deployment already owns exclusively:
two backends sharing it share their containers' fate by construction (which
is precisely the shipped-stack-plus-dev-backend case, and there the shared
answer is the correct one), while a backend pointed at a different root is a
different deployment. Deriving also keeps the label free of any stored
identifier, so nothing identifying the operator or their filesystem layout
travels into Docker metadata.
"""

import hashlib
import os
from pathlib import Path
from typing import Final

DEPLOYMENT_LABEL: Final[str] = "synthorg.deployment"
"""Docker label carrying :func:`deployment_id_for` on every sandbox."""

_DIGEST_CHARS: Final[int] = 16
"""Prefix length of the hex digest used as the label value.

Long enough that two workspace roots on one daemon will not collide in
practice, short enough to stay readable in ``docker ps`` output.
"""


def normalised_path(path: Path | str) -> str:
    """Return *path* resolved and normalised for this platform's case rules.

    ``os.path.normcase`` rather than ``casefold``: Windows resolves one
    directory under differing case, so a deployment that restarted with a
    differently cased path must still recognise its own containers, while
    POSIX treats ``/work/A`` and ``/work/a`` as two directories and folding
    them together would hand one deployment's containers to the other.

    Args:
        path: A filesystem path.

    Returns:
        The normalised absolute path, as a string.
    """
    return os.path.normcase(str(Path(path).resolve()))


def path_is_within(candidate: Path | str, root: Path | str) -> bool:
    """Report whether *candidate* is *root* or lies beneath it.

    Args:
        candidate: The path under test.
        root: The directory it must be inside.

    Returns:
        ``True`` when *candidate* is inside *root*.
    """
    candidate_parts = Path(normalised_path(candidate)).parts
    root_parts = Path(normalised_path(root)).parts
    return candidate_parts[: len(root_parts)] == root_parts


def deployment_id_for(workspace_root: Path) -> str:
    """Return the deployment identity for *workspace_root*.

    Args:
        workspace_root: The resolved agent workspace root this backend
            serves.

    Returns:
        A hex digest prefix identifying the deployment.
    """
    resolved = normalised_path(workspace_root)
    return hashlib.sha256(resolved.encode("utf-8")).hexdigest()[:_DIGEST_CHARS]
