# module-kind: adapter
"""aiodocker implementation of the reconciliation Docker surface.

:mod:`synthorg.tools.sandbox.reconciliation` is a pure function over a
three-method protocol so it can be tested without a daemon. This module is
the production side of that seam: the narrow set of daemon calls the boot
reconciliation pass needs, and nothing else.

The label filter is applied by the daemon rather than in Python. A shared
Docker host routinely carries containers belonging to other tools and other
people, so the query itself is the boundary that keeps them out of the
candidate set: nothing downstream can act on a container the daemon never
returned.
"""

import json
from collections.abc import Sequence

import aiodocker

from synthorg.tools.sandbox.deployment_identity import DEPLOYMENT_LABEL
from synthorg.tools.sandbox.reconciliation import (
    MANAGED_LABEL,
    MANAGED_LABEL_VALUE,
    ManagedContainer,
)


class AiodockerReconcileClient:
    """Reconciliation Docker surface backed by ``aiodocker``.

    Implements :class:`~synthorg.tools.sandbox.reconciliation.DockerClientProtocol`.
    """

    def __init__(self, docker: aiodocker.Docker) -> None:
        """Bind the client to a connected ``aiodocker`` instance.

        Args:
            docker: A connected client. Ownership stays with the caller,
                which is what closes it.
        """
        self._docker = docker

    async def list_managed_containers(self) -> Sequence[ManagedContainer]:
        """List every container carrying the managed label, running or not.

        ``all=True`` because a stopped orphan is still occupying disk and
        still pinning the image it was created from, which is what blocks
        an image from being reclaimed later.

        Returns:
            Result of type ``Sequence[ManagedContainer]``.
        """
        label_selector = f"{MANAGED_LABEL}={MANAGED_LABEL_VALUE}"
        containers = await self._docker.containers.list(
            all=True,
            filters=json.dumps({"label": [label_selector]}),
        )
        return [
            ManagedContainer(
                container_id=container["Id"],
                deployment_id=(container["Labels"] or {}).get(DEPLOYMENT_LABEL),
                created_at=float(container["Created"]),
            )
            for container in containers
        ]

    async def stop_container(self, container_id: str) -> None:
        """Stop a container by id."""
        await self._docker.containers.container(container_id).stop()

    async def remove_container(self, container_id: str) -> None:
        """Remove a container by id, taking its anonymous volumes with it.

        ``v=True`` because each sandbox leaves an anonymous volume behind,
        and removing the container without it converts one leak into a
        quieter one that no container listing will ever show.
        """
        await self._docker.containers.container(container_id).delete(force=True, v=True)
