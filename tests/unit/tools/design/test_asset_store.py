"""Tests for the pluggable design-asset store backends."""

from pathlib import Path

import pytest

from synthorg.tools.design.asset_store import (
    FilesystemDesignAssetStore,
    InMemoryDesignAssetStore,
    build_design_asset_store,
)

pytestmark = pytest.mark.unit


def test_build_selects_backend_by_path(tmp_path: Path) -> None:
    assert isinstance(build_design_asset_store(None), InMemoryDesignAssetStore)
    assert isinstance(
        build_design_asset_store(str(tmp_path)), FilesystemDesignAssetStore
    )


@pytest.fixture(params=["memory", "filesystem"])
def store(
    request: pytest.FixtureRequest, tmp_path: Path
) -> InMemoryDesignAssetStore | FilesystemDesignAssetStore:
    if request.param == "memory":
        return InMemoryDesignAssetStore()
    return FilesystemDesignAssetStore(tmp_path / "assets")


def test_round_trips_metadata_and_content(
    store: InMemoryDesignAssetStore | FilesystemDesignAssetStore,
) -> None:
    store.register("img-1", {"type": "image", "content_type": "image/png"})
    written = store.save_content("img-1", b"\x89PNG-data", content_type="image/png")
    assert written == len(b"\x89PNG-data")
    meta = store.get("img-1")
    assert meta is not None
    assert meta["type"] == "image"
    assert store.load_content("img-1") == b"\x89PNG-data"


def test_items_and_delete(
    store: InMemoryDesignAssetStore | FilesystemDesignAssetStore,
) -> None:
    store.register("img-1", {"type": "image", "content_type": "image/png"})
    store.save_content("img-1", b"data", content_type="image/png")
    assert set(store.items()) == {"img-1"}
    assert store.delete("img-1") is True
    assert store.delete("img-1") is False
    assert store.get("img-1") is None
    assert store.load_content("img-1") is None


def test_missing_asset_returns_none(
    store: InMemoryDesignAssetStore | FilesystemDesignAssetStore,
) -> None:
    assert store.get("absent") is None
    assert store.load_content("absent") is None


@pytest.mark.parametrize("bad_id", ["../escape", "a/b", "with space", ""])
def test_rejects_unsafe_asset_ids(
    store: InMemoryDesignAssetStore | FilesystemDesignAssetStore,
    bad_id: str,
) -> None:
    # Every entry point validates identically across both backends (Liskov):
    # a malformed id raises rather than silently returning ``None`` on one
    # backend and traversing on the other.
    with pytest.raises(ValueError, match="asset_id"):
        store.register(bad_id, {"type": "image"})
    with pytest.raises(ValueError, match="asset_id"):
        store.get(bad_id)
    with pytest.raises(ValueError, match="asset_id"):
        store.delete(bad_id)
    with pytest.raises(ValueError, match="asset_id"):
        store.load_content(bad_id)


def test_corrupt_sidecar_degrades_gracefully(tmp_path: Path) -> None:
    root = tmp_path / "assets"
    store = FilesystemDesignAssetStore(root)
    store.register("good", {"type": "image", "content_type": "image/png"})
    # A truncated/corrupt sidecar (e.g. a crash mid-write) must not crash the
    # calling tool: it is skipped in items() and read as absent in get().
    (root / "broken.json").write_text("{not valid json", encoding="utf-8")
    assert set(store.items()) == {"good"}
    assert store.get("broken") is None


def test_filesystem_persists_across_instances(tmp_path: Path) -> None:
    root = tmp_path / "assets"
    first = FilesystemDesignAssetStore(root)
    first.register("img-1", {"type": "image", "content_type": "image/png"})
    first.save_content("img-1", b"pixels", content_type="image/png")
    # A fresh store over the same directory sees the durable asset.
    second = FilesystemDesignAssetStore(root)
    assert second.get("img-1") is not None
    assert second.load_content("img-1") == b"pixels"
    assert (root / "img-1.png").is_file()
    assert (root / "img-1.json").is_file()
