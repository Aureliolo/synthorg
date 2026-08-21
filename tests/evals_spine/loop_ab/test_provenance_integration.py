# module-kind: tests
"""Provenance capture against a real git repository.

These drive the real ``git`` binary via subprocess, so they sit in the
integration capability (matching the tiering ``test_briefs.py`` states for
subprocess-driving tests) rather than slowing the unit suite.
"""

from pathlib import Path

import pytest

from evals.errors import ProvenanceUnavailableError
from evals.harness.host import RecordedImages
from evals.harness.provenance import manifest_digest
from evals.loop_ab.provenance import capture_provenance

pytestmark = pytest.mark.integration

_IMAGES = RecordedImages(
    sandbox="example.invalid/sandbox:under-test",
    sidecar="example.invalid/sidecar:under-test",
    openhands="example.invalid/openhands:under-test",
    sandbox_id="sha256:" + "1" * 64,
    sidecar_id="sha256:" + "2" * 64,
    openhands_id="sha256:" + "3" * 64,
)


def test_provenance_reads_the_real_repository(tmp_path: Path) -> None:
    """Provenance is captured from git itself, never hand-supplied."""
    manifest = tmp_path / "manifest.yaml"
    manifest.write_text("loops: []\n", encoding="utf-8")

    provenance = capture_provenance(
        repo_root=Path(__file__).resolve().parents[3],
        manifest_path=manifest,
        brief_suite_version="sha256:cafe",
        images=_IMAGES,
    )

    assert len(provenance.git_commit) == 40
    assert provenance.manifest_sha256 == manifest_digest(manifest)


def test_provenance_records_the_images_the_legs_ran_on(tmp_path: Path) -> None:
    """The commit does not describe a container, so the images travel with it."""
    manifest = tmp_path / "manifest.yaml"
    manifest.write_text("loops: []\n", encoding="utf-8")

    provenance = capture_provenance(
        repo_root=Path(__file__).resolve().parents[3],
        manifest_path=manifest,
        brief_suite_version="sha256:cafe",
        images=_IMAGES,
    )

    assert provenance.sandbox_image == _IMAGES.sandbox
    assert provenance.sidecar_image == _IMAGES.sidecar
    assert provenance.openhands_image == _IMAGES.openhands


def test_provenance_outside_a_repository_fails_loud(tmp_path: Path) -> None:
    """An unnamed commit means an unreproducible scoreboard, so refuse it."""
    manifest = tmp_path / "manifest.yaml"
    manifest.write_text("loops: []\n", encoding="utf-8")

    with pytest.raises(ProvenanceUnavailableError):
        capture_provenance(
            repo_root=tmp_path,
            manifest_path=manifest,
            brief_suite_version="sha256:cafe",
            images=_IMAGES,
        )
