# module-kind: adapter
"""The charter store answering the spine's authorisation question.

One read per brief that forces a plan, which is once per initiative rather
than once per task, so it is asked directly rather than cached: a cache
here would answer from before the operator's decision, which is the one
moment the answer changes.
"""

from synthorg.core.types import NotBlankStr
from synthorg.engine.pipeline.charter_authority_port import CharterAuthorisation
from synthorg.meta.charter.enums import CharterStatus
from synthorg.persistence.charter_protocol import CharterRepository


class CharterStoreAuthority:
    """Reads authorisation straight off the charter row.

    Args:
        charter_repo: The durable charter store.
    """

    __slots__ = ("_charter_repo",)

    def __init__(self, charter_repo: CharterRepository) -> None:
        self._charter_repo = charter_repo

    async def authorisation_of(self, charter_id: str) -> CharterAuthorisation:
        """Return what the charter store says about *charter_id*.

        Returns:
            The verdict for the named charter.
        """
        charter = await self._charter_repo.get(NotBlankStr(charter_id))
        if charter is None:
            return CharterAuthorisation.UNKNOWN
        if charter.status is CharterStatus.APPROVED:
            return CharterAuthorisation.APPROVED
        return CharterAuthorisation.UNDECIDED


__all__ = ["CharterStoreAuthority"]
