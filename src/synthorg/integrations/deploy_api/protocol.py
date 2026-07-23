"""Vendor-neutral deploy-platform client surface.

The governed deploy tools speak only this protocol, so a platform preset
can be added without the tool layer, its approval gating, or its tests
learning anything about a specific vendor.
"""

from enum import StrEnum
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict

from synthorg.core.types import NotBlankStr


class DeployState(StrEnum):
    """Where a deployment sits in its lifecycle.

    Platform-specific states are normalised onto these leaves so an agent
    (and the tests) never branch on vendor vocabulary.
    """

    QUEUED = "queued"
    BUILDING = "building"
    READY = "ready"
    FAILED = "failed"
    CANCELLED = "cancelled"


class Deployment(BaseModel):
    """One deployment record."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    id: NotBlankStr
    state: DeployState
    url: str = ""
    created_at: str = ""


class DeployLogLine(BaseModel):
    """One line of a deployment's build or runtime log.

    A line always carries content: an empty or whitespace-only event is
    noise a parser drops rather than emits, so ``text`` is non-blank.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    timestamp: str = ""
    text: NotBlankStr


@runtime_checkable
class DeployApiClient(Protocol):
    """The deploy operations a governed deploy tool can perform.

    A client is bound to one project *and one environment* at construction,
    from the resolved connection record. Keeping both off the call surface
    means an agent cannot name a project the operator did not configure, nor
    escalate a staging target to a production release through an argument.
    """

    @property
    def project(self) -> NotBlankStr:
        """The operator-configured project this client is bound to.

        Exposed so the binding is a structural fact a caller (and every
        implementation's tests) can assert against, not a docstring promise.
        """
        ...

    async def trigger_deployment(self, *, git_ref: str) -> Deployment:
        """Start a deployment of ``git_ref`` for the bound project + environment."""
        ...

    async def get_deployment(self, *, deployment_id: NotBlankStr) -> Deployment:
        """Fetch one deployment's current state."""
        ...

    async def list_deployments(self, *, limit: int) -> tuple[Deployment, ...]:
        """List recent deployments for the bound project, newest first."""
        ...

    async def get_deployment_logs(
        self, *, deployment_id: NotBlankStr, limit: int
    ) -> tuple[DeployLogLine, ...]:
        """Fetch a deployment's log lines."""
        ...

    async def aclose(self) -> None:
        """Release the underlying transport."""
        ...


__all__ = [
    "DeployApiClient",
    "DeployLogLine",
    "DeployState",
    "Deployment",
]
