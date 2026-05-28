"""Backup feature state slice.

Holds the backup/restore service. ``None`` until wired at boot; the backup
controllers raise 503 on a ``None`` service.
"""

from pydantic import ConfigDict

from synthorg._core.features import BaseFeatureStateSlice
from synthorg.backup.service import BackupService


class BackupStateSlice(BaseFeatureStateSlice):
    """Application-state slice owned by the backup feature."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    service: BackupService | None = None
