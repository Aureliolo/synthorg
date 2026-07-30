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
