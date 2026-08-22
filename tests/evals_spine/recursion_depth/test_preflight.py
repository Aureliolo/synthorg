# module-kind: tests
"""Everything knowable before the sweep boots is settled before it boots."""

import contextlib
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import ClassVar

import aiodocker
import pytest

from evals.errors import HarnessDockerUnavailableError, HarnessProviderMissingError
from evals.recursion_depth import preflight as preflight_module
from evals.recursion_depth.manifest import load_manifest
from evals.recursion_depth.preflight import _check_docker, _probe_pair, run_preflight
from synthorg.config.provider_schema import ProviderConfig, ProviderModelConfig
from synthorg.config.schema import RootConfig
from synthorg.core.types import NotBlankStr
from synthorg.providers.errors import ProviderError

pytestmark = pytest.mark.unit

_MANIFEST = (
    Path(__file__).resolve().parents[3] / "evals" / "recursion_depth" / "manifest.yaml"
)

#: What an upstream says when the credential is wrong, which is one of three
#: failures the operator has to be able to tell apart from the message alone.
_UPSTREAM_REFUSAL = "invalid api key"


class TestProviderCoverage:
    """A pair naming a provider nothing carries cannot record anything."""

    async def test_an_empty_providers_block_is_refused_by_name(self) -> None:
        # The easiest mistake there is: omitting --company-config, whose
        # default baseline carries no providers block at all. Left to the
        # sweep, it fails once per cell as a decomposition error.
        with pytest.raises(HarnessProviderMissingError, match="example-provider"):
            await run_preflight(
                manifest=load_manifest(_MANIFEST),
                company_config=RootConfig(company_name=NotBlankStr("Empty")),
            )

    async def test_the_message_names_the_flag_that_fixes_it(self) -> None:
        with pytest.raises(HarnessProviderMissingError, match="--company-config"):
            await run_preflight(
                manifest=load_manifest(_MANIFEST),
                company_config=RootConfig(company_name=NotBlankStr("Empty")),
            )

    async def test_it_runs_before_anything_is_built(self) -> None:
        # No Docker call, no host, no gateway: the coverage check needs none of
        # them, so a config mistake is reported on a machine with no daemon at
        # all rather than being masked by a daemon failure.
        with pytest.raises(HarnessProviderMissingError):
            await run_preflight(
                manifest=load_manifest(_MANIFEST),
                company_config=RootConfig(company_name=NotBlankStr("Empty")),
            )


def _configured() -> RootConfig:
    """Build a config whose providers block carries the manifest's pair.

    Returns:
        The config.
    """
    return RootConfig(
        company_name=NotBlankStr("Probed"),
        providers={
            "example-provider": ProviderConfig(
                connection_name=NotBlankStr("example-provider"),
                models=(ProviderModelConfig(id=NotBlankStr("example-capable-001")),),
            )
        },
    )


type _Complete = Callable[[object, str], Awaitable[object]]


def _answering(answer: _Complete) -> object:
    """Build a registry whose one provider answers with *answer*.

    Written as a plain class rather than with ``mock_of``: the probe reaches
    the provider through ``ProviderRegistry.from_config(...).get(...)``, so
    what is being substituted is the registry's construction path, and a spec'd
    double of the concrete registry would have to be a real one to answer it.

    Args:
        answer: What ``provider.complete`` does when awaited.

    Returns:
        A stand-in for ``ProviderRegistry``.
    """

    class _Provider:
        async def complete(
            self, messages: object, model_id: str, **kwargs: object
        ) -> object:
            del kwargs
            return await answer(messages, model_id)

    class _Registry:
        closed: ClassVar[list[bool]] = []

        @staticmethod
        def from_config(configs: object) -> _Registry:
            del configs
            return _Registry()

        @staticmethod
        def get(name: str) -> object:
            del name
            return _Provider()

        async def aclose(self) -> None:
            type(self).closed.append(True)

    return _Registry


class TestTheProbe:
    """A pair that cannot answer is named here, not once per cell.

    The probe exists because the same failure found later surfaces as
    ``decomposition.failed`` with a ``DecompositionError`` reason, which names
    the wrong subsystem entirely and sends an operator to the planner.
    """

    async def test_a_refusing_provider_is_reported_against_its_role(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def _refuse(messages: object, model_id: str) -> object:
            del messages, model_id
            raise ProviderError(_UPSTREAM_REFUSAL)

        monkeypatch.setattr(preflight_module, "ProviderRegistry", _answering(_refuse))
        manifest = load_manifest(_MANIFEST)

        with pytest.raises(HarnessProviderMissingError, match="executor"):
            await _probe_pair(
                role="executor",
                pair=manifest.executor,
                company_config=_configured(),
            )

    async def test_the_upstreams_own_words_reach_the_operator(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A bare "could not complete" does not separate three different fixes."""

        async def _refuse(messages: object, model_id: str) -> object:
            del messages, model_id
            raise ProviderError(_UPSTREAM_REFUSAL)

        monkeypatch.setattr(preflight_module, "ProviderRegistry", _answering(_refuse))
        manifest = load_manifest(_MANIFEST)

        with pytest.raises(HarnessProviderMissingError, match=_UPSTREAM_REFUSAL):
            await _probe_pair(
                role="executor",
                pair=manifest.executor,
                company_config=_configured(),
            )

    async def test_a_pair_that_never_answers_is_refused_too(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A hung endpoint is as unusable as a refusing one, and silent."""

        async def _hang(messages: object, model_id: str) -> object:
            del messages, model_id
            raise TimeoutError

        monkeypatch.setattr(preflight_module, "ProviderRegistry", _answering(_hang))
        manifest = load_manifest(_MANIFEST)

        with pytest.raises(HarnessProviderMissingError, match="reviewer"):
            await _probe_pair(
                role="reviewer",
                pair=manifest.reviewer,
                company_config=_configured(),
            )

    async def test_a_pair_that_answers_passes_silently(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def _answer(messages: object, model_id: str) -> object:
            del messages, model_id
            return object()

        monkeypatch.setattr(preflight_module, "ProviderRegistry", _answering(_answer))
        manifest = load_manifest(_MANIFEST)

        await _probe_pair(
            role="executor", pair=manifest.executor, company_config=_configured()
        )


class TestTheProbeReleasesWhatItOpened:
    """The probe's registry is unreachable after it, so it closes it or leaks.

    A driver on the ollama path builds an ``httpx.AsyncClient`` on its FIRST
    dispatch and holds it for the driver's life. This registry exists for one
    call, so nothing else is ever in a position to release that client.
    """

    @pytest.mark.parametrize("outcome", ["answers", "refuses", "hangs"])
    async def test_the_registry_is_closed_however_the_probe_ends(
        self, monkeypatch: pytest.MonkeyPatch, outcome: str
    ) -> None:
        """A refusing or hanging endpoint is exactly where a client is left."""

        async def _act(messages: object, model_id: str) -> object:
            del messages, model_id
            if outcome == "refuses":
                raise ProviderError(_UPSTREAM_REFUSAL)
            if outcome == "hangs":
                raise TimeoutError
            return object()

        registry = _answering(_act)
        monkeypatch.setattr(preflight_module, "ProviderRegistry", registry)
        manifest = load_manifest(_MANIFEST)

        with contextlib.suppress(HarnessProviderMissingError):
            await _probe_pair(
                role="executor", pair=manifest.executor, company_config=_configured()
            )

        assert registry.closed  # type: ignore[attr-defined]  # the stand-in's own recorder


class TestTheDockerCheck:
    """Every unit builds in a container, so an absent daemon stops the sweep."""

    @pytest.mark.parametrize(
        "failure",
        [
            aiodocker.DockerError(500, "daemon down"),
            OSError("connection refused"),
            ValueError("no docker host"),
        ],
        ids=["daemon-error", "unreachable", "no-host"],
    )
    async def test_every_shape_of_absence_is_one_typed_refusal(
        self, monkeypatch: pytest.MonkeyPatch, failure: Exception
    ) -> None:
        """Three unrelated exception types, one thing wrong, one message."""

        def _unavailable() -> object:
            raise failure

        monkeypatch.setattr(aiodocker, "Docker", _unavailable)

        with pytest.raises(HarnessDockerUnavailableError, match="container"):
            await _check_docker()
