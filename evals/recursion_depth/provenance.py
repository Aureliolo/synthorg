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
from evals.recursion_depth.manifest import ModelPair, RecursionDepthManifest
from evals.recursion_depth.models import CostBasis, Provenance
from evals.recursion_depth.tree import SpecBrief
from synthorg.config.schema import RootConfig
from synthorg.core.billing_enums import MEASURABLE_BILLING_MODELS
from synthorg.core.types import NotBlankStr


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
            from the dirty check. The default out-dir is tracked, so a finished
            stage would otherwise dirty the tree with its own artifacts and the
            next ``--resume`` would be refused on an identity mismatch.

    Returns:
        The provenance stamp.
    """
    git = capture_git_state(repo_root, ignoring=out_dir)
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
    )


__all__ = ["capture_provenance", "provider_is_priced"]
