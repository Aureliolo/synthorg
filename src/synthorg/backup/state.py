"""Backup feature state slice.

Holds the backup/restore service. ``None`` until wired at boot; the backup
controllers raise 503 on a ``None`` service.
"""

from pydantic import ConfigDict

from synthorg._core.features import BaseFeatureStateSlice
from synthorg.backup.service import BackupService


class BackupStateSlice(BaseFeatureStateSlice):
    """Application-state slice owned by the backup feature."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    service: BackupService | None = None
    #: Whether the operator asked for backups (``backup.enabled``). Set
    #: independently of ``service`` so a construction failure is
    #: distinguishable from a deliberately backup-less run: without it, a
    #: service that could not be built is indistinguishable from one nobody
    #: wanted, and every ``backup.*`` setting silently loses its consumer.
    expected: bool = False
    #: Why construction failed, redacted, when it did. Carried here because
    #: the reason exists only inside the factory's handler and is otherwise
    #: lost the moment it is logged: an operator then sees a subsystem
    #: reporting "absent" with no way to learn that, say, ``pg_dump`` is not
    #: on PATH, which is the difference between a fixable fault and a
    #: mystery. ``None`` whenever the service is wired or was never wanted.
    unavailable_reason: str | None = None
