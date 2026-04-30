"""Typed argument model for the ontology lookup tool.

Tools wired to consume this model:

* :class:`~synthorg.ontology.injection.tool.LookupEntityTool`
  -> :class:`LookupEntityArgs`
"""

from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from synthorg.core.types import NotBlankStr  # noqa: TC001 -- Pydantic field type


class LookupEntityArgs(BaseModel):
    """Args for ``lookup_entity``.

    Exactly one of ``name`` (exact lookup) or ``query`` (free-text
    search) must be provided.  Both-or-neither cases are rejected at
    the validation boundary so the tool body never has to disambiguate.

    The ``json_schema_extra`` ``oneOf`` clause projects the same
    invariant into the published JSON Schema; without it the schema
    would advertise both fields as independently optional, and MCP
    clients / LLMs could happily generate both-or-neither payloads
    that only fail at dispatch.
    """

    model_config = ConfigDict(
        frozen=True,
        allow_inf_nan=False,
        extra="forbid",
        # ``oneOf`` enforces "exactly one" at the schema level: the
        # caller must supply one branch and not both.  Combined with
        # ``required`` on each branch this matches the runtime
        # ``_exactly_one_mode`` validator below.
        json_schema_extra={
            "oneOf": [
                {"required": ["name"]},
                {"required": ["query"]},
            ],
        },
    )

    name: NotBlankStr | None = Field(
        default=None,
        description="Exact entity name to retrieve",
    )
    query: NotBlankStr | None = Field(
        default=None,
        description="Free-text search query",
    )

    @model_validator(mode="after")
    def _exactly_one_mode(self) -> Self:
        if (self.name is None) == (self.query is None):
            msg = "Provide exactly one of 'name' or 'query', not both or neither"
            raise ValueError(msg)
        return self


__all__ = ["LookupEntityArgs"]
