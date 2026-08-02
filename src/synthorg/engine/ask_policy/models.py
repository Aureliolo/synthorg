# module-kind: declarative
"""Operator-authored ask-policy directives and the resolved config.

The standing directive lives in :mod:`directives` because it must be total. What
an operator adds on top is data, so it is modelled here: an :class:`AskDirective`
names a choice the organisation always wants escalated (a schema change, a public
API break, spend above a threshold), scoped org-wide or to a role or department
with the same ``ScopeKind`` vocabulary the strategy and house-style layers use.
"""

from typing import Final, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from synthorg.core.types import NotBlankStr
from synthorg.engine.strategy.active_principle import ALL_SCOPE, ScopeKind

_MAX_DIRECTIVE_LEN: Final[int] = 2000


class AskDirective(BaseModel):
    """One operator-authored "when to ask" directive.

    Attributes:
        id: Unique directive identifier.
        text: The directive text rendered below the standing directive.
        scope: ``"all"`` or a role / department name (per ``scope_kind``).
        scope_kind: Whether ``scope`` names the whole org, a role, or a dept.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    id: NotBlankStr = Field(description="Unique directive identifier")
    text: NotBlankStr = Field(
        max_length=_MAX_DIRECTIVE_LEN,
        description="Directive text injected into prompts",
    )
    scope: NotBlankStr = Field(default=ALL_SCOPE, description="'all' or role/dept name")
    scope_kind: ScopeKind = Field(
        default=ScopeKind.ALL,
        description="Whether scope targets the org, a role, or a department",
    )

    @model_validator(mode="after")
    def _validate_scope_consistency(self) -> Self:
        """Reject a ``scope`` / ``scope_kind`` pairing that cannot match.

        Returns:
            The validated instance.

        Raises:
            ValueError: When ``ALL`` is not paired with the ``all`` sentinel.
        """
        is_all_kind = self.scope_kind is ScopeKind.ALL
        is_all_scope = self.scope == ALL_SCOPE
        if is_all_kind != is_all_scope:
            msg = (
                f"scope_kind={self.scope_kind.value!r} and scope={self.scope!r} "
                "are inconsistent: ALL requires scope='all' and vice versa"
            )
            raise ValueError(msg)
        return self


class AskPolicyConfig(BaseModel):
    """The resolved ask-policy configuration for the current process.

    Attributes:
        enabled: Whether the standing directive is injected at all.
        extra_directives: Operator-authored directives appended below it.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    enabled: bool = Field(
        default=True,
        description="Whether the standing ask directive is injected",
    )
    extra_directives: tuple[AskDirective, ...] = Field(
        default=(),
        description="Operator-authored directives appended to the standing one",
    )


__all__ = ["AskDirective", "AskPolicyConfig"]
