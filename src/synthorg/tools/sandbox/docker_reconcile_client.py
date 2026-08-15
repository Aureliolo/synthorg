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
from pydantic import BaseModel, ConfigDict, Field

from synthorg.core.boundary import parse_typed
from synthorg.tools.sandbox._mount_paths import CONTAINER_WORKSPACE
from synthorg.tools.sandbox.deployment_identity import DEPLOYMENT_LABEL
from synthorg.tools.sandbox.reconciliation import (
    MANAGED_LABEL,
    MANAGED_LABEL_VALUE,
    ManagedContainer,
)


class _DaemonMount(BaseModel):  # lint-allow: frozen-extra-forbid -- daemon payload
    """One entry of a container's ``Mounts`` array."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="ignore")

    destination: str = Field(default="", alias="Destination")
    source: str = Field(default="", alias="Source")


class _DaemonContainer(BaseModel):  # lint-allow: frozen-extra-forbid -- daemon payload
    """The subset of ``GET /containers/json`` this pass reads.

    Parsed rather than indexed because the daemon's response is external
    input: a missing key or a differently-typed field would otherwise
    surface as a raw ``KeyError`` or ``ValueError`` from inside the
    reconciliation pass, where the failure reads as a reconciliation bug
    rather than a response that did not match expectations.

    ``extra="ignore"``, unlike everywhere we own both ends of the wire: a
    container object carries dozens of fields this pass has no use for, and
    forbidding them would reject every genuine response the moment Docker
    adds one. What must not be tolerated is a field we DO read arriving
    absent or wrong, and that the declarations below still refuse.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="ignore")

    container_id: str = Field(alias="Id")
    labels: dict[str, str] | None = Field(default=None, alias="Labels")
    created: float = Field(default=0.0, alias="Created")
    mounts: tuple[_DaemonMount, ...] = Field(default=(), alias="Mounts")

    def workspace_source(self) -> str | None:
        """Return the host path mounted as the container workspace.

        Returns:
            The mount source, or ``None`` when the container has no
            workspace mount.
        """
        for mount in self.mounts:
            if mount.destination == CONTAINER_WORKSPACE:
                return mount.source or None
        return None


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
        parsed = [
            parse_typed(
                "docker.containers.list",
                container._container,  # noqa: SLF001 -- aiodocker exposes the raw mapping only here
                _DaemonContainer,
            )
            for container in containers
        ]
        return [
            ManagedContainer(
                container_id=item.container_id,
                deployment_id=(item.labels or {}).get(DEPLOYMENT_LABEL),
                created_at=item.created,
                workspace_source=item.workspace_source(),
            )
            for item in parsed
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
