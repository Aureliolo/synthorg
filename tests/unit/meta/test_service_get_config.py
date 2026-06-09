"""Tests for SelfImprovementService.get_config()."""

from typing import cast
from unittest.mock import AsyncMock

import pytest

from synthorg.approval.protocol import ApprovalStoreProtocol
from synthorg.core.types import NotBlankStr
from synthorg.meta.config import CodeModificationConfig, SelfImprovementConfig
from synthorg.meta.service import SelfImprovementService


def _service(*, code_token: str | None) -> SelfImprovementService:
    code_cfg = CodeModificationConfig(
        github_token=NotBlankStr(code_token) if code_token else None,
        github_repo=NotBlankStr("Aureliolo/synthorg") if code_token else None,
    )
    cfg = SelfImprovementConfig(
        enabled=False,  # avoid approval-store gate
        code_modification_enabled=False,
        code_modification=code_cfg,
    )
    return SelfImprovementService(
        config=cfg,
        approval_store=AsyncMock(spec=ApprovalStoreProtocol),
    )


class TestGetConfig:
    @pytest.mark.unit
    def test_returns_dict_dump(self) -> None:
        service = _service(code_token=None)
        dump = service.get_config()
        assert isinstance(dump, dict)
        assert dump["enabled"] is False
        assert "schedule" in dump
        assert "regression" in dump

    @pytest.mark.unit
    def test_redacts_github_token(self) -> None:
        service = _service(code_token="ghp_super_secret_token_xyz")
        dump = service.get_config()
        assert (
            cast("dict[str, object]", dump["code_modification"])["github_token"]
            == "***redacted***"
        )

    @pytest.mark.unit
    def test_no_redaction_when_secret_unset(self) -> None:
        service = _service(code_token=None)
        dump = service.get_config()
        assert (
            cast("dict[str, object]", dump["code_modification"])["github_token"] is None
        )

    @pytest.mark.unit
    def test_does_not_mutate_internal_config(self) -> None:
        service = _service(code_token="ghp_secret")
        dump_a = service.get_config()
        dump_b = service.get_config()
        # Two calls produce equivalent (and independent) dumps.
        assert dump_a == dump_b
        assert dump_a is not dump_b
        # The internal config still has the real token (it's frozen
        # Pydantic; this guards against accidental in-place mutation).
        assert service._config.code_modification.github_token == "ghp_secret"
