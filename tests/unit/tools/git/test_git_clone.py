"""Unit and integration tests for GitCloneTool (clone + SSRF prevention)."""

from pathlib import Path

import pytest

import synthorg.tools._git_clone as git_clone_module
from synthorg.tools.base import ToolExecutionResult
from synthorg.tools.git_tools import GitCloneTool
from synthorg.tools.git_url_validator import (
    DnsValidationOk,
    GitCloneNetworkPolicy,
)

from .conftest import _run_git

# ── GitCloneTool ──────────────────────────────────────────────────


@pytest.mark.unit
class TestGitCloneTool:
    """Tests for git_clone."""

    async def test_clone_local_repo(
        self,
        git_repo: Path,
        workspace: Path,
        allow_local_clone: None,
    ) -> None:
        tool = GitCloneTool(workspace=workspace)
        result = await tool.execute(
            arguments={
                "url": str(git_repo),
                "directory": "cloned",
            },
        )
        assert not result.is_error
        assert (workspace / "cloned" / "README.md").exists()

    async def test_clone_with_depth(
        self,
        git_repo: Path,
        workspace: Path,
        allow_local_clone: None,
    ) -> None:
        tool = GitCloneTool(workspace=workspace)
        result = await tool.execute(
            arguments={
                "url": str(git_repo),
                "directory": "shallow",
                "depth": 1,
            },
        )
        assert not result.is_error

    async def test_clone_directory_outside_workspace(
        self,
        git_repo: Path,
        workspace: Path,
        allow_local_clone: None,
    ) -> None:
        tool = GitCloneTool(workspace=workspace)
        result = await tool.execute(
            arguments={
                "url": str(git_repo),
                "directory": "../../outside",
            },
        )
        assert result.is_error

    async def test_clone_invalid_url(self, clone_tool: GitCloneTool) -> None:
        result = await clone_tool.execute(
            arguments={"url": "not-a-real-url-at-all"},
        )
        assert result.is_error
        assert "Invalid clone URL" in result.content

    async def test_clone_with_branch(
        self,
        git_repo: Path,
        workspace: Path,
        allow_local_clone: None,
    ) -> None:
        _run_git(["branch", "test-branch"], git_repo)
        tool = GitCloneTool(workspace=workspace)
        result = await tool.execute(
            arguments={
                "url": str(git_repo),
                "directory": "branch-clone",
                "branch": "test-branch",
            },
        )
        assert not result.is_error


# ── Security: SSRF prevention in clone ────────────────────────────


@pytest.mark.unit
class TestGitCloneToolSsrf:
    """SSRF prevention integration tests for git_clone."""

    async def test_clone_ssrf_loopback_blocked(
        self,
        workspace: Path,
    ) -> None:
        """Clone to loopback IP must be blocked."""
        tool = GitCloneTool(workspace=workspace)
        result = await tool.execute(
            arguments={"url": "https://127.0.0.1/repo.git"},
        )
        assert result.is_error
        assert "blocked" in result.content.lower()

    async def test_clone_ssrf_private_ip_blocked(
        self,
        workspace: Path,
    ) -> None:
        """Clone to private network IP must be blocked."""
        tool = GitCloneTool(workspace=workspace)
        result = await tool.execute(
            arguments={"url": "https://10.0.0.5/internal.git"},
        )
        assert result.is_error
        assert "blocked" in result.content.lower()

    async def test_clone_ssrf_allowlisted_host(
        self,
        workspace: Path,
    ) -> None:
        """Allowlisted host bypasses SSRF check."""
        policy = GitCloneNetworkPolicy(
            hostname_allowlist=("internal-git.example.com",),
        )
        tool = GitCloneTool(workspace=workspace, network_policy=policy)
        result = await tool.execute(
            arguments={
                "url": "https://internal-git.example.com/repo.git",
            },
        )
        # SSRF check passes (allowlisted); clone fails for other
        # reasons (host doesn't exist) -- but NOT an SSRF error.
        assert "blocked" not in result.content.lower()
        assert "ssrf" not in result.content.lower()

    async def test_clone_file_scheme_blocked(
        self,
        clone_tool: GitCloneTool,
    ) -> None:
        """Scheme rejection wiring: file:// blocked end-to-end."""
        result = await clone_tool.execute(
            arguments={"url": "file:///etc"},
        )
        assert result.is_error
        assert "Invalid clone URL" in result.content


# ── TOCTOU DNS rebinding mitigation in clone ─────────────────────


def _mock_validate(
    validation: DnsValidationOk,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Patch validate_clone_url_host to return *validation*."""

    async def _inner(url: str, policy: GitCloneNetworkPolicy) -> DnsValidationOk:
        return validation

    monkeypatch.setattr(git_clone_module, "validate_clone_url_host", _inner)


def _mock_run_git(
    captured: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Patch GitCloneTool._run_git to capture args."""

    async def _inner(
        self_: GitCloneTool,
        args: list[str],
        *,
        cwd: Path | None = None,
        deadline: float = 30.0,
    ) -> ToolExecutionResult:
        captured.extend(args)
        return ToolExecutionResult(content="ok")

    monkeypatch.setattr(GitCloneTool, "_run_git", _inner)


@pytest.mark.unit
class TestGitCloneToolToctou:
    """TOCTOU DNS rebinding mitigation wiring tests."""

    async def test_https_injects_curlopt_resolve(
        self,
        workspace: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """HTTPS clone prepends -c http.curloptResolve to args."""
        _mock_validate(
            DnsValidationOk(
                hostname="example.com",
                port=443,
                resolved_ips=("93.184.216.34",),
                is_https=True,
            ),
            monkeypatch,
        )
        captured: list[str] = []
        _mock_run_git(captured, monkeypatch)

        tool = GitCloneTool(workspace=workspace)
        result = await tool.execute(
            arguments={"url": "https://example.com/repo.git"},
        )
        assert not result.is_error
        assert captured[0] == "-c"
        assert captured[1] == "http.curloptResolve=example.com:443:93.184.216.34"
        assert captured[2] == "clone"

    async def test_https_custom_port_in_resolve(
        self,
        workspace: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Custom HTTPS port is reflected in curloptResolve."""
        _mock_validate(
            DnsValidationOk(
                hostname="example.com",
                port=8443,
                resolved_ips=("1.2.3.4",),
                is_https=True,
            ),
            monkeypatch,
        )
        captured: list[str] = []
        _mock_run_git(captured, monkeypatch)

        tool = GitCloneTool(workspace=workspace)
        await tool.execute(
            arguments={"url": "https://example.com:8443/repo.git"},
        )
        assert "http.curloptResolve=example.com:8443:1.2.3.4" in captured[1]

    async def test_ssh_double_resolve_passes(
        self,
        workspace: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """SSH clone with consistent DNS proceeds normally."""
        _mock_validate(
            DnsValidationOk(
                hostname="example.com",
                resolved_ips=("93.184.216.34",),
                is_https=False,
            ),
            monkeypatch,
        )

        verify_calls: list[tuple[str, frozenset[str]]] = []

        async def mock_verify(
            hostname: str,
            expected_ips: frozenset[str],
            dns_timeout: float,
        ) -> str | None:
            verify_calls.append((hostname, expected_ips))
            return None

        monkeypatch.setattr(git_clone_module, "verify_dns_consistency", mock_verify)
        captured: list[str] = []
        _mock_run_git(captured, monkeypatch)

        tool = GitCloneTool(workspace=workspace)
        result = await tool.execute(
            arguments={"url": "ssh://git@example.com/repo.git"},
        )
        assert not result.is_error
        assert captured[0] == "clone"
        assert len(verify_calls) == 1
        assert verify_calls[0][0] == "example.com"
        assert verify_calls[0][1] == frozenset({"93.184.216.34"})

    async def test_ssh_double_resolve_detects_rebinding(
        self,
        workspace: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """SSH clone blocked when double-resolve detects rebinding."""
        _mock_validate(
            DnsValidationOk(
                hostname="evil.example.com",
                resolved_ips=("93.184.216.34",),
                is_https=False,
            ),
            monkeypatch,
        )

        async def mock_verify(
            hostname: str,
            expected_ips: frozenset[str],
            dns_timeout: float,
        ) -> str | None:
            return "DNS rebinding detected for 'evil.example.com'"

        monkeypatch.setattr(git_clone_module, "verify_dns_consistency", mock_verify)

        tool = GitCloneTool(workspace=workspace)
        result = await tool.execute(
            arguments={"url": "ssh://git@evil.example.com/repo.git"},
        )
        assert result.is_error
        assert "rebinding" in result.content.lower()

    async def test_literal_ip_skips_pinning(
        self,
        workspace: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Literal IP URL skips TOCTOU mitigation (no DNS to rebind)."""
        _mock_validate(
            DnsValidationOk(
                hostname="93.184.216.34",
                port=443,
                resolved_ips=(),
                is_https=True,
            ),
            monkeypatch,
        )
        captured: list[str] = []
        _mock_run_git(captured, monkeypatch)

        tool = GitCloneTool(workspace=workspace)
        result = await tool.execute(
            arguments={"url": "https://93.184.216.34/repo.git"},
        )
        assert not result.is_error
        assert captured[0] == "clone"
        assert "-c" not in captured

    async def test_mitigation_disabled_skips_pinning(
        self,
        workspace: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Disabled mitigation skips DNS pinning even for HTTPS."""
        received_policies: list[GitCloneNetworkPolicy] = []

        async def _validate(
            url: str,
            policy: GitCloneNetworkPolicy,
        ) -> DnsValidationOk:
            received_policies.append(policy)
            return DnsValidationOk(
                hostname="example.com",
                port=443,
                resolved_ips=(),
                is_https=True,
            )

        monkeypatch.setattr(git_clone_module, "validate_clone_url_host", _validate)
        captured: list[str] = []
        _mock_run_git(captured, monkeypatch)

        policy = GitCloneNetworkPolicy(dns_rebinding_mitigation=False)
        tool = GitCloneTool(workspace=workspace, network_policy=policy)
        result = await tool.execute(
            arguments={"url": "https://example.com/repo.git"},
        )
        assert not result.is_error
        assert captured[0] == "clone"
        assert "-c" not in captured
        assert len(received_policies) == 1
        assert received_policies[0].dns_rebinding_mitigation is False

    async def test_scp_double_resolve_passes(
        self,
        workspace: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """SCP-like URL clone with consistent DNS proceeds normally."""
        _mock_validate(
            DnsValidationOk(
                hostname="example.com",
                resolved_ips=("93.184.216.34",),
                is_https=False,
            ),
            monkeypatch,
        )

        verify_calls: list[tuple[str, frozenset[str], float]] = []

        async def mock_verify(
            hostname: str,
            expected_ips: frozenset[str],
            dns_timeout: float,
        ) -> str | None:
            verify_calls.append((hostname, expected_ips, dns_timeout))
            return None

        monkeypatch.setattr(git_clone_module, "verify_dns_consistency", mock_verify)
        captured: list[str] = []
        _mock_run_git(captured, monkeypatch)

        tool = GitCloneTool(workspace=workspace)
        result = await tool.execute(
            arguments={"url": "git@example.com:repo.git"},
        )
        assert not result.is_error
        assert captured[0] == "clone"
        assert "-c" not in captured
        assert len(verify_calls) == 1
        assert verify_calls[0][0] == "example.com"
        assert verify_calls[0][1] == frozenset({"93.184.216.34"})
