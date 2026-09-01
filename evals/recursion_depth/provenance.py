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

from evals.harness.connection_identity import connection_sha256
from evals.harness.provenance import capture_git_state, manifest_digest
from evals.recursion_depth.journal import JOURNAL_NAME, PROGRESS_NAME
from evals.recursion_depth.manifest import ModelPair, RecursionDepthManifest
from evals.recursion_depth.models import CostBasis, Provenance
from evals.recursion_depth.tree import SpecBrief
from synthorg.config.schema import RootConfig
from synthorg.core.billing_enums import MEASURABLE_BILLING_MODELS
from synthorg.core.types import NotBlankStr


def recording_dirs(out_dir: Path) -> tuple[Path, ...]:
    """This recording's own directory, plus any sibling recording's.

    A concurrent sibling's output dirties the tree exactly as this recording's
    own does, and is no more "the code under test" than this one's. Measured on
    three cells run concurrently, which the plan explicitly endorses: the first
    read a clean tree and the other two read dirty on the same commit, purely
    because the first cell's directory had appeared. The clean cell could then
    no longer be resumed.

    A sibling qualifies only by PROOF that it is a recording, namely that it
    holds one of the journals this harness writes. Excluding the parent
    wholesale would be the obvious shortcut and is unsafe: ``--out-dir`` takes
    any path, so a run writing to ``results/x`` would have excluded ``results``
    while one writing to ``src/x`` would have excluded all of ``src``.

    Args:
        out_dir: Where this recording writes.

    Returns:
        Directories to exclude from the dirty check, this one always first.
    """
    found = [out_dir]
    parent = out_dir.parent
    if not parent.is_dir():
        return tuple(found)
    for sibling in sorted(parent.iterdir()):
        if sibling == out_dir or not sibling.is_dir():
            continue
        if (sibling / JOURNAL_NAME).exists() or (sibling / PROGRESS_NAME).exists():
            found.append(sibling)
    return tuple(found)


def _connection_identity(
    pair: ModelPair, *, company_config: RootConfig
) -> NotBlankStr | None:
    """Digest what *pair* actually dispatches through, when it can be resolved.

    ``None`` rather than a raise: ``run_preflight`` already refuses a manifest
    naming a provider ``company_config`` does not have, so a missing entry
    here means provenance is being captured out of that order, and the
    honest answer to an unresolvable connection is the same "unknown" a
    recording made before this field existed reads as.

    Returns:
        The digest, or ``None``.
    """
    config = company_config.providers.get(pair.provider)
    if config is None:
        return None
    return connection_sha256(config, model_id=pair.model_id)


def provider_is_priced(pair: ModelPair, *, company_config: RootConfig) -> bool:
    """Whether *pair*'s connection prices its calls.

    An unresolvable connection is treated as unpriced rather than raising:
    ``run_preflight`` already refuses a manifest naming a provider
    ``company_config`` does not have, so reaching this with no config means
    provenance is being captured out of that order, and the honest reading of
    an unknown connection is the cautious one, not the optimistic one.

    Returns:
        Whether the resolved connection's billing model is measurable.
    """
    config = company_config.providers.get(pair.provider)
    if config is None:
        return False
    return config.billing_model in MEASURABLE_BILLING_MODELS


def capture_provenance(
    *,
    repo_root: Path,
    manifest_path: Path,
    manifest: RecursionDepthManifest,
    spec: SpecBrief,
    company_config: RootConfig,
    out_dir: Path | None = None,
    sandbox_image: str | None = None,
) -> Provenance:
    """Stamp what this sweep is being measured against.

    Shells out to git, so callers on an event loop run it off-thread.

    Args:
        repo_root: Repository the recursion point and the gate were built from.
        manifest_path: The matrix file, hashed so a changed sweep is visible in
            the diff even when nothing else moved.
        manifest: The loaded matrix, for the pairs and the independence class.
        spec: The specification that was built.
        company_config: The config the pairs actually dispatch through, so the
            placeholder ``(provider, model_id)`` name on the manifest can be
            compared against the real connection behind it.
        out_dir: Where this sweep writes its report and its journal, excluded
            from the dirty check along with any CONCURRENT SIBLING recording's
            directory (see :func:`recording_dirs`). The default out-dir is
            tracked, so a finished stage would otherwise dirty the tree with its
            own artifacts and the next ``--resume`` would be refused on an
            identity mismatch.
        sandbox_image: The image the host RESOLVED for this run, which is what
            the units actually ran in and not necessarily what was asked for.
            Passed in rather than resolved here because the resolution needs a
            booted host, and this is deliberately called before one exists.

    Returns:
        The provenance stamp.
    """
    git = capture_git_state(
        repo_root, ignoring=recording_dirs(out_dir) if out_dir else ()
    )
    executor_connection = _connection_identity(
        manifest.executor, company_config=company_config
    )
    reviewer_connection = _connection_identity(
        manifest.reviewer, company_config=company_config
    )
    # A sweep-wide verdict, not a per-pair one: a merge whose assembling
    # session priced its calls and whose review did not has no honest total,
    # so the whole recording is unpriced the moment either connection is.
    cost_basis = (
        CostBasis.PRICED
        if provider_is_priced(manifest.executor, company_config=company_config)
        and provider_is_priced(manifest.reviewer, company_config=company_config)
        else CostBasis.UNPRICED
    )
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
        executor_connection_sha256=executor_connection,
        reviewer_connection_sha256=reviewer_connection,
        cost_basis=cost_basis,
        sandbox_image=NotBlankStr(sandbox_image) if sandbox_image else None,
    )


__all__ = ["capture_provenance", "provider_is_priced"]
