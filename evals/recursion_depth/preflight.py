# module-kind: code
"""Checks that run before the sweep boots anything or spends anything.

Every condition here is discoverable in seconds and, left undiscovered, is
found instead by a unit failing mid-decomposition. That matters beyond the
wasted minute: the failure surfaces as ``decomposition.failed`` and the cell is
recorded unavailable with a ``DecompositionError`` reason, which names the
wrong subsystem entirely. An operator reading that goes looking at the planner.

Measured on a run with a deliberately invalid key: 56 seconds to fail, because
the credential error was retried by the driver, returned across the recorder's
own gateway hop as a 502, and retried again on the far side. A bad key fails
identically every time, so all of that is latency; the same nesting applies to
a key that expires or a quota that runs out mid-sweep.

The probe is also the warm-up. A cold model load otherwise lands entirely on
whichever cell happens to be recorded first, and depth 1 is the cheapest cell
in the matrix, so the curve's flattest point would carry the load time.
"""

import asyncio
from typing import Final

import aiodocker

from evals.errors import (
    HarnessDockerUnavailableError,
    HarnessProviderMissingError,
)
from evals.recursion_depth.manifest import ModelPair, RecursionDepthManifest
from synthorg.config.schema import RootConfig
from synthorg.observability import get_logger
from synthorg.observability.events.evals import (
    EVALS_HARNESS_PROVIDER_MISSING,
    EVALS_RECURSION_PREFLIGHT_PASSED,
)
from synthorg.providers.enums import MessageRole
from synthorg.providers.errors import ProviderError
from synthorg.providers.models import ChatMessage, CompletionConfig
from synthorg.providers.registry import ProviderRegistry

logger = get_logger(__name__)

#: Cheapest thing that still proves the credential, the endpoint and the model
#: id all work together. Anything shorter stops proving the model id.
_PROBE_PROMPT: Final[str] = "Reply with the single word: ok"

#: One token is enough to prove the round trip and keeps the probe free on a
#: per-token connection.
_PROBE_MAX_TOKENS: Final[int] = 1

#: Seconds a pair may take to answer a one-token request before the probe gives
#: up. Generous, because a cold model load is the expected slow case here and
#: this is not a latency judgement, only a liveness one.
_PROBE_TIMEOUT_SECONDS: Final[float] = 180.0


async def run_preflight(
    *, manifest: RecursionDepthManifest, company_config: RootConfig
) -> None:
    """Settle everything knowable before the host boots.

    Args:
        manifest: The recording matrix.
        company_config: The config the run will boot against.

    Raises:
        HarnessDockerUnavailableError: The daemon did not answer.
        HarnessProviderMissingError: A pair names an absent provider, or one
            could not complete a trivial request.
    """
    _check_pair_providers(manifest=manifest, company_config=company_config)
    await _check_docker()
    pairs = (("executor", manifest.executor), ("reviewer", manifest.reviewer))
    for role, pair in pairs:
        await _probe_pair(role=role, pair=pair, company_config=company_config)
    logger.info(
        EVALS_RECURSION_PREFLIGHT_PASSED,
        executor=manifest.executor.label,
        reviewer=manifest.reviewer.label,
    )


def _check_pair_providers(
    *, manifest: RecursionDepthManifest, company_config: RootConfig
) -> None:
    """Confirm both pairs name a provider the company config carries.

    Separate from the probe because it needs no network and catches the easiest
    mistake there is: omitting ``--company-config`` entirely, whose default
    carries no ``providers`` block at all.

    Args:
        manifest: The recording matrix.
        company_config: The config the run will boot against.

    Raises:
        HarnessProviderMissingError: A pair names an absent provider.
    """
    missing = sorted(
        {
            pair.provider
            for pair in (manifest.executor, manifest.reviewer)
            if pair.provider not in company_config.providers
        }
    )
    if not missing:
        return
    logger.error(
        EVALS_HARNESS_PROVIDER_MISSING,
        providers=tuple(missing),
        configured=tuple(sorted(company_config.providers)),
    )
    msg = (
        f"the manifest names providers absent from the company config: "
        f"{', '.join(missing)}. Pass --company-config pointing at a config "
        f"whose providers block carries both the executor and the reviewer."
    )
    raise HarnessProviderMissingError(msg)


async def _probe_pair(
    *, role: str, pair: ModelPair, company_config: RootConfig
) -> None:
    """Prove one pair can actually answer before the sweep depends on it.

    Dispatches to the pair's provider directly rather than through the
    recorder's gateway: the gateway is not up yet, and what is in question is
    the upstream credential rather than our own hop.

    Args:
        role: Which half of the manifest this pair is, for the message.
        pair: The binding to probe.
        company_config: The config carrying its provider.

    Raises:
        HarnessProviderMissingError: The pair could not answer.
    """
    registry = ProviderRegistry.from_config(
        {pair.provider: company_config.providers[pair.provider]}
    )
    provider = registry.get(pair.provider)
    try:
        async with asyncio.timeout(_PROBE_TIMEOUT_SECONDS):
            await provider.complete(
                [ChatMessage(role=MessageRole.USER, content=_PROBE_PROMPT)],
                pair.model_id,
                config=CompletionConfig(max_tokens=_PROBE_MAX_TOKENS),
            )
    # The two shapes a misconfigured pair actually produces: the provider
    # refusing (bad credential, unknown model, unreachable endpoint) and it
    # never answering. Both are properties of the configuration rather than of
    # anything the sweep does, so both are reported here rather than once per
    # cell as a failure of whichever subsystem happened to make the call.
    except (ProviderError, TimeoutError) as exc:
        msg = (
            f"the {role} pair {pair.label} could not complete a one-token "
            f"request, so no cell recorded against it would measure anything. "
            f"Check the credential and the model id in the company config"
        )
        raise HarnessProviderMissingError(msg) from exc


async def _check_docker() -> None:
    """Confirm the Docker daemon answers before any unit runs.

    Raises:
        HarnessDockerUnavailableError: The daemon did not answer.
    """
    try:
        async with aiodocker.Docker() as client:
            await client.version()
    # Only what an absent or unhealthy daemon actually raises: a refused or
    # timed-out connection (both ``OSError``), the daemon's own error, and the
    # ``ValueError`` aiodocker raises when it can find no host to talk to.
    except (aiodocker.DockerError, OSError, ValueError) as exc:
        msg = (
            "the Docker daemon is unreachable, and every unit in the sweep "
            "builds in a container"
        )
        raise HarnessDockerUnavailableError(msg) from exc


__all__ = ["run_preflight"]
