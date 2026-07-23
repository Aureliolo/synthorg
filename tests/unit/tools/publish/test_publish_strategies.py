"""Unit tests for the publish strategies and their factory.

Covers digest promotion (read-by-digest then PUT-by-tag), the full workspace
OCI-layout upload (blob existence check -> upload missing -> tag), the image
size cap, path-traversal and integrity rejection, and the ``auto`` method
resolution.
"""

import hashlib
import json
from pathlib import Path

import pytest

from synthorg.core.types import NotBlankStr
from synthorg.integrations.connections.registry_target import PublishMethod
from synthorg.integrations.registry_api.protocol import (
    OCI_INDEX_MEDIA_TYPE,
    OCI_MANIFEST_MEDIA_TYPE,
    ManifestRef,
    TagList,
)
from synthorg.tools.publish.errors import (
    PublishSourceError,
    PublishToolArgumentError,
)
from synthorg.tools.publish.strategies import (
    PublishRequest,
    build_publish_strategy,
    resolve_publish_method,
)
from synthorg.tools.publish.strategies.digest_promote import DigestPromoteStrategy
from synthorg.tools.publish.strategies.workspace_push import WorkspacePushStrategy

pytestmark = pytest.mark.unit

_REPO = NotBlankStr("acme/app")


def _digest(data: bytes) -> str:
    return f"sha256:{hashlib.sha256(data).hexdigest()}"


class _FakeRegistryClient:
    """In-memory registry client recording what a strategy uploaded / tagged."""

    def __init__(self, *, manifests: dict[str, bytes] | None = None) -> None:
        self._manifests = manifests or {}
        self.uploaded: list[str] = []
        self.put_tags: dict[str, bytes] = {}
        self.present: set[str] = set()

    @property
    def repository(self) -> NotBlankStr:
        return _REPO

    async def list_tags(self, *, limit: int) -> TagList:
        return TagList(repository=_REPO, tags=tuple(self.put_tags))

    async def get_manifest(self, *, reference: NotBlankStr) -> ManifestRef:
        raw = self._manifests[str(reference)]
        return ManifestRef(
            digest=NotBlankStr(_digest(raw)),
            media_type=OCI_MANIFEST_MEDIA_TYPE,
            size=len(raw),
            raw=raw,
        )

    async def put_manifest(
        self, *, tag: NotBlankStr, raw: bytes, media_type: str
    ) -> ManifestRef:
        self.put_tags[str(tag)] = raw
        return ManifestRef(digest=NotBlankStr(_digest(raw)), media_type=media_type)

    async def blob_exists(self, *, digest: NotBlankStr) -> bool:
        return str(digest) in self.present

    async def upload_blob(self, *, digest: NotBlankStr, data: bytes) -> None:
        self.uploaded.append(str(digest))

    async def aclose(self) -> None:
        return None


def _request(**overrides: object) -> PublishRequest:
    defaults: dict[str, object] = {
        "dest_tag": NotBlankStr("latest"),
        "source_digest": None,
        "source_image_path": "",
        "max_manifest_bytes": 1_000_000,
        "max_image_bytes": 1_000_000_000,
        "workspace_root": Path.cwd(),
    }
    defaults.update(overrides)
    return PublishRequest(**defaults)  # type: ignore[arg-type]


class TestDigestPromote:
    async def test_promotes_source_digest_to_tag(self) -> None:
        manifest = _MANIFEST
        client = _FakeRegistryClient(manifests={_digest(manifest): manifest})
        outcome = await DigestPromoteStrategy().publish(
            client,
            _request(source_digest=NotBlankStr(_digest(manifest))),
        )
        assert outcome.method == "digest_promote"
        assert client.put_tags["latest"] == manifest

    async def test_missing_source_digest_raises(self) -> None:
        client = _FakeRegistryClient()
        with pytest.raises(PublishToolArgumentError):
            await DigestPromoteStrategy().publish(client, _request())

    async def test_manifest_over_cap_raises(self) -> None:
        manifest = _MANIFEST
        client = _FakeRegistryClient(manifests={_digest(manifest): manifest})
        with pytest.raises(PublishSourceError):
            await DigestPromoteStrategy().publish(
                client,
                _request(
                    source_digest=NotBlankStr(_digest(manifest)), max_manifest_bytes=1
                ),
            )


_MANIFEST = b'{"schemaVersion":2,"config":{},"layers":[]}'


def _write_single_image_layout(root: Path) -> None:
    """Write a minimal single-image OCI layout under *root*."""
    config = b'{"architecture":"amd64","os":"linux"}'
    layer = b"layer-tar-bytes"
    manifest = json.dumps(
        {
            "schemaVersion": 2,
            "mediaType": OCI_MANIFEST_MEDIA_TYPE,
            "config": {
                "mediaType": "application/vnd.oci.image.config.v1+json",
                "digest": _digest(config),
                "size": len(config),
            },
            "layers": [
                {
                    "mediaType": "application/vnd.oci.image.layer.v1.tar",
                    "digest": _digest(layer),
                    "size": len(layer),
                }
            ],
        },
        separators=(",", ":"),
    ).encode()
    index = json.dumps(
        {
            "schemaVersion": 2,
            "mediaType": OCI_INDEX_MEDIA_TYPE,
            "manifests": [
                {
                    "mediaType": OCI_MANIFEST_MEDIA_TYPE,
                    "digest": _digest(manifest),
                    "size": len(manifest),
                }
            ],
        },
        separators=(",", ":"),
    ).encode()
    blobs = root / "blobs" / "sha256"
    blobs.mkdir(parents=True)
    for blob in (config, layer, manifest):
        (blobs / _digest(blob).split(":", 1)[1]).write_bytes(blob)
    (root / "oci-layout").write_bytes(b'{"imageLayoutVersion":"1.0.0"}')
    (root / "index.json").write_bytes(index)


class TestWorkspacePush:
    async def test_uploads_blobs_then_tags(self, tmp_path: Path) -> None:
        layout = tmp_path / "image"
        layout.mkdir()
        _write_single_image_layout(layout)
        client = _FakeRegistryClient()
        outcome = await WorkspacePushStrategy().publish(
            client,
            _request(source_image_path="image", workspace_root=tmp_path),
        )
        assert outcome.method == "workspace_push"
        assert outcome.blobs_uploaded == 2  # config + one layer
        assert "latest" in client.put_tags

    async def test_present_blobs_are_not_re_uploaded(self, tmp_path: Path) -> None:
        layout = tmp_path / "image"
        layout.mkdir()
        _write_single_image_layout(layout)
        client = _FakeRegistryClient()
        # Mark every blob present so none is uploaded.
        for blob in (layout / "blobs" / "sha256").iterdir():
            client.present.add(f"sha256:{blob.name}")
        outcome = await WorkspacePushStrategy().publish(
            client,
            _request(source_image_path="image", workspace_root=tmp_path),
        )
        assert outcome.blobs_uploaded == 0

    async def test_path_traversal_is_refused(self, tmp_path: Path) -> None:
        client = _FakeRegistryClient()
        with pytest.raises(PublishSourceError):
            await WorkspacePushStrategy().publish(
                client,
                _request(source_image_path="../escape", workspace_root=tmp_path),
            )

    async def test_oversize_image_is_refused(self, tmp_path: Path) -> None:
        layout = tmp_path / "image"
        layout.mkdir()
        _write_single_image_layout(layout)
        client = _FakeRegistryClient()
        with pytest.raises(PublishSourceError):
            await WorkspacePushStrategy().publish(
                client,
                _request(
                    source_image_path="image",
                    workspace_root=tmp_path,
                    max_image_bytes=1,
                ),
            )

    async def test_corrupt_blob_is_refused(self, tmp_path: Path) -> None:
        layout = tmp_path / "image"
        layout.mkdir()
        _write_single_image_layout(layout)
        # Corrupt one blob so its content no longer matches its digest.
        blob = next((layout / "blobs" / "sha256").iterdir())
        blob.write_bytes(b"tampered")
        client = _FakeRegistryClient()
        with pytest.raises(PublishSourceError):
            await WorkspacePushStrategy().publish(
                client,
                _request(source_image_path="image", workspace_root=tmp_path),
            )


class TestMethodResolution:
    def test_explicit_method_is_honoured(self) -> None:
        assert (
            resolve_publish_method(
                PublishMethod.DIGEST_PROMOTE, has_digest=True, has_image_path=False
            )
            is PublishMethod.DIGEST_PROMOTE
        )

    def test_auto_with_image_path_resolves_workspace_push(self) -> None:
        assert (
            resolve_publish_method(
                PublishMethod.AUTO, has_digest=False, has_image_path=True
            )
            is PublishMethod.WORKSPACE_PUSH
        )

    def test_auto_with_digest_resolves_promote(self) -> None:
        assert (
            resolve_publish_method(
                PublishMethod.AUTO, has_digest=True, has_image_path=False
            )
            is PublishMethod.DIGEST_PROMOTE
        )

    def test_auto_with_both_is_ambiguous(self) -> None:
        with pytest.raises(PublishToolArgumentError):
            resolve_publish_method(
                PublishMethod.AUTO, has_digest=True, has_image_path=True
            )

    def test_auto_with_neither_is_refused(self) -> None:
        with pytest.raises(PublishToolArgumentError):
            resolve_publish_method(
                PublishMethod.AUTO, has_digest=False, has_image_path=False
            )

    def test_build_strategy_returns_concrete(self) -> None:
        assert isinstance(
            build_publish_strategy(PublishMethod.DIGEST_PROMOTE), DigestPromoteStrategy
        )
        assert isinstance(
            build_publish_strategy(PublishMethod.WORKSPACE_PUSH), WorkspacePushStrategy
        )
