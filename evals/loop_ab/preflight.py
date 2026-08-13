# module-kind: code
"""Checks that run before a recording matrix spends anything.

Every condition here is discoverable in seconds and, left undiscovered, costs
either money or a run. A manifest capability naming a provider the company config
does not carry fails once per cell, so the operator gets a scoreboard of
unavailable rows instead of one message; forgetting ``--company-config``
entirely (the default baseline carries no ``providers`` block at all) produces
exactly that, and it is the easiest mistake to make. An unreachable Docker
daemon is worse than useless to discover late: every loop drives a sandbox, so
the native legs can dispatch real, billable turns before the first shell call
reveals the daemon was never there.

The third is the provider's own service time. Latency is a scored dimension and
the matrix records its cells one after another over about an hour, so a
provider queueing an order of magnitude above its usual rate scores each cell
against whatever the queue was doing when that cell ran rather than against the
other cells. Measured once, a hosted model answered a five-token request in
72s and, twenty minutes later, in 1.2s; a matrix spanning that is not a
comparison. The probe is also the warm-up, because a cold model load lands
entirely on whichever cell the matrix happens to record first.

All three are properties of the machine, the configuration or the upstream,
never of a loop, so none belongs in the per-cell handler that records a loop as
unavailable.
"""

import asyncio
import shutil
from collections.abc import Awaitable, Callable
from typing import Final

import aiodocker

from evals.errors import (
    BriefExecutionError,
    EvalToolMissingError,
    LoopAbDockerUnavailableError,
    LoopAbProviderDegradedError,
    LoopAbProviderMissingError,
)
from evals.loop_ab.manifest import CapabilityEntry, LoopAbManifest
from evals.models.brief import Brief
from synthorg.config.schema import RootConfig
from synthorg.core.clock import Clock, SystemClock
from synthorg.observability import get_logger
from synthorg.observability.events.evals import (
    EVALS_EXECUTABLE_TOOL_MISSING,
    EVALS_LOOP_AB_PREFLIGHT_LATENCY,
    EVALS_LOOP_AB_PREFLIGHT_PASSED,
    EVALS_LOOP_AB_PROVIDER_MISSING,
)
from synthorg.providers.enums import MessageRole
from synthorg.providers.models import ChatMessage, CompletionConfig
from synthorg.providers.registry import ProviderRegistry

logger = get_logger(__name__)

#: Seconds a capability's model may take to answer a trivial request and still be
#: worth measuring against. Set from the observed working range rather than a
#: service-level promise: the recorded matrix that produced a usable scoreboard
#: ran at a median of 1.6s per full agent turn, so a five-token reply taking
#: more than this is queueing, not working.
DEFAULT_LATENCY_CEILING_SECONDS: Final[float] = 15.0

#: Attempts per capability. The first pays whatever cold load the provider owes and
#: is discarded, so a cold model is warmed rather than refused and that cost
#: stays off the first cell. The rest are judged on their WORST: this exists to
#: refuse an intermittently-degraded provider, and a provider that answers once
#: in 1.2s and once in 72s is exactly that, so taking the best of them would
#: pass the very condition the check was written for.
_PROBE_ATTEMPTS: Final[int] = 3

#: Leading attempts spent warming the model rather than judging it.
_WARMUP_ATTEMPTS: Final[int] = 1

#: How far past the band a single attempt may run before it is abandoned. A
#: probe has to answer faster than the thing it is protecting against: one
#: measured attempt took 311s, retries included, which is five minutes spent
#: establishing a fact the first 60 already settled. An abandoned attempt is
#: recorded at its bound, which is a lower bound and already outside the band.
_PROBE_TIMEOUT_FACTOR: Final[float] = 4.0

_PROBE_PROMPT: Final[str] = "Reply with the single word: ok"
_PROBE_MAX_TOKENS: Final[int] = 5

#: Times one completion against a capability and returns the seconds it took.
LatencyProbe = Callable[[CapabilityEntry], Awaitable[float]]


async def run_preflight(
    *,
    manifest: LoopAbManifest,
    company_config: RootConfig,
    briefs: tuple[Brief, ...] = (),
    latency_ceiling_seconds: float = DEFAULT_LATENCY_CEILING_SECONDS,
    check_docker: bool = True,
    probe: LatencyProbe | None = None,
) -> None:
    """Refuse a matrix the machine, the configuration or the upstream fails.

    Args:
        manifest: The loaded recording manifest.
        company_config: The company config the run will boot against.
        briefs: The suite the matrix will grade, whose check commands have to
            exist on this machine. Empty skips the check.
        latency_ceiling_seconds: The band a capability's warm response must fall in.
            Zero or less skips the probe, for an operator who has decided the
            provider's current weather is what they want measured.
        check_docker: Whether to require a reachable daemon. Off only for the
            offline suite, which drives no container.
        probe: Times one completion against a capability. Defaults to a real
            completion through the capability's configured provider.

    Raises:
        LoopAbProviderMissingError: A manifest capability names a provider the
            company config does not carry.
        BriefExecutionError: A brief declares no checks, so no loop's run of it
            could be graded.
        EvalToolMissingError: A brief grades with a command this machine does
            not have.
        LoopAbDockerUnavailableError: The Docker daemon is unreachable.
        LoopAbProviderDegradedError: A capability's warm response is outside the band.
    """
    _check_tier_providers(manifest=manifest, company_config=company_config)
    _check_briefs_gradeable(briefs)
    _check_grading_tools(briefs)
    if check_docker:
        await _check_docker()
    await _check_provider_latency(
        manifest=manifest,
        company_config=company_config,
        ceiling_seconds=latency_ceiling_seconds,
        probe=probe,
    )
    logger.info(
        EVALS_LOOP_AB_PREFLIGHT_PASSED,
        capabilities=len(manifest.capabilities),
        providers=len(company_config.providers),
    )


def _check_tier_providers(
    *, manifest: LoopAbManifest, company_config: RootConfig
) -> None:
    """Confirm every capability's bound provider exists in the company config.

    Args:
        manifest: The loaded recording manifest.
        company_config: The company config the run will boot against.

    Raises:
        LoopAbProviderMissingError: One or more capabilities name an absent provider.
    """
    missing = sorted(
        {
            capability.provider
            for capability in manifest.capabilities
            if capability.provider not in company_config.providers
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
        f"manifest capabilities name providers absent from the company config: "
        f"{', '.join(missing)}. Pass --company-config pointing at a config whose "
        f"providers block covers every capability."
    )
    raise LoopAbProviderMissingError(msg)


def _check_briefs_gradeable(briefs: tuple[Brief, ...]) -> None:
    """Confirm every brief declares the checks the A/B grades it by.

    Knowable from the suite alone, so it is settled here rather than after a
    cell has run: the loop would otherwise be dispatched, spend its turns, and
    only then fail on a fact that was true before it started.

    Args:
        briefs: The suite the matrix will grade.

    Raises:
        BriefExecutionError: A brief declares no checks block.
    """
    ungradeable = sorted(brief.brief_id for brief in briefs if brief.checks is None)
    if not ungradeable:
        return
    msg = (
        f"briefs declare no checks block and so cannot be graded: "
        f"{', '.join(ungradeable)}. The loop A/B grades a workspace by running "
        f"an executable brief's checks against it."
    )
    raise BriefExecutionError(msg)


def _check_grading_tools(briefs: tuple[Brief, ...]) -> None:
    """Confirm every command the suite grades with exists on this machine.

    A grading interpreter is a property of the machine, not of a loop, so
    discovering it missing per cell burns the whole matrix producing rows that
    say the loop was unavailable when the loop ran fine and only the grader
    could not.

    Args:
        briefs: The suite the matrix will grade.

    Raises:
        EvalToolMissingError: A brief grades with an absent command.
    """
    missing = sorted(
        {
            spec.cmd[0]
            for brief in briefs
            if brief.checks is not None
            for specs in (
                brief.checks.hidden_tests,
                brief.checks.build,
                brief.checks.invariants,
            )
            for spec in specs
            if shutil.which(spec.cmd[0]) is None
        }
    )
    if not missing:
        return
    logger.error(EVALS_EXECUTABLE_TOOL_MISSING, commands=tuple(missing))
    msg = (
        f"the brief suite grades with commands this machine does not have: "
        f"{', '.join(missing)}. Every cell would run, spend, and then fail to "
        f"be graded."
    )
    raise EvalToolMissingError(msg)


async def _check_provider_latency(
    *,
    manifest: LoopAbManifest,
    company_config: RootConfig,
    ceiling_seconds: float,
    probe: LatencyProbe | None,
) -> None:
    """Confirm every capability answers a trivial request inside the band.

    Each capability is a separate model pool, so a fast small model says nothing
    about the large one the same matrix scores; all of them are probed.

    Args:
        manifest: The loaded recording manifest.
        company_config: The company config the run will boot against.
        ceiling_seconds: The band, or zero to skip the probe entirely.
        probe: Times one completion, or ``None`` for a real one.

    Raises:
        LoopAbProviderDegradedError: A capability's warm response is outside the band.
    """
    if ceiling_seconds <= 0:
        return
    timer = probe if probe is not None else _completion_probe(company_config)
    budget = ceiling_seconds * _PROBE_TIMEOUT_FACTOR
    for entry in manifest.capabilities:
        attempts = [
            await _bounded(timer, entry, budget) for _ in range(_PROBE_ATTEMPTS)
        ]
        judged = attempts[_WARMUP_ATTEMPTS:]
        slowest = max(judged)
        logger.info(
            EVALS_LOOP_AB_PREFLIGHT_LATENCY,
            capability=entry.capability,
            provider=entry.provider,
            model_id=entry.model_id,
            slowest_seconds=round(slowest, 2),
            warmup_seconds=round(attempts[0], 2),
            attempts=tuple(round(a, 2) for a in attempts),
            ceiling_seconds=ceiling_seconds,
        )
        if slowest <= ceiling_seconds:
            continue
        msg = (
            f"capability {entry.capability!r} ({entry.model_id}) took "
            f"{slowest:.1f}s on one of "
            f"{len(judged)} warmed {_PROBE_MAX_TOKENS}-token requests, outside "
            f"the {ceiling_seconds}s band. Latency is a scored dimension and the "
            f"matrix records for about an hour, so cells would be scored "
            f"against the provider's queue rather than against each other. "
            f"Retry when it recovers, or pass "
            f"--preflight-latency-seconds 0 to measure it as it is."
        )
        raise LoopAbProviderDegradedError(msg)


async def _bounded(
    timer: LatencyProbe,
    entry: CapabilityEntry,
    budget: float,
) -> float:
    """Time one attempt, abandoning it once it is decisively outside the band.

    Returns:
        The measured seconds, or *budget* when the attempt was abandoned.
    """
    try:
        async with asyncio.timeout(budget):
            return await timer(entry)
    except TimeoutError:
        return budget


def _completion_probe(company_config: RootConfig) -> LatencyProbe:
    """Build the probe that times a real completion against a capability.

    Deliberately dispatches to the capability's provider directly rather than through
    the recorder's gateway: the gateway is not up yet when the preflight runs,
    and what is in question is the upstream's service time, not our own hop.

    Returns:
        A probe timing one completion per call.
    """
    clock: Clock = SystemClock()

    async def _probe(capability: CapabilityEntry) -> float:
        """Time one trivial completion against *capability*.

        Returns:
            Seconds the completion took.
        """
        registry = ProviderRegistry.from_config(
            {capability.provider: company_config.providers[capability.provider]}
        )
        provider = registry.get(capability.provider)
        started = clock.monotonic()
        await provider.complete(
            [ChatMessage(role=MessageRole.USER, content=_PROBE_PROMPT)],
            capability.model_id,
            config=CompletionConfig(max_tokens=_PROBE_MAX_TOKENS),
        )
        return clock.monotonic() - started

    return _probe


async def _check_docker() -> None:
    """Confirm the Docker daemon answers before any cell runs.

    Raises:
        LoopAbDockerUnavailableError: The daemon did not answer.
    """
    try:
        async with aiodocker.Docker() as client:
            await client.version()
    # Only what an absent or unhealthy daemon actually raises: a refused or
    # timed-out connection (both ``OSError``), the daemon's own error, and the
    # ``ValueError`` aiodocker raises when it can find no host to talk to.
    # Anything else is a fault in this process, and reporting it as "Docker is
    # unreachable" would send the operator to check a daemon that is fine.
    except (aiodocker.DockerError, OSError, ValueError) as exc:
        msg = (
            "the Docker daemon is unreachable, and every loop in the matrix "
            "runs its shell tool in a container"
        )
        raise LoopAbDockerUnavailableError(msg) from exc


__all__ = ["DEFAULT_LATENCY_CEILING_SECONDS", "LatencyProbe", "run_preflight"]
