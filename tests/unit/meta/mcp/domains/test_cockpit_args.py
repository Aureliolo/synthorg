"""Smoke tests for the MCP cockpit steering domain args module.

The steering tools route external ``arguments`` through these models at the
invoker, so a blank id or malformed payload is rejected at the MCP boundary
rather than reaching the service write path.
"""

import pytest
from pydantic import ValidationError

from synthorg.core.enums import InterventionKind
from synthorg.engine.intervention.models import SupersedeMode
from synthorg.meta.mcp.domains._cockpit_args import (
    SteerArgs,
    SteerListArgs,
    SteerSupersedeArgs,
)


class TestSteerArgs:
    @pytest.mark.unit
    def test_minimal_valid_defaults(self) -> None:
        args = SteerArgs(
            project_id="proj-1",
            kind=InterventionKind.HINT,
            text="prefer the existing util",
            confirm=True,
            reason="operator hint",
        )
        assert args.supersede_mode is SupersedeMode.NONE
        assert args.narrow_task_ids == ()
        assert args.supersede_task_ids == ()

    @pytest.mark.unit
    def test_blank_project_id_rejected(self) -> None:
        with pytest.raises(ValidationError):
            SteerArgs.model_validate(
                {
                    "project_id": "",
                    "kind": "hint",
                    "text": "x",
                    "confirm": True,
                    "reason": "r",
                },
            )

    @pytest.mark.unit
    def test_blank_text_rejected(self) -> None:
        with pytest.raises(ValidationError):
            SteerArgs.model_validate(
                {
                    "project_id": "proj-1",
                    "kind": "hint",
                    "text": "   ",
                    "confirm": True,
                    "reason": "r",
                },
            )

    @pytest.mark.unit
    def test_unknown_kind_rejected(self) -> None:
        with pytest.raises(ValidationError):
            SteerArgs.model_validate(
                {
                    "project_id": "proj-1",
                    "kind": "frobnicate",
                    "text": "x",
                    "confirm": True,
                    "reason": "r",
                },
            )

    @pytest.mark.unit
    def test_admin_guardrails_required(self) -> None:
        with pytest.raises(ValidationError):
            SteerArgs.model_validate(
                {"project_id": "proj-1", "kind": "hint", "text": "x"},
            )


class TestSteerSupersedeArgs:
    @pytest.mark.unit
    def test_minimal_valid(self) -> None:
        SteerSupersedeArgs(
            project_id="proj-1",
            directive_id="d1",
            task_ids=("t1",),
            confirm=True,
            reason="operator supersede",
        )

    @pytest.mark.unit
    def test_blank_directive_id_rejected(self) -> None:
        with pytest.raises(ValidationError):
            SteerSupersedeArgs.model_validate(
                {
                    "project_id": "proj-1",
                    "directive_id": "",
                    "task_ids": ("t1",),
                    "confirm": True,
                    "reason": "r",
                },
            )


class TestSteerListArgs:
    @pytest.mark.unit
    def test_requires_non_blank_project_id(self) -> None:
        SteerListArgs(project_id="proj-1")
        with pytest.raises(ValidationError):
            SteerListArgs.model_validate({"project_id": ""})
