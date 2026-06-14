"""Validators raise ``ValueError`` so Pydantic wraps them.

Pydantic v2 only wraps ``ValueError`` / ``AssertionError`` raised inside
a validator into a ``ValidationError``; a bare ``TypeError`` escapes the
``model_validate`` / constructor call uncaught. The convention rollout
converted several ``isinstance``-guard ``raise TypeError`` sites to
``raise ValueError``. These tests pin that bad input surfaces as a
``ValidationError`` (not a raw ``TypeError``).
"""

import pytest
from pydantic import ValidationError

from synthorg.api.dto_workflow import WorkflowIODeclarationRequest
from synthorg.budget.call_category import LLMCallCategory
from synthorg.budget.currency import DEFAULT_CURRENCY, CurrencyCode
from synthorg.engine.workflow.definition import WorkflowIODeclaration
from synthorg.engine.workflow.enums import WorkflowValueType
from synthorg.providers.cost_recording import CostRecordingContext

pytestmark = pytest.mark.unit


def test_cost_recording_context_bad_tracker_is_validation_error() -> None:
    with pytest.raises(ValidationError):
        CostRecordingContext(
            cost_tracker=object(),  # type: ignore[arg-type]
            agent_id="agent-1",
            task_id="task-1",
            call_category=LLMCallCategory.PRODUCTIVE,
            currency=CurrencyCode(DEFAULT_CURRENCY),
        )


def test_workflow_io_declaration_bad_default_is_validation_error() -> None:
    with pytest.raises(ValidationError):
        WorkflowIODeclaration(
            name="count",
            type=WorkflowValueType.INTEGER,
            required=False,
            default="not-an-int",
        )


def test_workflow_io_declaration_request_bad_default_is_validation_error() -> None:
    with pytest.raises(ValidationError):
        WorkflowIODeclarationRequest(
            name="count",
            type=WorkflowValueType.INTEGER,
            required=False,
            default="not-an-int",
        )


def test_workflow_io_declaration_valid_default_constructs() -> None:
    decl = WorkflowIODeclaration(
        name="count",
        type=WorkflowValueType.INTEGER,
        required=False,
        default=3,
    )
    assert decl.default == 3
