# module-kind: code
"""What a recursion-depth report was measured against.

The recursion point, the atomicity rule and the gate all live in this tree, so
the commit is not decoration: a curve produced before a change to any of them
is a curve about a different system. The manifest digest is here for the same
reason, and both model pairs plus the independence class because the whole
result turns on who judged.
"""

from datetime import UTC, datetime
from pathlib import Path

from evals.harness.provenance import capture_git_state, manifest_digest
from evals.recursion_depth.manifest import RecursionDepthManifest
from evals.recursion_depth.models import Provenance
from evals.recursion_depth.tree import SpecBrief
from synthorg.core.types import NotBlankStr


def capture_provenance(
    *,
    repo_root: Path,
    manifest_path: Path,
    manifest: RecursionDepthManifest,
    spec: SpecBrief,
    out_dir: Path | None = None,
) -> Provenance:
    """Stamp what this sweep is being measured against.

    Shells out to git, so callers on an event loop run it off-thread.

    Args:
        repo_root: Repository the recursion point and the gate were built from.
        manifest_path: The matrix file, hashed so a changed sweep is visible in
            the diff even when nothing else moved.
        manifest: The loaded matrix, for the pairs and the independence class.
        spec: The specification that was built.
        out_dir: Where this sweep writes its report and its journal, excluded
            from the dirty check. The default out-dir is tracked, so a finished
            stage would otherwise dirty the tree with its own artifacts and the
            next ``--resume`` would be refused on an identity mismatch.

    Returns:
        The provenance stamp.
    """
    git = capture_git_state(repo_root, ignoring=out_dir)
    return Provenance(
        generated_at=datetime.now(UTC),
        git_commit=NotBlankStr(git.commit),
        git_dirty=git.dirty,
        manifest_sha256=NotBlankStr(manifest_digest(manifest_path)),
        spec_id=NotBlankStr(spec.spec_id),
        requirement_count=len(spec.requirement_ids),
        executor=manifest.executor,
        reviewer=manifest.reviewer,
        independence=manifest.independence,
    )


__all__ = ["capture_provenance"]
