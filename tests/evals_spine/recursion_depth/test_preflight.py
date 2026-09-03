# module-kind: tests
"""Everything knowable before the sweep boots is settled before it boots."""

import contextlib
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import ClassVar

import aiodocker
import pytest
from structlog.testing import capture_logs

from evals.errors import HarnessDockerUnavailableError, HarnessProviderMissingError
from evals.recursion_depth import preflight as preflight_module
from evals.recursion_depth.manifest import load_manifest
from evals.recursion_depth.preflight import (
    _check_docker,
    _probe_embedder,
    _probe_pair,
    run_preflight,
)
from synthorg.config.provider_schema import ProviderConfig, ProviderModelConfig
from synthorg.config.schema import RootConfig
from synthorg.core.types import NotBlankStr
from synthorg.memory.errors import MemoryEmbeddingError
from synthorg.observability.events.evals import EVALS_RECURSION_EMBEDDER_PROBED
from synthorg.providers.drivers.litellm_auth import OPENAI_SDK_ROUTES
from synthorg.providers.embedding_endpoint import EmbeddingEndpoint
from synthorg.providers.enums import AuthType
from synthorg.providers.errors import ProviderError

pytestmark = pytest.mark.unit

SDK_ROUTE = next(iter(sorted(OPENAI_SDK_ROUTES)))

_MANIFEST = (
    Path(__file__).resolve().parents[3] / "evals" / "recursion_depth" / "manifest.yaml"
)

#: What an upstream says when the credential is wrong, which is one of three
#: failures the operator has to be able to tell apart from the message alone.
_UPSTREAM_REFUSAL = "invalid api key"

#: What a driver says when it cannot release what it opened, which is a fact
#: about the harness rather than about the operator's configuration.
_CLEANUP_FAILURE = "socket already detached"


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
    """Build a config whose providers block carries every manifest binding.

    Returns:
        The config.
    """
    return RootConfig(
        company_name=NotBlankStr("Probed"),
        providers={
            "example-provider": ProviderConfig(
                connection_name=NotBlankStr("example-provider"),
                models=(ProviderModelConfig(id=NotBlankStr("example-capable-001")),),
            ),
            "example-embedding-provider": ProviderConfig(
                litellm_provider=NotBlankStr(SDK_ROUTE),
                auth_type=AuthType.NONE,
                base_url=NotBlankStr("http://localhost:11434/v1"),
                models=(
                    ProviderModelConfig(
                        id=NotBlankStr("test-embed-001"),
                        alias=NotBlankStr("example-embedding-001"),
                    ),
                ),
            ),
        },
    )


class TestEmbedderCoverage:
    """Memory that cannot resolve its provider stays OFF, after the plan is paid."""

    async def test_an_embedder_provider_nothing_carries_is_refused_by_name(
        self,
    ) -> None:
        # The two pairs are present, so what is missing is the embedder alone.
        config = RootConfig(
            company_name=NotBlankStr("Pairs only"),
            providers={"example-provider": _configured().providers["example-provider"]},
        )

        with pytest.raises(
            HarnessProviderMissingError, match="example-embedding-provider"
        ):
            await run_preflight(
                manifest=load_manifest(_MANIFEST), company_config=config
            )


class TestTheEmbedderProbe:
    """The embedder is probed with the call memory itself makes.

    Found by planning the first wired recording: the preflight probed the two
    pairs and nothing else, so an embedder that could not answer surfaced only
    as memory OFF in the smoke's wiring report, after a plan had been bought.
    """

    async def test_an_embedder_that_cannot_answer_is_refused_against_its_role(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def _refuse(**kwargs: object) -> int:
            del kwargs
            raise MemoryEmbeddingError(_UPSTREAM_REFUSAL)

        monkeypatch.setattr(preflight_module, "probe_embedder_dims", _refuse)
        manifest = load_manifest(_MANIFEST)

        with pytest.raises(HarnessProviderMissingError, match="embedder"):
            await _probe_embedder(
                embedder=manifest.embedder, company_config=_configured()
            )

    async def test_the_probe_is_addressed_as_memory_addresses_it(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Route, alias and endpoint, or the probe proves a call nothing makes."""
        seen: list[dict[str, object]] = []

        async def _answer(**kwargs: object) -> int:
            seen.append(kwargs)
            return 8

        monkeypatch.setattr(preflight_module, "probe_embedder_dims", _answer)
        manifest = load_manifest(_MANIFEST)

        await _probe_embedder(embedder=manifest.embedder, company_config=_configured())

        endpoint = seen[0]["endpoint"]
        assert isinstance(endpoint, EmbeddingEndpoint)
        assert endpoint.api_base == "http://localhost:11434/v1"
        assert endpoint.route == SDK_ROUTE
        assert endpoint.model_ids is not None
        assert endpoint.model_ids["example-embedding-001"] == "test-embed-001"
        assert seen[0]["provider"] == "example-embedding-provider"
        assert seen[0]["model"] == "example-embedding-001"

    async def test_a_probe_that_answers_is_logged_with_its_width(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def _answer(**_kwargs: object) -> int:
            return 8

        monkeypatch.setattr(preflight_module, "probe_embedder_dims", _answer)
        manifest = load_manifest(_MANIFEST)

        with capture_logs() as logs:
            await _probe_embedder(
                embedder=manifest.embedder, company_config=_configured()
            )

        probed = [
            log for log in logs if log["event"] == EVALS_RECURSION_EMBEDDER_PROBED
        ]
        assert len(probed) == 1
        assert probed[0]["width"] == 8


type _Complete = Callable[[object, str], Awaitable[object]]


def _answering(answer: _Complete, *, close_error: Exception | None = None) -> object:
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
            if close_error is not None:
                raise close_error

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

    A driver on an HTTP-backed path builds an ``httpx.AsyncClient`` on its FIRST
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


class TestCleanupNeverOutranksTheProbesVerdict:
    """A raise inside `finally` REPLACES the exception in flight.

    The probe exists to name which of three unrelated fixes an operator needs
    (a bad credential, an unknown model id, an unreachable endpoint). A driver
    that fails to close is a fourth, unrelated fact, and letting it overwrite
    the verdict throws away the entire output of the probe.
    """

    async def test_a_failing_close_does_not_erase_the_upstream_diagnosis(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def _refuse(messages: object, model_id: str) -> object:
            del messages, model_id
            raise ProviderError(_UPSTREAM_REFUSAL)

        monkeypatch.setattr(
            preflight_module,
            "ProviderRegistry",
            _answering(_refuse, close_error=RuntimeError(_CLEANUP_FAILURE)),
        )
        manifest = load_manifest(_MANIFEST)

        with pytest.raises(HarnessProviderMissingError) as caught:
            await _probe_pair(
                role="executor", pair=manifest.executor, company_config=_configured()
            )

        assert _UPSTREAM_REFUSAL in str(caught.value)
        assert _CLEANUP_FAILURE not in str(caught.value)

    async def test_an_unnamed_failure_still_releases_the_registry(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The release must not depend on the probe failing a way we named.

        The two named branches are the ones a misconfigured pair produces. An
        unnamed failure, and a cancellation above all, leaves the same client
        open, and a run stopped by an operator is exactly when that happens.
        """

        async def _break(messages: object, model_id: str) -> object:
            del messages, model_id
            raise MemoryError

        registry = _answering(_break)
        monkeypatch.setattr(preflight_module, "ProviderRegistry", registry)
        manifest = load_manifest(_MANIFEST)

        with pytest.raises(MemoryError):
            await _probe_pair(
                role="executor", pair=manifest.executor, company_config=_configured()
            )

        assert registry.closed  # type: ignore[attr-defined]  # the stand-in's own recorder

    async def test_an_unnamed_failure_is_not_displaced_by_a_cleanup_failure(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def _break(messages: object, model_id: str) -> object:
            del messages, model_id
            raise MemoryError

        monkeypatch.setattr(
            preflight_module,
            "ProviderRegistry",
            _answering(_break, close_error=RuntimeError(_CLEANUP_FAILURE)),
        )
        manifest = load_manifest(_MANIFEST)

        with pytest.raises(MemoryError):
            await _probe_pair(
                role="executor", pair=manifest.executor, company_config=_configured()
            )

    async def test_a_cleanup_only_failure_is_still_reported(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """With no verdict to displace, the leak is the only thing wrong."""

        async def _answer(messages: object, model_id: str) -> object:
            del messages, model_id
            return object()

        monkeypatch.setattr(
            preflight_module,
            "ProviderRegistry",
            _answering(_answer, close_error=RuntimeError(_CLEANUP_FAILURE)),
        )
        manifest = load_manifest(_MANIFEST)

        with pytest.raises(HarnessProviderMissingError, match="leaked client"):
            await _probe_pair(
                role="executor", pair=manifest.executor, company_config=_configured()
            )


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
