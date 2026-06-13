"""Smoke tests for the MCP cockpit steering domain args module.

The steering tools route external ``arguments`` through these models at the
invoker, so a blank id or malformed payload is rejected at the MCP boundary
rather than reaching the service write path.
"""

import pytest
from pydantic import ValidationError

from synthorg.engine.intervention.enums import InterventionKind
from synthorg.engine.intervention.models import SupersedeMode
from synthorg.meta.mcp.domains._cockpit_args import (
    FramesArgs,
    InterveneArgs,
    LiveActivityArgs,
    SeekArgs,
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


class TestLiveActivityArgs:
    @pytest.mark.unit
    def test_accepts_empty(self) -> None:
        LiveActivityArgs.model_validate({})

    @pytest.mark.unit
    def test_rejects_unexpected_fields(self) -> None:
        with pytest.raises(ValidationError):
            LiveActivityArgs.model_validate({"execution_id": "ex-1"})


class TestFramesArgs:
    @pytest.mark.unit
    def test_defaults_pagination(self) -> None:
        args = FramesArgs(execution_id="ex-1")
        assert args.offset == 0
        assert args.limit == 50

    @pytest.mark.unit
    def test_requires_execution_id(self) -> None:
        with pytest.raises(ValidationError):
            FramesArgs.model_validate({})

    @pytest.mark.unit
    def test_rejects_blank_execution_id(self) -> None:
        with pytest.raises(ValidationError):
            FramesArgs.model_validate({"execution_id": ""})


class TestSeekArgs:
    @pytest.mark.unit
    def test_minimal_valid(self) -> None:
        args = SeekArgs(execution_id="ex-1", turn_index=1)
        assert args.turn_index == 1

    @pytest.mark.unit
    def test_rejects_turn_index_below_one(self) -> None:
        with pytest.raises(ValidationError):
            SeekArgs.model_validate({"execution_id": "ex-1", "turn_index": 0})


class TestInterveneArgs:
    @pytest.mark.unit
    def test_minimal_valid(self) -> None:
        InterveneArgs(task_id="task-1", confirm=True, reason="operator pause")

    @pytest.mark.unit
    def test_requires_admin_guardrails(self) -> None:
        with pytest.raises(ValidationError):
            InterveneArgs.model_validate({"task_id": "task-1"})

    @pytest.mark.unit
    def test_rejects_blank_task_id(self) -> None:
        with pytest.raises(ValidationError):
            InterveneArgs.model_validate(
                {"task_id": "", "confirm": True, "reason": "r"},
            )
