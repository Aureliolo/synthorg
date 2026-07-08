# module-kind: adapter
"""Pluggable storage for generated design assets.

Two backends satisfy :class:`DesignAssetStore`:

* :class:`InMemoryDesignAssetStore` -- a process-local registry (the
  historical ``AssetManagerTool`` behaviour), used when no
  ``asset_storage_path`` is configured.
* :class:`FilesystemDesignAssetStore` -- durable, path-traversal-guarded
  storage that writes each asset's bytes plus a JSON metadata sidecar under
  a configured directory, so generated images survive a restart and stay
  queryable.

Metadata operations are synchronous (small JSON); callers persisting large
image bytes should invoke :meth:`save_content` off the event loop.
"""

import copy
import json
import re
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Final, Protocol, runtime_checkable
from uuid import uuid4

from pydantic import JsonValue

from synthorg.core.critical_errors import reraise_critical
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.design import (
    DESIGN_ASSET_PERSIST_FAILED,
    DESIGN_ASSET_PERSISTED,
)

logger = get_logger(__name__)

_SAFE_ID_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$"
)
"""Asset ids are restricted to a filename-safe token (no path separators)."""

_EXT_BY_MIME: Final[Mapping[str, str]] = {
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/webp": "webp",
    "image/gif": "gif",
}
_DEFAULT_EXT: Final[str] = "bin"
_METADATA_SUFFIX: Final[str] = ".json"


def _require_safe_id(asset_id: str) -> str:
    """Validate an asset id is a filename-safe token.

    Returns:
        The validated asset id.

    Raises:
        ValueError: If the id contains path separators or other unsafe
            characters.
    """
    if not _SAFE_ID_PATTERN.match(asset_id):
        msg = f"asset_id must match {_SAFE_ID_PATTERN.pattern!r}, got {asset_id!r}"
        raise ValueError(msg)
    return asset_id


def _ext_for(content_type: str) -> str:
    """Return the file extension for a content type.

    Returns:
        The extension (without a dot), or ``"bin"`` for unknown types.
    """
    return _EXT_BY_MIME.get(content_type, _DEFAULT_EXT)


@runtime_checkable
class DesignAssetStore(Protocol):
    """Storage interface for generated design-asset metadata + content."""

    def register(self, asset_id: str, metadata: dict[str, JsonValue]) -> None:
        """Persist (or replace) an asset's metadata."""
        ...

    def get(self, asset_id: str) -> dict[str, JsonValue] | None:
        """Return an asset's metadata, or ``None`` when absent."""
        ...

    def delete(self, asset_id: str) -> bool:
        """Delete an asset's metadata (and content); ``True`` if removed."""
        ...

    def items(self) -> Mapping[str, dict[str, JsonValue]]:
        """Return a snapshot mapping of asset id to metadata."""
        ...

    def save_content(self, asset_id: str, content: bytes, *, content_type: str) -> int:
        """Persist an asset's binary content; returns bytes written."""
        ...

    def load_content(self, asset_id: str) -> bytes | None:
        """Return an asset's binary content, or ``None`` when absent."""
        ...


class InMemoryDesignAssetStore:
    """Process-local, non-durable asset store (default when no path is set)."""

    def __init__(
        self,
        assets: dict[str, dict[str, JsonValue]] | None = None,
    ) -> None:
        """Seed the store, deep-copying any pre-existing assets."""
        self._meta: dict[str, dict[str, JsonValue]] = (
            copy.deepcopy(assets) if assets else {}
        )
        self._content: dict[str, bytes] = {}

    def register(self, asset_id: str, metadata: dict[str, JsonValue]) -> None:
        """Store metadata under ``asset_id`` (deep-copied)."""
        self._meta[_require_safe_id(asset_id)] = copy.deepcopy(metadata)

    def get(self, asset_id: str) -> dict[str, JsonValue] | None:
        """Return a deep copy of the metadata, or ``None``.

        Returns:
            The metadata copy, or ``None`` when absent.
        """
        meta = self._meta.get(_require_safe_id(asset_id))
        return copy.deepcopy(meta) if meta is not None else None

    def delete(self, asset_id: str) -> bool:
        """Remove the asset; ``True`` when a metadata row was removed.

        Returns:
            ``True`` if metadata existed and was removed.
        """
        safe_id = _require_safe_id(asset_id)
        self._content.pop(safe_id, None)
        return self._meta.pop(safe_id, None) is not None

    def items(self) -> Mapping[str, dict[str, JsonValue]]:
        """Return a deep-copied snapshot of all metadata.

        Returns:
            Mapping of asset id to metadata.
        """
        return copy.deepcopy(self._meta)

    def save_content(self, asset_id: str, content: bytes, *, content_type: str) -> int:
        """Store content bytes in memory; returns the byte count.

        Returns:
            Number of bytes stored.
        """
        del content_type
        self._content[_require_safe_id(asset_id)] = bytes(content)
        return len(content)

    def load_content(self, asset_id: str) -> bytes | None:
        """Return stored content bytes, or ``None``.

        Returns:
            The content bytes, or ``None`` when absent.
        """
        return self._content.get(_require_safe_id(asset_id))


class FilesystemDesignAssetStore:
    """Durable, path-traversal-guarded filesystem asset store.

    Layout under the configured root: ``<id>.json`` (metadata) and
    ``<id>.<ext>`` (content, extension derived from the content type).
    """

    def __init__(self, root: Path) -> None:
        """Create the store, ensuring the root directory exists."""
        self._root = root
        self._root.mkdir(parents=True, exist_ok=True)

    def _meta_path(self, asset_id: str) -> Path:
        """Return the metadata sidecar path for a validated id.

        Returns:
            The ``<id>.json`` path.
        """
        return self._root / f"{_require_safe_id(asset_id)}{_METADATA_SUFFIX}"

    def _read_meta(self, path: Path, asset_id: str) -> dict[str, JsonValue] | None:
        """Parse one metadata sidecar, degrading a corrupt file to ``None``.

        A truncated/corrupt sidecar (partial write from a prior crash,
        disk corruption) is logged and treated as absent rather than
        crashing the calling tool.

        Returns:
            The parsed metadata, or ``None`` when the file is missing or
            unreadable.
        """
        if not path.is_file():
            return None
        try:
            parsed: JsonValue = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            logger.warning(
                DESIGN_ASSET_PERSIST_FAILED,
                asset_id=asset_id,
                reason="metadata_read_failed",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            return None
        if not isinstance(parsed, dict):
            # A sidecar hand-edited to a non-object JSON value (list, scalar)
            # would crash every downstream ``.get()``; treat it as corrupt.
            logger.warning(
                DESIGN_ASSET_PERSIST_FAILED,
                asset_id=asset_id,
                reason="metadata_not_object",
                error_type=type(parsed).__name__,
                error="metadata sidecar is not a JSON object",
            )
            return None
        return parsed

    def _atomic_write(self, path: Path, write: Callable[[Path], object]) -> None:
        """Write ``path`` via a per-call-unique temp file then atomic rename.

        The temp name carries a ``uuid4`` so two concurrent writers to the
        same asset id cannot share (and clobber) one temp file before the
        rename.

        Raises:
            OSError: If the write or rename fails (surfaced to the caller,
                which logs ``DESIGN_ASSET_PERSIST_FAILED``).
        """
        tmp = path.with_suffix(f"{path.suffix}.{uuid4().hex}.tmp")
        try:
            write(tmp)
            tmp.replace(path)
        finally:
            tmp.unlink(missing_ok=True)

    def register(self, asset_id: str, metadata: dict[str, JsonValue]) -> None:
        """Write the metadata sidecar atomically."""
        path = self._meta_path(asset_id)
        payload = json.dumps(metadata, sort_keys=True)
        self._atomic_write(path, lambda tmp: tmp.write_text(payload, encoding="utf-8"))

    def get(self, asset_id: str) -> dict[str, JsonValue] | None:
        """Read the metadata sidecar, or ``None`` when absent/corrupt.

        Returns:
            The parsed metadata, or ``None``.
        """
        return self._read_meta(self._meta_path(asset_id), asset_id)

    def delete(self, asset_id: str) -> bool:
        """Delete the sidecar + any content file; ``True`` if a row existed.

        Globs every ``<id>.*`` sibling rather than resolving the content
        extension through the metadata, so a corrupt or missing sidecar
        cannot orphan the content bytes (or a stale temp file) on disk.

        Returns:
            ``True`` if the metadata sidecar existed and was removed.
        """
        safe_id = _require_safe_id(asset_id)
        existed = False
        for path in self._root.glob(f"{safe_id}.*"):
            if path.suffix == _METADATA_SUFFIX:
                existed = True
            path.unlink(missing_ok=True)
        return existed

    def items(self) -> Mapping[str, dict[str, JsonValue]]:
        """Scan the root for metadata sidecars and return a snapshot.

        Returns:
            Mapping of asset id to metadata for every readable sidecar.
        """
        result: dict[str, dict[str, JsonValue]] = {}
        for path in sorted(self._root.glob(f"*{_METADATA_SUFFIX}")):
            asset_id = path.name[: -len(_METADATA_SUFFIX)]
            meta = self._read_meta(path, asset_id)
            if meta is not None:
                result[asset_id] = meta
        return result

    def _content_path(self, asset_id: str, content_type: str) -> Path:
        """Return the content-file path for a validated id + content type.

        Returns:
            The ``<id>.<ext>`` path.
        """
        return self._root / f"{_require_safe_id(asset_id)}.{_ext_for(content_type)}"

    def save_content(self, asset_id: str, content: bytes, *, content_type: str) -> int:
        """Write content bytes atomically; returns the byte count.

        Returns:
            Number of bytes written.
        """
        path = self._content_path(asset_id, content_type)
        self._atomic_write(path, lambda tmp: tmp.write_bytes(content))
        logger.info(
            DESIGN_ASSET_PERSISTED,
            asset_id=asset_id,
            byte_size=len(content),
            content_type=content_type,
        )
        return len(content)

    def load_content(self, asset_id: str) -> bytes | None:
        """Return content bytes for an asset, or ``None`` when absent/unreadable.

        Returns:
            The content bytes, or ``None``.
        """
        meta = self.get(asset_id)
        if meta is None:
            return None
        path = self._content_path(asset_id, str(meta.get("content_type", "")))
        if not path.is_file():
            return None
        try:
            return path.read_bytes()
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            logger.warning(
                DESIGN_ASSET_PERSIST_FAILED,
                asset_id=asset_id,
                reason="content_read_failed",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            return None


def build_design_asset_store(asset_storage_path: str | None) -> DesignAssetStore:
    """Build the configured asset store.

    Args:
        asset_storage_path: Filesystem directory for durable storage, or
            ``None`` for the in-memory (non-durable) default.

    Returns:
        A :class:`FilesystemDesignAssetStore` when a path is given, else an
        :class:`InMemoryDesignAssetStore`.
    """
    if asset_storage_path is None:
        return InMemoryDesignAssetStore()
    return FilesystemDesignAssetStore(Path(asset_storage_path))
