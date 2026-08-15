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
from pathlib import Path
from typing import Final

DEPLOYMENT_LABEL: Final[str] = "synthorg.deployment"
"""Docker label carrying :func:`deployment_id_for` on every sandbox."""

_DIGEST_CHARS: Final[int] = 16
"""Prefix length of the hex digest used as the label value.

Long enough that two workspace roots on one daemon will not collide in
practice, short enough to stay readable in ``docker ps`` output.
"""


def deployment_id_for(workspace_root: Path) -> str:
    """Return the deployment identity for *workspace_root*.

    Args:
        workspace_root: The resolved agent workspace root this backend
            serves.

    Returns:
        A hex digest prefix identifying the deployment.
    """
    # Case-folded because Windows resolves the same directory under
    # differing case, and a deployment that restarted with a differently
    # cased path would otherwise stop recognising its own containers.
    resolved = str(Path(workspace_root).resolve()).casefold()
    return hashlib.sha256(resolved.encode("utf-8")).hexdigest()[:_DIGEST_CHARS]
