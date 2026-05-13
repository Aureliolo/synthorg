"""Regression: ``_do_backup_as_dict`` validates manifest before caching.

The idempotency layer caches the JSON dict returned by the callback.
If the dict cannot round-trip back into a ``BackupManifest``, we want
the validation to fail INSIDE the callback so the idempotency cache
never receives a corrupt entry (which would be served verbatim on
every subsequent request with the same key).
"""

import pytest
from pydantic import ValidationError

from synthorg.api.controllers.backup import _do_backup_as_dict
from synthorg.backup.models import BackupManifest
from synthorg.core.types import NotBlankStr

pytestmark = pytest.mark.unit


def _good_manifest() -> BackupManifest:
    """Return a minimal valid BackupManifest for the happy-path test."""
    return BackupManifest(
        synthorg_version=NotBlankStr("0.7.2"),
        timestamp=NotBlankStr("2026-05-13T00:00:00+00:00"),
        trigger="manual",  # type: ignore[arg-type]
        components=(),
        size_bytes=128,
        checksum=NotBlankStr(f"sha256:{'a' * 64}"),
        backup_id=NotBlankStr("abc123def456"),
    )


async def _good_callable() -> BackupManifest:
    return _good_manifest()


class TestBackupCallbackValidation:
    async def test_happy_path_returns_dict(self) -> None:
        dumped = await _do_backup_as_dict(_good_callable)
        assert isinstance(dumped, dict)
        assert dumped["backup_id"] == "abc123def456"

    async def test_invalid_manifest_raises_before_caching(self) -> None:
        """A malformed manifest must surface as ValidationError, not be cached."""

        async def corrupt() -> BackupManifest:
            # Bypass the model_copy validator by reaching directly into
            # the frozen model's __dict__ -- the round-trip
            # model_validate call inside _do_backup_as_dict is the
            # invariant under test, not the model's own validators.
            manifest = _good_manifest()
            object.__setattr__(manifest, "size_bytes", -1)
            return manifest

        with pytest.raises(ValidationError):
            await _do_backup_as_dict(corrupt)
