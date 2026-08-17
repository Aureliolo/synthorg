# module-kind: code
"""The request shape a surface renders, with its client resolved by name."""

from collections.abc import Mapping
from typing import Self

from pydantic import Field

from synthorg.api.state import AppState
from synthorg.client.models import ClientRequest
from synthorg.client.state import client_simulation_state_of
from synthorg.core.types import NotBlankStr


class ClientRequestRow(ClientRequest):
    """A request, plus the name of the client that submitted it.

    ``client_id`` is a key the operator chose the slug for, not the name the
    client goes by: ``ClientProfile`` carries a separate ``name``. The row
    resolves it once per response, and answers ``None`` when the pool no longer
    holds that client, which the surface words itself rather than falling back
    to the key.
    """

    client_name: NotBlankStr | None = Field(
        default=None,
        description="Display name of the submitting client, when it is known",
    )

    @classmethod
    def of(cls, request: ClientRequest, names: Mapping[str, str]) -> Self:
        """Build the row for *request*.

        Returns:
            The request with its client resolved.
        """
        resolved = names.get(request.client_id)
        return cls(
            **dict(request),
            client_name=NotBlankStr(resolved) if resolved else None,
        )


async def client_names(app_state: AppState) -> dict[str, str]:
    """Client id to display name, read once per response.

    Best-effort in one direction only: a pool that cannot be read yields an
    empty map and every row then names nobody, which is the honest answer and
    never the key.

    Returns:
        The map, empty when the pool is unreachable.
    """
    sim_state = client_simulation_state_of(app_state)
    profiles = await sim_state.pool.list_profiles()
    return {profile.client_id: str(profile.name) for profile in profiles}
