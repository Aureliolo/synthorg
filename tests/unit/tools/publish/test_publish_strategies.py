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
from synthorg.integrations.errors import RegistryApiClientError
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


def _digest(data: bytes, algo: str = "sha256") -> str:
    return f"{algo}:{hashlib.new(algo, data).hexdigest()}"


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
        raw = self._manifests.get(str(reference))
        if raw is None:
            # Mirror the real client: an absent reference surfaces as a
            # deterministic 4xx, not a bare KeyError.
            msg = "registry rejected the request to read a manifest (status 404)"
            raise RegistryApiClientError(msg)
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
        # A workspace-shaped request (path, no digest) handed to the promote
        # strategy has no digest to promote.
        client = _FakeRegistryClient()
        with pytest.raises(PublishToolArgumentError):
            await DigestPromoteStrategy().publish(
                client, _request(source_image_path="image")
            )

    async def test_absent_source_digest_is_a_source_error(self) -> None:
        # A 4xx on the source read maps to a source problem the agent can fix,
        # not an opaque upstream failure.
        client = _FakeRegistryClient()
        with pytest.raises(PublishSourceError):
            await DigestPromoteStrategy().publish(
                client,
                _request(source_digest=NotBlankStr(_digest(_MANIFEST))),
            )

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


def _image_manifest(config: bytes, layer: bytes, algo: str) -> bytes:
    """Serialise one OCI image manifest referencing *config* + *layer*."""
    return json.dumps(
        {
            "schemaVersion": 2,
            "mediaType": OCI_MANIFEST_MEDIA_TYPE,
            "config": {
                "mediaType": "application/vnd.oci.image.config.v1+json",
                "digest": _digest(config, algo),
                "size": len(config),
            },
            "layers": [
                {
                    "mediaType": "application/vnd.oci.image.layer.v1.tar",
                    "digest": _digest(layer, algo),
                    "size": len(layer),
                }
            ],
        },
        separators=(",", ":"),
    ).encode()


def _write_blobs(root: Path, algo: str, blobs: list[bytes]) -> None:
    """Write each blob under ``blobs/<algo>/<hex>`` in the layout."""
    blob_dir = root / "blobs" / algo
    blob_dir.mkdir(parents=True, exist_ok=True)
    for blob in blobs:
        (blob_dir / _digest(blob, algo).split(":", 1)[1]).write_bytes(blob)


def _write_single_image_layout(root: Path, algo: str = "sha256") -> None:
    """Write a minimal single-image OCI layout under *root*."""
    config = b'{"architecture":"amd64","os":"linux"}'
    layer = b"layer-tar-bytes"
    manifest = _image_manifest(config, layer, algo)
    index = json.dumps(
        {
            "schemaVersion": 2,
            "mediaType": OCI_INDEX_MEDIA_TYPE,
            "manifests": [
                {
                    "mediaType": OCI_MANIFEST_MEDIA_TYPE,
                    "digest": _digest(manifest, algo),
                    "size": len(manifest),
                }
            ],
        },
        separators=(",", ":"),
    ).encode()
    _write_blobs(root, algo, [config, layer, manifest])
    (root / "oci-layout").write_bytes(b'{"imageLayoutVersion":"1.0.0"}')
    (root / "index.json").write_bytes(index)


def _write_multi_arch_layout(root: Path, *, nested: bool = False) -> None:
    """Write a multi-arch OCI image-index layout with two child manifests.

    When *nested* is set the second child is itself an image index, the
    unsupported shape the strategy must reject.
    """
    amd_config = b'{"architecture":"amd64","os":"linux"}'
    amd_layer = b"amd64-layer"
    arm_config = b'{"architecture":"arm64","os":"linux"}'
    arm_layer = b"arm64-layer"
    amd_manifest = _image_manifest(amd_config, amd_layer, "sha256")
    arm_manifest = _image_manifest(arm_config, arm_layer, "sha256")
    second_media = OCI_INDEX_MEDIA_TYPE if nested else OCI_MANIFEST_MEDIA_TYPE
    # Pretty-printed on purpose: a compact re-serialisation of the parsed dict
    # would differ byte-for-byte, so a test asserting the tagged bytes equal the
    # on-disk index proves the original bytes (not a re-dump) are published.
    index = json.dumps(
        {
            "schemaVersion": 2,
            "mediaType": OCI_INDEX_MEDIA_TYPE,
            "manifests": [
                {
                    "mediaType": OCI_MANIFEST_MEDIA_TYPE,
                    "digest": _digest(amd_manifest),
                    "size": len(amd_manifest),
                },
                {
                    "mediaType": second_media,
                    "digest": _digest(arm_manifest),
                    "size": len(arm_manifest),
                },
            ],
        },
        indent=2,
    ).encode()
    _write_blobs(
        root,
        "sha256",
        [amd_config, amd_layer, arm_config, arm_layer, amd_manifest, arm_manifest],
    )
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

    async def test_sha512_layout_is_verified_and_pushed(self, tmp_path: Path) -> None:
        # A sha512-addressed layout must verify under sha512, not sha256.
        layout = tmp_path / "image"
        layout.mkdir()
        _write_single_image_layout(layout, algo="sha512")
        client = _FakeRegistryClient()
        outcome = await WorkspacePushStrategy().publish(
            client,
            _request(source_image_path="image", workspace_root=tmp_path),
        )
        assert outcome.blobs_uploaded == 2
        assert "latest" in client.put_tags

    async def test_multi_arch_index_tags_children_then_index(
        self, tmp_path: Path
    ) -> None:
        layout = tmp_path / "image"
        layout.mkdir()
        _write_multi_arch_layout(layout)
        original_index = (layout / "index.json").read_bytes()
        client = _FakeRegistryClient()
        outcome = await WorkspacePushStrategy().publish(
            client,
            _request(source_image_path="image", workspace_root=tmp_path),
        )
        # Two children, each config + layer -> four blobs; each child tagged by
        # digest, plus the destination tag for the index.
        assert outcome.blobs_uploaded == 4
        # The exact on-disk index bytes are tagged, so the index's content
        # digest is preserved rather than diverging via a re-serialisation.
        assert client.put_tags["latest"] == original_index
        child_tags = [tag for tag in client.put_tags if tag.startswith("sha256:")]
        assert len(child_tags) == 2

    async def test_nested_index_is_refused(self, tmp_path: Path) -> None:
        layout = tmp_path / "image"
        layout.mkdir()
        _write_multi_arch_layout(layout, nested=True)
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
