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
from collections.abc import Sequence
from typing import Final

import aiodocker

from evals.errors import (
    HarnessDockerUnavailableError,
    HarnessImageUnresolvedError,
    HarnessProviderMissingError,
)
from evals.recursion_depth.manifest import ModelPair, RecursionDepthManifest
from synthorg.config.schema import RootConfig
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.resilience import GeneralRetryHandler
from synthorg.observability import get_logger
from synthorg.observability.events.evals import (
    EVALS_HARNESS_DOCKER_UNAVAILABLE,
    EVALS_HARNESS_IMAGE_INSPECT_RETRYING,
    EVALS_HARNESS_IMAGE_UNRESOLVED,
    EVALS_HARNESS_PROBE_CLEANUP_FAILED,
    EVALS_HARNESS_PROVIDER_MISSING,
    EVALS_RECURSION_PREFLIGHT_PASSED,
)
from synthorg.observability.redaction import safe_error_description
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

#: The daemon's own definitive "I hold nothing under this name". Every other
#: status is it failing to answer.
_NOT_FOUND_STATUS: Final[int] = 404

#: Seconds one image inspect may take. A local daemon answers this in
#: milliseconds; a wedged one never answers at all, and without a bound the
#: preflight that exists to fail fast is the thing that hangs.
_INSPECT_TIMEOUT_SECONDS: Final[float] = 30.0

#: Attempts per reference. Small: this is a local socket, so the failures worth
#: re-asking about are a daemon mid-restart or a momentary stall, neither of
#: which needs many tries. What it buys is not tearing down a booted host over
#: a blip.
_INSPECT_RETRY_ATTEMPTS: Final[int] = 3

#: Backoff between those attempts.
_INSPECT_RETRY_BASE_SECONDS: Final[float] = 0.5
_INSPECT_RETRY_CAP_SECONDS: Final[float] = 2.0


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
    probe_failure: HarnessProviderMissingError | None = None
    unexpected = False
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
        logger.error(
            EVALS_HARNESS_PROVIDER_MISSING,
            role=role,
            pair=pair.label,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        # The upstream's own words, redacted, rather than only our summary of
        # them. "Could not complete" does not separate a bad credential from an
        # unknown model id from an unreachable endpoint, and those have three
        # different fixes; without the detail the operator reads the traceback
        # to find what the probe already knew.
        msg = (
            f"the {role} pair {pair.label} could not complete a one-token "
            f"request, so no cell recorded against it would measure anything: "
            f"{safe_error_description(exc)}"
        )
        probe_failure = HarnessProviderMissingError(msg)
        probe_failure.__cause__ = exc
    except BaseException:
        # Everything the two branches above do not name, cancellation included.
        # Nothing is concluded from it, and it is already on its way out; the
        # flag exists only so cleanup knows not to displace it.
        unexpected = True
        raise
    finally:
        # This registry is built for the probe alone and is unreachable
        # afterwards, so whatever its drivers opened lazily during the call is
        # released here or not at all. On an HTTP-backed driver path that is a
        # live ``httpx.AsyncClient``, and the failure branches need this as
        # much as the success one: an endpoint that refuses or hangs is exactly
        # where a client is left open.
        #
        # A `finally`, so a cancellation or an unnamed failure cannot skip the
        # release; but an exception raised HERE would REPLACE the one in
        # flight, erasing the message naming the bad credential, the unknown
        # model or the timeout, which is the whole output of this probe. So
        # cleanup is told whether anything is already on its way to the caller,
        # and it reports its own failure only when nothing is.
        await _release(
            registry, already_failing=probe_failure is not None or unexpected
        )
    if probe_failure is not None:
        raise probe_failure


async def _release(registry: ProviderRegistry, *, already_failing: bool) -> None:
    """Close *registry*, without letting cleanup outrank what went wrong first.

    Args:
        registry: The single-use registry the probe dispatched through.
        already_failing: Whether something is already on its way to the caller,
            either the probe's own verdict or an unnamed failure passing
            through. A cleanup failure never displaces one of those; it is
            logged and dropped.

    Raises:
        HarnessProviderMissingError: Cleanup failed and nothing else did, so
            this is the only thing that went wrong and the operator would
            otherwise never hear about it.
    """
    try:
        await registry.aclose()
    except Exception as exc:
        reraise_critical(exc)
        logger.warning(
            EVALS_HARNESS_PROBE_CLEANUP_FAILED,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
            probe_already_failed=already_failing,
        )
        if not already_failing:
            msg = (
                f"the probe's provider connection could not be released, so a "
                f"sweep would run alongside a leaked client: "
                f"{safe_error_description(exc)}"
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
        logger.error(
            EVALS_HARNESS_DOCKER_UNAVAILABLE,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        msg = (
            "the Docker daemon is unreachable, and every unit in the sweep "
            "builds in a container"
        )
        raise HarnessDockerUnavailableError(msg) from exc


async def check_images_resolve(references: Sequence[str]) -> None:
    """Refuse to spend when a declared image is not on the daemon.

    The daemon answers this at boot and the answer was LOGGED rather than
    acted on, on the reasoning that a recording naming an absent image "fails
    on its first container, which is a better place to learn it". It does fail
    there, and that place is not better: measured on a queued cell, the run
    booted clean, planned, spent 85,555 tokens, and then died at the first
    container it opened with ``[404] No such image``, having bought a plan for
    a cell that could never be graded. The reference was a published TAG that
    upstream no longer carries, which a digest would have made obvious and a
    tag never does.

    Asked as early as the reference is KNOWN, which is after the host boots:
    unless ``--sandbox-image`` names one, it comes from the running instance's
    own settings resolver. That is later than free, so the caller's comment
    says so; what it buys is refusing before the first SESSION rather than
    before the first container, which is where the money is.

    A daemon that cannot answer is a different condition from an image it does
    not hold, and only the second is this function's verdict. The distinction
    is the one ``workspace_mount`` already draws for the same call: folding a
    timeout or a 500 into "no such image" would tell an operator to rebuild an
    image that is fine, after tearing down a booted host to say it.

    Args:
        references: Image references the run will need, in the order a reader
            should be told about them.

    Raises:
        HarnessImageUnresolvedError: The daemon holds no image under one of
            them.
        HarnessDockerUnavailableError: The daemon could not answer, so
            nothing was determined either way.
    """
    missing: list[str] = []
    retry = GeneralRetryHandler(
        # A 404 is the daemon's definitive answer and re-asking cannot change
        # it. Everything else is the daemon failing to answer, which a moment
        # later it may well do.
        retryable=lambda exc: not _is_absent(exc),
        max_attempts=_INSPECT_RETRY_ATTEMPTS,
        base=_INSPECT_RETRY_BASE_SECONDS,
        cap=_INSPECT_RETRY_CAP_SECONDS,
        event=EVALS_HARNESS_IMAGE_INSPECT_RETRYING,
        jitter=False,
    )
    async with aiodocker.Docker() as client:
        for reference in references:

            async def inspect(reference: str = reference) -> None:
                async with asyncio.timeout(_INSPECT_TIMEOUT_SECONDS):
                    await client.images.inspect(reference)

            try:
                await retry.execute(inspect, reference=reference)
            except Exception as exc:
                reraise_critical(exc)
                if not _is_absent(exc):
                    # The daemon failed to ANSWER, which is not the same
                    # finding as an image that is genuinely absent: an alert
                    # keyed on the unresolved event would file a Docker
                    # outage as a broken reference and send whoever reads it
                    # looking for a tag.
                    logger.error(
                        EVALS_HARNESS_DOCKER_UNAVAILABLE,
                        image=reference,
                        error_type=type(exc).__name__,
                        error=safe_error_description(exc),
                    )
                    msg = (
                        f"the Docker daemon could not answer whether it holds "
                        f"{reference}, so nothing about it was determined and "
                        f"a sweep would be gambling its whole spend on the "
                        f"answer: {safe_error_description(exc)}"
                    )
                    raise HarnessDockerUnavailableError(msg) from exc
                missing.append(reference)
    if not missing:
        return
    logger.error(EVALS_HARNESS_IMAGE_UNRESOLVED, missing=tuple(missing))
    msg = (
        f"the Docker daemon holds no image under {', '.join(missing)}, and a "
        f"cell that cannot open a container cannot be graded, so every unit "
        f"would be recorded unavailable AFTER its sessions had been paid for. "
        f"Build the image from this tree (`make sandbox-image`) and pass "
        f"--sandbox-image, or pass a digest that exists: a published tag can "
        f"stop resolving without anything in this repository changing"
    )
    raise HarnessImageUnresolvedError(msg)


def _is_absent(exc: BaseException) -> bool:
    """Whether *exc* is the daemon SAYING it holds no such image.

    Args:
        exc: What the inspect raised.

    Returns:
        True only for the daemon's own definitive negative.
    """
    return isinstance(exc, aiodocker.DockerError) and exc.status == _NOT_FOUND_STATUS


__all__ = ["check_images_resolve", "run_preflight"]
