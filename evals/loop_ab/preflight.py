# module-kind: code
"""Checks that run before a recording matrix spends anything.

Both conditions here are discoverable in milliseconds and, left undiscovered,
cost either money or a run. A manifest tier naming a provider the company config
does not carry fails once per cell, so the operator gets a scoreboard of
unavailable rows instead of one message; forgetting ``--company-config``
entirely (the default baseline carries no ``providers`` block at all) produces
exactly that, and it is the easiest mistake to make. An unreachable Docker
daemon is worse than useless to discover late: every loop drives a sandbox, so
the native legs can dispatch real, billable turns before the first shell call
reveals the daemon was never there.

Both are properties of the machine and the configuration, never of a loop, so
neither belongs in the per-cell handler that records a loop as unavailable.
"""

import aiodocker

from evals.errors import LoopAbDockerUnavailableError, LoopAbProviderMissingError
from evals.loop_ab.manifest import LoopAbManifest
from synthorg.config.schema import RootConfig
from synthorg.observability import get_logger
from synthorg.observability.events.evals import (
    EVALS_LOOP_AB_PREFLIGHT_PASSED,
    EVALS_LOOP_AB_PROVIDER_MISSING,
)

logger = get_logger(__name__)


async def run_preflight(
    *, manifest: LoopAbManifest, company_config: RootConfig
) -> None:
    """Refuse a matrix the machine or the configuration cannot deliver.

    Args:
        manifest: The loaded recording manifest.
        company_config: The company config the run will boot against.

    Raises:
        LoopAbProviderMissingError: A manifest tier names a provider the
            company config does not carry.
        LoopAbDockerUnavailableError: The Docker daemon is unreachable.
    """
    _check_tier_providers(manifest=manifest, company_config=company_config)
    await _check_docker()
    logger.info(
        EVALS_LOOP_AB_PREFLIGHT_PASSED,
        tiers=len(manifest.tiers),
        providers=len(company_config.providers),
    )


def _check_tier_providers(
    *, manifest: LoopAbManifest, company_config: RootConfig
) -> None:
    """Confirm every tier's bound provider exists in the company config.

    Args:
        manifest: The loaded recording manifest.
        company_config: The company config the run will boot against.

    Raises:
        LoopAbProviderMissingError: One or more tiers name an absent provider.
    """
    missing = sorted(
        {
            tier.provider
            for tier in manifest.tiers
            if tier.provider not in company_config.providers
        }
    )
    if not missing:
        return
    logger.error(
        EVALS_LOOP_AB_PROVIDER_MISSING,
        providers=tuple(missing),
        configured=tuple(sorted(company_config.providers)),
    )
    msg = (
        f"manifest tiers name providers absent from the company config: "
        f"{', '.join(missing)}. Pass --company-config pointing at a config whose "
        f"providers block covers every tier."
    )
    raise LoopAbProviderMissingError(msg)


async def _check_docker() -> None:
    """Confirm the Docker daemon answers before any cell runs.

    Raises:
        LoopAbDockerUnavailableError: The daemon did not answer.
    """
    try:
        async with aiodocker.Docker() as client:
            await client.version()
    except MemoryError, RecursionError:
        # An interpreter-level fault is not a statement about the daemon, and
        # reporting it as one sends the operator to check Docker.
        raise
    except Exception as exc:
        msg = (
            "the Docker daemon is unreachable, and every loop in the matrix "
            "runs its shell tool in a container"
        )
        raise LoopAbDockerUnavailableError(msg) from exc


__all__ = ["run_preflight"]
