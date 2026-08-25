# module-kind: tests
"""The A/B recorder's own gateway host, driven over its real socket.

The whole point of the host is that mint and verify are the same
``GatewaySigner`` instance, which a standalone script cannot otherwise obtain.
Asserting that in-process would prove nothing, so these mint a bearer from the
host's signer and spend it against the host's own HTTP surface.

No provider is contacted: the company config binds the deterministic scripted
driver, so a full round trip costs nothing.
"""

import asyncio
import hashlib
import os
from pathlib import Path

import httpx
import pytest

from evals.errors import (
    HarnessGatewayUnavailableError,
    HarnessHostAlreadyStartedError,
)
from evals.harness.host import (
    DEFAULT_CONTAINER_HOST,
    RecordingGatewayHost,
    RecordingHostConfig,
    _cancel_serving,
)
from synthorg.core.auth.roles import HumanRole
from synthorg.core.types import NotBlankStr
from synthorg.llm.gateway_binding import mint_run_token
from synthorg.llm.gateway_token import GatewaySigner
from synthorg.persistence.state import persistence_of
from synthorg.settings.model_ref import ModelRef
from synthorg.settings.state import config_resolver_of
from tests.evals_spine._recording import (
    RECORDING_MODEL,
    RECORDING_PROVIDER,
    recording_company_config,
)

pytestmark = [
    pytest.mark.integration,
    # Every test here boots the real application, binds a socket and serves
    # HTTP, which is the slow capability by any reading of a 300s budget.
    pytest.mark.slow,
    pytest.mark.timeout(300),
]

_TTL_SECONDS = 600

#: What the deterministic scripted driver always opens its completion with.
_SCRIPTED_PREFIX = "Scripted deterministic completion"

#: Marker the injected mid-boot failure carries, so the test matches its own.
_BOOT_FAILURE = "injected boot failure"

#: The Cat-3 bootstrap secrets the host swaps for throwaway values while it runs.
_SECRET_VARS = (
    "SYNTHORG_JWT_SECRET",
    "SYNTHORG_PAGINATION_CURSOR_SECRET",
    "SYNTHORG_MASTER_KEY",
    "SYNTHORG_SETTINGS_KEY",
)


_COMPLETION_BODY: dict[str, object] = {
    "model": "ignored",
    "messages": [{"role": "user", "content": "hi"}],
}


def _local_mcp_url(host: RecordingGatewayHost) -> str:
    """Address the MCP endpoint over loopback rather than the container alias.

    Returns:
        The same mounted route, dialled the way this process can reach it.
    """
    return host.container_mcp_url.replace(DEFAULT_CONTAINER_HOST, "127.0.0.1")


def _config(tmp_path: Path, *, scratch: Path | None = None) -> RecordingHostConfig:
    """Build a loopback-bound host config rooted under *tmp_path*.

    Returns:
        The host config.
    """
    return RecordingHostConfig(
        company_config=recording_company_config(),
        scratch_dir=scratch if scratch is not None else tmp_path / "host",
        bind_host="127.0.0.1",
    )


def _secret_fingerprint() -> dict[str, str | None]:
    """Digest each bootstrap secret so a failed assertion never prints one.

    The property under test is that the host put back exactly what it found,
    which a digest proves; comparing the values themselves would write the
    operator's real Cat-3 secrets into the failure report.

    Returns:
        One digest per variable, or ``None`` where the variable is unset.
    """
    return {
        var: (
            None
            if (value := os.environ.get(var)) is None
            else hashlib.sha256(value.encode()).hexdigest()
        )
        for var in _SECRET_VARS
    }


async def _raise_boot_failure(*_args: object, **_kwargs: object) -> None:
    """Fail a boot step the way a full disk or a dead socket would.

    Raises:
        OSError: Always, standing in for whatever the real step could hit.
    """
    raise OSError(_BOOT_FAILURE)


def _bearer(host: RecordingGatewayHost) -> str:
    """Mint a run bearer from the host's own signer.

    Returns:
        The signed per-run token.
    """
    return mint_run_token(
        host.signer,
        execution_id=NotBlankStr("loop-ab-host-test"),
        agent_id=NotBlankStr("agent-1"),
        task_id=NotBlankStr("task-1"),
        ref=ModelRef(provider=RECORDING_PROVIDER, model_id=RECORDING_MODEL),
        ttl_seconds=_TTL_SECONDS,
    )


class TestSigner:
    async def test_a_bearer_minted_here_is_accepted_over_there(
        self, host: RecordingGatewayHost
    ) -> None:
        # The defect this host exists to fix: a token minted by any instance
        # other than the one the gateway verifies with is rejected, so the only
        # convincing assertion spends a locally minted bearer on the real route.
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{host.local_gateway_url}/chat/completions",
                headers={"Authorization": f"Bearer {_bearer(host)}"},
                json=_COMPLETION_BODY,
            )

        assert response.status_code == 200, response.text
        content = response.json()["choices"][0]["message"]["content"]
        # The scripted driver is deterministic, so asserting on the content it
        # is known to produce catches a hop that answered with something else
        # (a different model dispatched, a truncated or re-encoded body) where
        # a truthiness check would pass.
        assert content.startswith(_SCRIPTED_PREFIX), content

    async def test_a_malformed_bearer_is_refused(
        self, host: RecordingGatewayHost
    ) -> None:
        # The other half of the same claim: the route is not simply unguarded.
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{host.local_gateway_url}/chat/completions",
                headers={"Authorization": "Bearer not-a-token"},
                json=_COMPLETION_BODY,
            )

        assert response.status_code == 401, response.text

    async def test_a_bearer_from_another_signer_is_refused(
        self, host: RecordingGatewayHost
    ) -> None:
        # The actual defect is cross-INSTANCE, not malformed input: a token that
        # is correctly shaped and correctly signed, just by a different signer,
        # is what a recorder pointed at a separately running backend would send.
        foreign = mint_run_token(
            GatewaySigner.with_random_key(),
            execution_id=NotBlankStr("loop-ab-host-test"),
            agent_id=NotBlankStr("agent-1"),
            task_id=NotBlankStr("task-1"),
            ref=ModelRef(provider=RECORDING_PROVIDER, model_id=RECORDING_MODEL),
            ttl_seconds=_TTL_SECONDS,
        )
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{host.local_gateway_url}/chat/completions",
                headers={"Authorization": f"Bearer {foreign}"},
                json=_COMPLETION_BODY,
            )

        assert response.status_code == 401, response.text


class TestEndpointSettings:
    async def test_both_endpoints_resolve_to_the_bound_port(
        self, host: RecordingGatewayHost
    ) -> None:
        # The loop wiring reads these settings, not the host object, so a port
        # written anywhere else would leave the container dialling nothing.
        resolver = config_resolver_of(host.app_state)

        gateway = await resolver.get_str("providers", "gateway_base_url")
        mcp = await resolver.get_str("tools", "credentialed_mcp_base_url")

        assert gateway == host.container_gateway_url
        assert mcp == host.container_mcp_url
        assert f":{host.port}/" in gateway
        assert f":{host.port}/" in mcp

    async def test_container_urls_address_the_docker_host_alias(
        self, host: RecordingGatewayHost
    ) -> None:
        # The container joins the sidecar's network namespace, where loopback
        # is the sidecar's own; only the host-gateway alias reaches the recorder.
        assert host.container_gateway_url.startswith("http://host.docker.internal:")
        assert host.local_gateway_url.startswith("http://127.0.0.1:")


class TestCredentialedMcp:
    async def test_the_handshake_succeeds_but_grants_no_tools(
        self, host: RecordingGatewayHost
    ) -> None:
        # The SDK will not build an agent without an MCP endpoint, so the
        # surface has to answer. It must still hand the coding briefs nothing:
        # they need no credentialed tool, and the shipped empty capability
        # grant is what keeps the credentialed surface unreachable.
        headers = {"Authorization": f"Bearer {_bearer(host)}"}
        url = f"{_local_mcp_url(host)}/mcp"
        async with httpx.AsyncClient() as client:
            initialize = await client.post(
                url,
                headers=headers,
                json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            )
            listed = await client.post(
                url,
                headers=headers,
                json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
            )

        assert initialize.status_code == 200, initialize.text
        assert initialize.json()["result"]["protocolVersion"]
        assert listed.status_code == 200, listed.text
        assert listed.json()["result"]["tools"] == []


class TestLifecycle:
    async def test_scratch_directory_is_removed_on_exit(self, tmp_path: Path) -> None:
        scratch = tmp_path / "host"
        config = RecordingHostConfig(
            company_config=recording_company_config(),
            scratch_dir=scratch,
            bind_host="127.0.0.1",
        )

        async with RecordingGatewayHost(config) as started:
            assert scratch.is_dir()
            assert started.port > 0

        assert not scratch.exists()

    async def test_stop_is_idempotent(self, tmp_path: Path) -> None:
        # Teardown runs from ``__aexit__`` and again from ``start()``'s own
        # unwind, so the second call has to be a no-op rather than an error
        # that replaces whatever ended the run.
        started = RecordingGatewayHost(_config(tmp_path))
        await started.start()
        await started.stop()
        await started.stop()

        assert started.port == 0

    async def test_stop_before_start_is_a_no_op(self, tmp_path: Path) -> None:
        await RecordingGatewayHost(_config(tmp_path)).stop()

    async def test_a_server_that_overruns_teardown_is_cancelled(self) -> None:
        # ``asyncio.timeout`` cancels the coroutine that is waiting, never the
        # task it waits on. Without an explicit cancel the server keeps running
        # against a socket teardown closes moments later.
        async def _serve_forever() -> None:
            await asyncio.Event().wait()

        never_finishes = asyncio.create_task(_serve_forever())

        await _cancel_serving(never_finishes, port=0)

        assert never_finishes.cancelled()

    async def test_a_failed_start_restores_the_environment(
        self, tmp_path: Path, host: RecordingGatewayHost
    ) -> None:
        # A start that raises has already swapped the process environment, and
        # ``__aexit__`` never runs for an ``__aenter__`` that raised, so the
        # unwind is the only thing that puts the operator's secrets back.
        del host  # one host is already active, which is what makes this fail
        before = _secret_fingerprint()
        scratch = tmp_path / "second-host"

        with pytest.raises(HarnessHostAlreadyStartedError):
            async with RecordingGatewayHost(_config(tmp_path, scratch=scratch)):
                pass

        assert _secret_fingerprint() == before
        assert not scratch.exists()

    async def test_a_boot_that_fails_midway_releases_the_process_slot(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The slot and the swapped secrets are claimed before the fallible
        # steps, so a direct ``start()`` caller that never reaches the context
        # manager must still get them back, or the next host in the process is
        # refused and the operator's environment keeps this run's throwaways.
        before = _secret_fingerprint()
        failing = RecordingGatewayHost(_config(tmp_path, scratch=tmp_path / "doomed"))
        # Patched on the instance, so the recovery host below boots for real.
        monkeypatch.setattr(failing, "_seed_admin", _raise_boot_failure)

        with pytest.raises(OSError, match=_BOOT_FAILURE):
            await failing.start()

        assert _secret_fingerprint() == before
        assert not (tmp_path / "doomed").exists()
        # Proven by the next host booting at all: the slot is process-global.
        async with RecordingGatewayHost(_config(tmp_path)) as recovered:
            assert recovered.port > 0

    async def test_a_second_concurrent_host_is_refused(
        self, tmp_path: Path, host: RecordingGatewayHost
    ) -> None:
        # Two live hosts would each capture the other's throwaway secrets as
        # the values to restore, so whichever stopped first would leave the
        # survivor's environment holding secrets that no longer decrypt.
        del host
        with pytest.raises(HarnessHostAlreadyStartedError):
            await RecordingGatewayHost(_config(tmp_path)).start()

    async def test_signer_before_start_fails_loud(self, tmp_path: Path) -> None:
        # A host that never started has no signer, and silently returning one
        # built here would be exactly the second instance this fixes.
        config = RecordingHostConfig(
            company_config=recording_company_config(),
            scratch_dir=tmp_path / "host",
            bind_host="127.0.0.1",
        )

        with pytest.raises(HarnessGatewayUnavailableError):
            _ = RecordingGatewayHost(config).signer


class TestFirstRunSetup:
    async def test_the_setup_route_grants_nobody_admin(
        self, host: RecordingGatewayHost
    ) -> None:
        # ``/auth/setup`` is excluded from authentication on purpose so a real
        # deployment cannot lock its operator out, and it grants CEO plus OWNER
        # to the first caller while no CEO exists. The recorder boots a database
        # that has none and serves the whole application, so without a seeded
        # admin this route hands full control of a process holding the
        # operator's provider credentials to anyone who can reach the port.
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"http://127.0.0.1:{host.port}/api/v1/auth/setup",
                json={"username": "intruder", "password": "hunter2-hunter2"},
            )

        assert response.status_code == 409, response.text

    async def test_the_seeded_admin_is_the_only_ceo(
        self, host: RecordingGatewayHost
    ) -> None:
        persistence = persistence_of(host.app_state)

        assert await persistence.users.count_by_role(HumanRole.CEO) == 1

    async def test_seeding_a_database_that_already_has_a_ceo_is_a_no_op(
        self, host: RecordingGatewayHost, tmp_path: Path
    ) -> None:
        """The resume path: a killed run leaves its scratch database behind.

        ``stop`` removes it on a clean exit, so a populated scratch database
        only outlives a run that was KILLED, which is precisely the run
        somebody resumes. Seeding unconditionally made that boot die on
        ``UNIQUE constraint failed: users.role`` before the sweep could read
        the journal it was resuming from, and that journal is a recording
        already paid for.

        What closes the unauthenticated setup route is that a CEO EXISTS, not
        that this boot created one, so finding one is success rather than a
        collision to report.
        """
        persistence = persistence_of(host.app_state)
        second = RecordingGatewayHost(_config(tmp_path, scratch=tmp_path / "second"))

        await second._seed_admin(persistence)

        assert await persistence.users.count_by_role(HumanRole.CEO) == 1


class TestOpenHandsImage:
    async def test_image_override_reaches_the_setting(self, tmp_path: Path) -> None:
        # A maintainer records against a locally built image; the loop wiring
        # reads the setting, so the flag has to land there rather than on a
        # field only the recorder consults.
        config = RecordingHostConfig(
            company_config=recording_company_config(),
            scratch_dir=tmp_path / "host",
            bind_host="127.0.0.1",
            openhands_image="synthorg-openhands:local",
        )

        async with RecordingGatewayHost(config) as started:
            resolved = await config_resolver_of(started.app_state).get_str(
                "tools", "openhands_image"
            )

        assert resolved == "synthorg-openhands:local"
