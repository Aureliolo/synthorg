"""Typed args models for the project-charter MCP domain.

Mirrors the ``_simple_args`` pattern: each model is frozen +
``extra="forbid"`` and validated by the invoker before the handler
runs. The ``CharterStatus`` wire enum is a ``Literal`` so the schema
list cannot drift from the args surface.
"""

from typing import Literal

from pydantic import Field

from synthorg.core.types import NotBlankStr  # noqa: TC001 -- runtime by Pydantic
from synthorg.meta.mcp.domains._common_args import (
    AdminGuardrailFields,
    PaginationFields,
    _ArgsBase,
)

CharterStatusLiteral = Literal["drafted", "approved", "cancelled"]


class CharterInterviewArgs(_ArgsBase):
    """Args for ``charter.interview`` (one elicitation turn)."""

    message: NotBlankStr = Field(description="The human's message this turn")
    conversation_id: NotBlankStr | None = Field(
        default=None,
        description="Existing interview to continue, or null to open one",
    )
    project: NotBlankStr | None = Field(
        default=None,
        description="Existing project id to target (else a new one is proposed)",
    )


class CharterListArgs(PaginationFields):
    """Args for ``charter.list``."""

    status: CharterStatusLiteral | None = Field(
        default=None,
        description="Filter by charter status",
    )
    project_id: NotBlankStr | None = Field(
        default=None,
        description="Filter by bound project id",
    )
    created_by: NotBlankStr | None = Field(
        default=None,
        description="Filter by interview owner",
    )


class CharterGetArgs(_ArgsBase):
    """Args for ``charter.get``."""

    charter_id: NotBlankStr = Field(description="Charter id")


class CharterApproveArgs(AdminGuardrailFields):
    """Args for ``charter.approve`` (admin; spends budget + runs the spine)."""

    charter_id: NotBlankStr = Field(description="Charter id")


class CharterCancelArgs(_ArgsBase):
    """Args for ``charter.cancel``."""

    charter_id: NotBlankStr = Field(description="Charter id")


__all__ = [
    "CharterApproveArgs",
    "CharterCancelArgs",
    "CharterGetArgs",
    "CharterInterviewArgs",
    "CharterListArgs",
    "CharterStatusLiteral",
]
