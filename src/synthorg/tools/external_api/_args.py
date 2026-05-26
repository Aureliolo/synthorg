"""Typed arguments for the governed external-access tool."""

from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from synthorg.core.types import NotBlankStr  # noqa: TC001 -- Pydantic field type

# HTTP methods the tool accepts. Writes route to approval (per the
# sensitive-or-write gating rule); reads do not.
_READ_METHODS: frozenset[str] = frozenset({"GET", "HEAD"})
_WRITE_METHODS: frozenset[str] = frozenset({"POST", "PUT", "PATCH", "DELETE"})
_ALLOWED_METHODS: frozenset[str] = _READ_METHODS | _WRITE_METHODS
# Methods that may carry a request body.
_BODY_METHODS: frozenset[str] = frozenset({"POST", "PUT", "PATCH"})


class ExternalApiArgs(BaseModel):
    """Arguments for the ``external_api`` tool.

    The agent always names a catalog ``connection`` (which supplies the
    base URL, credentials, egress hosts, and the sensitivity flag) and
    targets a resource either by a relative ``path`` (joined to the
    connection's base URL) or an absolute ``url`` (validated to resolve
    within the connection's allowed hosts).
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    connection: NotBlankStr = Field(
        description="Name of the pre-configured connection from the catalog.",
    )
    method: str = Field(
        default="GET",
        description="HTTP method: GET, HEAD, POST, PUT, PATCH, or DELETE.",
    )
    path: str = Field(
        default="",
        description=(
            "Relative path joined to the connection's base URL. Provide"
            " exactly one of path or url."
        ),
    )
    url: str = Field(
        default="",
        description=(
            "Absolute URL. Must resolve to a host within the connection's"
            " allowed hosts. Provide exactly one of path or url."
        ),
    )
    headers: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "Additional request headers. The connection's credentials are"
            " injected automatically and must not be supplied here."
        ),
    )
    body: str | None = Field(
        default=None,
        description="Request body. Permitted only for POST, PUT, and PATCH.",
    )
    approval_id: NotBlankStr | None = Field(
        default=None,
        description=(
            "Identifier of a granted approval, supplied when re-issuing a"
            " sensitive call after human approval. Optional: the call is"
            " matched to its approval by content signature regardless."
        ),
    )

    @model_validator(mode="after")
    def _normalise_and_validate(self) -> Self:
        """Uppercase the method and enforce cross-field invariants.

        Returns:
            Result of type ``Self``.

        Raises:
            ValueError: If an argument fails domain validation.
        """
        normalised = self.method.strip().upper()
        if normalised not in _ALLOWED_METHODS:
            allowed = ", ".join(sorted(_ALLOWED_METHODS))
            msg = f"method must be one of {allowed}; got {self.method!r}"
            raise ValueError(msg)
        object.__setattr__(self, "method", normalised)

        has_path = bool(self.path.strip())
        has_url = bool(self.url.strip())
        if has_path == has_url:
            msg = "provide exactly one of path or url"
            raise ValueError(msg)

        if self.body is not None and normalised not in _BODY_METHODS:
            allowed_body = ", ".join(sorted(_BODY_METHODS))
            msg = f"body is only permitted for {allowed_body}; got {normalised}"
            raise ValueError(msg)

        return self

    @property
    def is_write(self) -> bool:
        """Whether the method mutates remote state (routes to approval)."""
        return self.method in _WRITE_METHODS
