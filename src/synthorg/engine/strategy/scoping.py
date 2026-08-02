# module-kind: code
"""The one scope-matching rule for every ``ScopeKind``-scoped directive.

Active principles, house-style directives and ask-policy directives all answer
the same question: does this org-authored text apply to the agent whose prompt
is being built? Each carries a ``ScopeKind`` plus a ``scope`` string, and each
provider filters its snapshot with the identical predicate. It lives here, next
to the ``ScopeKind`` vocabulary itself, so a fourth scoped directive inherits
the rule rather than restating it.

Callers normalise the agent's role and department once per read and pass the
keys in, because a provider filters a whole snapshot against a single agent.
"""

from synthorg.core.normalization import normalize_identifier
from synthorg.engine.strategy.active_principle import ScopeKind


def scope_matches(
    *,
    scope_kind: ScopeKind,
    scope: str,
    role_key: str | None,
    dept_key: str | None,
) -> bool:
    """Return whether a scoped directive applies to an agent.

    Args:
        scope_kind: Whether ``scope`` names the whole org, a role, or a dept.
        scope: The directive's scope value.
        role_key: The agent's normalised role, or ``None`` when it has none.
        dept_key: The agent's normalised department, or ``None``.

    Returns:
        ``True`` for an ``ALL``-scoped directive, a ``ROLE``-scoped directive
        matching ``role_key``, or a ``DEPARTMENT``-scoped directive matching
        ``dept_key``.
    """
    if scope_kind is ScopeKind.ALL:
        return True
    scope_key = normalize_identifier(scope)
    if scope_kind is ScopeKind.ROLE:
        return role_key is not None and scope_key == role_key
    return dept_key is not None and scope_key == dept_key


__all__ = ["scope_matches"]
