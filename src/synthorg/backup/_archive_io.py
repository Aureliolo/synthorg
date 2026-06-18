# module-kind: code
"""Pure tar/checksum/manifest I/O for the backup service.

Blocking filesystem primitives the backup service drives through
``asyncio.to_thread``: directory checksumming, tar.gz pack/unpack with
path-traversal guards, and manifest extraction. Kept free of service
state so both the archive mixin and the top-level service call them
directly.
"""

import hashlib
import json
import tarfile
from pathlib import Path
from typing import Final

from pydantic import ValidationError

from synthorg.backup.errors import ManifestError
from synthorg.backup.models import BackupManifest
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.backup import BACKUP_MANIFEST_INVALID

logger = get_logger(__name__)

_CHECKSUM_CHUNK_SIZE: Final[int] = 65536
# Internal constant by design: defensive cap on serialised backup
# manifest size; prevents manifest bloat from runaway tags or notes.
# Not exposed to the settings registry.
_MANIFEST_MAX_SIZE: Final[int] = 65536


def compute_checksum(directory: Path) -> str:
    """Compute SHA-256 checksum of all files in a directory.

    Returns:
        The hex SHA-256 digest over the directory's files (excluding
        ``manifest.json`` and symlinks), path-prefixed for stability.
    """
    hasher = hashlib.sha256()
    for filepath in sorted(directory.rglob("*")):
        if (
            filepath.is_file()
            and not filepath.is_symlink()
            and filepath.name != "manifest.json"
        ):
            rel = filepath.relative_to(directory).as_posix()
            hasher.update(rel.encode("utf-8"))
            with filepath.open("rb") as fh:
                while chunk := fh.read(_CHECKSUM_CHUNK_SIZE):
                    hasher.update(chunk)
    return hasher.hexdigest()


def compress_dir(source_dir: Path, archive_path: Path) -> None:
    """Create a tar.gz archive from a directory."""
    with tarfile.open(archive_path, "w:gz") as tar:
        for item in source_dir.iterdir():
            tar.add(item, arcname=item.name)


def extract_tar(archive_path: Path, target_dir: Path) -> None:
    """Extract a tar.gz archive to a target directory.

    Raises:
        ManifestError: When a member has an absolute / traversal path
            or an unsafe symlink target.
    """
    target_dir.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive_path, "r:gz") as tar:
        for member in tar.getmembers():
            if member.name.startswith("/") or ".." in Path(member.name).parts:
                msg = f"Unsafe path in archive: {member.name}"
                logger.warning(
                    BACKUP_MANIFEST_INVALID,
                    reason="unsafe_archive_member_path",
                    error_type=ManifestError.__name__,
                )
                raise ManifestError(msg)
            if member.issym() or member.islnk():
                linkname = member.linkname
                if linkname.startswith("/") or ".." in Path(linkname).parts:
                    msg = (
                        f"Unsafe symlink target in archive: {member.name} -> {linkname}"
                    )
                    logger.warning(
                        BACKUP_MANIFEST_INVALID,
                        reason="unsafe_archive_symlink_target",
                        error_type=ManifestError.__name__,
                    )
                    raise ManifestError(msg)
        tar.extractall(target_dir, filter="data")


def read_manifest_from_archive(archive_path: Path) -> BackupManifest | None:
    """Read manifest.json from a tar.gz archive.

    Returns:
        The parsed manifest, or ``None`` when the archive has no
        manifest, exceeds the size limit, or is corrupt / invalid.
    """
    try:
        with tarfile.open(archive_path, "r:gz") as tar:
            try:
                member = tar.getmember("manifest.json")
            except KeyError:
                return None
            extracted = tar.extractfile(member)
            if extracted is None:
                return None
            with extracted as f:
                raw = f.read(_MANIFEST_MAX_SIZE + 1)
            if len(raw) > _MANIFEST_MAX_SIZE:
                logger.warning(
                    BACKUP_MANIFEST_INVALID,
                    path=str(archive_path),
                    error="manifest.json exceeds size limit",
                    read_bytes=len(raw),
                    max_bytes=_MANIFEST_MAX_SIZE,
                    member=member.name,
                )
                return None
            data = json.loads(raw)
            return BackupManifest.model_validate(data)
    except tarfile.TarError as exc:
        logger.warning(
            BACKUP_MANIFEST_INVALID,
            path=str(archive_path),
            category="archive_corrupt",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return None
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        logger.warning(
            BACKUP_MANIFEST_INVALID,
            path=str(archive_path),
            category="json_parse_failed",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return None
    except ValidationError as exc:
        logger.warning(
            BACKUP_MANIFEST_INVALID,
            path=str(archive_path),
            category="schema_validation_failed",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return None
    except OSError as exc:
        logger.warning(
            BACKUP_MANIFEST_INVALID,
            path=str(archive_path),
            category="io_error",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return None
