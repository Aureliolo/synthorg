# module-kind: code
"""Stamp a scoreboard with what it was measured against.

Loop-completion semantics are still moving, so a scoreboard recorded against an
older commit can describe behaviour the loops no longer have. Recording the
commit (and whether the tree was dirty) makes that visible in the artifact
rather than leaving a stale ranking looking authoritative.

How the commit and the digest are obtained lives in
:mod:`evals.harness.provenance`; what a scoreboard stamps is here, because the
fields are this artifact's own.
"""

from datetime import UTC, datetime
from pathlib import Path

from evals.harness.host import RecordedImages
from evals.harness.provenance import capture_git_state, manifest_digest
from evals.loop_ab.models import Provenance
from synthorg.core.types import NotBlankStr


def capture_provenance(
    *,
    repo_root: Path,
    manifest_path: Path,
    brief_suite_version: str,
    images: RecordedImages,
    out_dir: Path | None = None,
) -> Provenance:
    """Capture what this recording ran against.

    Args:
        repo_root: Repository the loops under test were measured from.
        manifest_path: The recording manifest driving the matrix.
        brief_suite_version: Stable digest of the brief suite measured.
        images: The container images the recording host resolved for its legs.
        out_dir: Where this recording writes its scoreboard and its journal,
            excluded from the dirty check. The committed scoreboard lives under
            a tracked directory, so a finished matrix would otherwise dirty the
            tree with its own artifacts and the next ``--resume`` would be
            refused on an identity mismatch.

    Returns:
        The assembled :class:`Provenance`.

    Raises:
        ProvenanceUnavailableError: The git metadata could not be read.
    """
    git = capture_git_state(repo_root, ignoring=out_dir)
    return Provenance(
        generated_at=datetime.now(UTC),
        git_commit=NotBlankStr(git.commit),
        git_dirty=git.dirty,
        manifest_sha256=NotBlankStr(manifest_digest(manifest_path)),
        brief_suite_version=NotBlankStr(brief_suite_version),
        sandbox_image=NotBlankStr(images.sandbox),
        sidecar_image=NotBlankStr(images.sidecar),
        openhands_image=NotBlankStr(images.openhands),
        sandbox_image_id=images.sandbox_id,
        sidecar_image_id=images.sidecar_id,
        openhands_image_id=images.openhands_id,
    )


__all__ = ["capture_provenance"]
