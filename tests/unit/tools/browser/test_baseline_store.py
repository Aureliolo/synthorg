"""Unit tests for :class:`WorkspaceBaselineStore`."""

import hashlib
import json
from pathlib import Path

import pytest

from synthorg.tools.browser._baseline import WorkspaceBaselineStore
from synthorg.tools.browser._constants import BASELINE_META_FILENAME
from synthorg.tools.browser.errors import (
    BrowserBaselineNotFoundError,
    BrowserDomainError,
)

pytestmark = pytest.mark.unit


@pytest.fixture
def store(tmp_path: Path) -> WorkspaceBaselineStore:
    return WorkspaceBaselineStore(workspace=tmp_path)


class TestPathResolution:
    def test_baseline_path_structure(
        self,
        store: WorkspaceBaselineStore,
        tmp_path: Path,
    ) -> None:
        path = store.baseline_path(spec_name="login", screenshot_name="hero")
        assert path.is_relative_to(tmp_path)
        assert path.name == "hero.png"
        assert path.parent.name == "login"

    def test_relative_path_is_workspace_scoped(
        self,
        store: WorkspaceBaselineStore,
    ) -> None:
        absolute = store.baseline_path(
            spec_name="login",
            screenshot_name="hero",
        )
        absolute.parent.mkdir(parents=True, exist_ok=True)
        absolute.write_bytes(b"x")
        relative = store.relative(absolute)
        assert relative.startswith(".synthorg/screenshots/login/")

    @pytest.mark.parametrize(
        ("field_name", "name"),
        [
            ("spec_name", ".."),
            ("spec_name", "."),
            ("spec_name", "../escape"),
            ("spec_name", "a/b"),
            ("spec_name", "a\\b"),
            ("spec_name", ""),
            ("screenshot_name", ".."),
            ("screenshot_name", "."),
            ("screenshot_name", "../escape"),
            ("screenshot_name", "a/b"),
            ("screenshot_name", "a\\b"),
            ("screenshot_name", ""),
        ],
    )
    def test_rejects_path_traversal_names(
        self,
        store: WorkspaceBaselineStore,
        field_name: str,
        name: str,
    ) -> None:
        kwargs = {"spec_name": "login", "screenshot_name": "hero"}
        kwargs[field_name] = name
        with pytest.raises(BrowserDomainError):
            store.baseline_path(**kwargs)


class TestSidecar:
    def test_sidecar_records_metadata(
        self,
        store: WorkspaceBaselineStore,
    ) -> None:
        png = b"\x89PNG\r\n\x1a\nfake"
        meta_path = store.write_sidecar(
            spec_name="login",
            screenshot_name="hero",
            png_bytes=png,
        )
        assert meta_path.name == f"hero{BASELINE_META_FILENAME}"
        payload = json.loads(meta_path.read_text(encoding="utf-8"))
        assert payload["sha256"] == hashlib.sha256(png).hexdigest()
        assert payload["spec_name"] == "login"
        assert "axe_version" in payload


class TestAdoption:
    def test_adopt_current_promotes_to_baseline(
        self,
        store: WorkspaceBaselineStore,
        tmp_path: Path,
    ) -> None:
        current = store.current_path(
            spec_name="login",
            screenshot_name="hero",
        )
        current.parent.mkdir(parents=True, exist_ok=True)
        current.write_bytes(b"png-bytes")

        baseline = store.adopt_current_as_baseline(
            spec_name="login",
            screenshot_name="hero",
        )
        assert baseline.exists()
        assert baseline.read_bytes() == b"png-bytes"
        assert not current.exists()

    def test_adopt_without_current_raises(
        self,
        store: WorkspaceBaselineStore,
    ) -> None:
        with pytest.raises(BrowserBaselineNotFoundError):
            store.adopt_current_as_baseline(
                spec_name="login",
                screenshot_name="hero",
            )


def test_store_rejects_relative_workspace(tmp_path: Path) -> None:
    relative = Path("relative/workspace")
    with pytest.raises(BrowserDomainError):
        WorkspaceBaselineStore(workspace=relative)
