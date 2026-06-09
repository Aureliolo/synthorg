"""Dry-run validators for architecture proposals.

Per-operation validation of each ``ArchitectureChange`` against a
read-only registry view, plus the in-proposal ``_PendingChanges``
accumulator that tracks scheduled creates / removes so dangling-ref
pairs *within* the same proposal are caught. Pure functions returning
lists of human-readable error strings; no state is mutated outside the
supplied ``_PendingChanges``.
"""

from collections.abc import Sequence
from typing import TYPE_CHECKING, Final

from synthorg.meta.appliers._validation import validate_payload_keys
from synthorg.meta.models import ArchitectureChange

if TYPE_CHECKING:
    from synthorg.meta.appliers.architecture_applier import ArchitectureApplierContext

_OP_CREATE_ROLE: Final[str] = "create_role"
_OP_CREATE_DEPARTMENT: Final[str] = "create_department"
_OP_MODIFY_WORKFLOW: Final[str] = "modify_workflow"
_OP_REMOVE_ROLE: Final[str] = "remove_role"
_OP_REMOVE_DEPARTMENT: Final[str] = "remove_department"
_SUPPORTED_OPS: Final[frozenset[str]] = frozenset(
    {
        _OP_CREATE_ROLE,
        _OP_CREATE_DEPARTMENT,
        _OP_MODIFY_WORKFLOW,
        _OP_REMOVE_ROLE,
        _OP_REMOVE_DEPARTMENT,
    }
)

_CREATE_ROLE_REQUIRED: Final[frozenset[str]] = frozenset({"description"})
_CREATE_ROLE_ALLOWED: Final[frozenset[str]] = frozenset(
    {
        "description",
        "department",
        "required_skills",
        "authority_level",
        "tool_access",
    }
)
_CREATE_DEPT_REQUIRED: Final[frozenset[str]] = frozenset()
_CREATE_DEPT_ALLOWED: Final[frozenset[str]] = frozenset({"head", "policies"})

# Value-level length caps applied to free-text payload fields to bound
# memory usage and mitigate stored-XSS risk if a downstream UI renders
# these values.  The limits are deliberately generous -- they only catch
# obvious abuse, not legitimate edge cases.
_MAX_DESCRIPTION_CHARS: Final[int] = 2_000
_MAX_ROLE_NAME_CHARS: Final[int] = 80
_MAX_SKILL_NAME_CHARS: Final[int] = 80
_MAX_SKILLS_PER_ROLE: Final[int] = 100
_MAX_TOOL_NAME_CHARS: Final[int] = 80
_MAX_TOOLS_PER_ROLE: Final[int] = 100
_MAX_POLICIES_PER_DEPT: Final[int] = 100
_MAX_POLICY_CHARS: Final[int] = 500

# Authority levels are free text for now (operators pick their own
# taxonomy).  Cap length + require non-blank so the field at least
# rejects obvious junk.
_MAX_AUTHORITY_LEVEL_CHARS: Final[int] = 60


class _PendingChanges:
    """In-proposal mutable accumulator for scheduled creates / removes.

    Tracks in-flight references with provenance so the validator
    catches dangling-ref pairs *within* the same proposal -- e.g. a
    ``remove_department`` that would leave behind a ``create_role``
    pointing at it, or a ``remove_role`` that would leave behind a
    ``create_department`` with that role as its head.

    References are stored as ``dict[str, set[str]]`` maps keyed by the
    *referenced* id, with values holding the set of *referencing*
    ids (the creates that introduced the reference).  When a
    reference-introducing create is itself cancelled by a later
    remove in the same proposal, the referencing id is dropped from
    the value set so the downstream ``in_use`` check sees the
    reference has actually gone away.

    ``has_*`` helpers encapsulate the "is this referenced?" check so
    call sites can't accidentally inspect an empty value set that
    has not been garbage-collected yet.
    """

    __slots__ = (
        "new_departments",
        "new_roles",
        "pending_department_refs",
        "pending_role_refs",
        "removed_departments",
        "removed_roles",
    )

    def __init__(self) -> None:
        self.new_roles: set[str] = set()
        self.removed_roles: set[str] = set()
        self.new_departments: set[str] = set()
        self.removed_departments: set[str] = set()
        # dept_name -> {role_names that reference it}
        self.pending_department_refs: dict[str, set[str]] = {}
        # role_name -> {dept_names that reference it as head}
        self.pending_role_refs: dict[str, set[str]] = {}

    # -- Reference registration (create_* paths) ----------------

    def add_department_ref(self, *, dept: str, from_role: str) -> None:
        """Record that ``from_role`` references ``dept``."""
        self.pending_department_refs.setdefault(dept, set()).add(from_role)

    def add_role_ref(self, *, role: str, from_department: str) -> None:
        """Record that ``from_department`` references ``role`` as head."""
        self.pending_role_refs.setdefault(role, set()).add(from_department)

    # -- Reference removal (remove_* paths) ---------------------

    def drop_refs_from_role(self, role: str) -> None:
        """Drop every dept ref that was introduced by creating ``role``."""
        _prune_provenance(self.pending_department_refs, role)

    def drop_refs_from_department(self, department: str) -> None:
        """Drop every role ref that was introduced by creating ``department``."""
        _prune_provenance(self.pending_role_refs, department)

    # -- In-use queries -----------------------------------------

    def has_department_refs(self, dept: str) -> bool:
        """Return True when any *still-live* create_role points at ``dept``.

        Returns:
            ``True`` or ``False`` reflecting the condition.
        """
        return bool(self.pending_department_refs.get(dept))

    def has_role_refs(self, role: str) -> bool:
        """Return True when any *still-live* create_department heads at ``role``.

        Returns:
            ``True`` or ``False`` reflecting the condition.
        """
        return bool(self.pending_role_refs.get(role))


def _prune_provenance(
    refs: dict[str, set[str]],
    referencer: str,
) -> None:
    """Drop *referencer* from every value set in *refs*, GC empty keys."""
    empty: list[str] = []
    for key, referencers in refs.items():
        referencers.discard(referencer)
        if not referencers:
            empty.append(key)
    for key in empty:
        refs.pop(key, None)


def _validate_change(
    change: ArchitectureChange,
    *,
    context: ArchitectureApplierContext,
    pending: _PendingChanges,
) -> list[str]:
    """Validate a single ``ArchitectureChange``.

    Returns:
        List of the declared element type.
    """
    if change.operation not in _SUPPORTED_OPS:
        return [
            f"Unknown operation {change.operation!r}; "
            f"supported: {sorted(_SUPPORTED_OPS)}"
        ]
    dispatch = {
        _OP_CREATE_ROLE: lambda: _validate_create_role(
            change, context=context, pending=pending
        ),
        _OP_CREATE_DEPARTMENT: lambda: _validate_create_department(
            change, context=context, pending=pending
        ),
        _OP_MODIFY_WORKFLOW: lambda: _validate_modify_workflow(change, context=context),
        _OP_REMOVE_ROLE: lambda: _validate_remove_role(
            change, context=context, pending=pending
        ),
        _OP_REMOVE_DEPARTMENT: lambda: _validate_remove_department(
            change, context=context, pending=pending
        ),
    }
    return dispatch[change.operation]()


def _validate_create_role(
    change: ArchitectureChange,
    *,
    context: ArchitectureApplierContext,
    pending: _PendingChanges,
) -> list[str]:
    """Validate create role.

    Returns:
        ``list[str]`` instance.
    """
    errors: list[str] = []
    name = change.target_name
    if name in pending.new_roles:
        errors.append(f"create_role: duplicate target_name {name!r} in proposal")
    elif context.has_role(name):
        errors.append(f"create_role: role {name!r} already exists")
    errors.extend(
        validate_payload_keys(
            change.payload,
            required=_CREATE_ROLE_REQUIRED,
            allowed=_CREATE_ROLE_ALLOWED,
        )
    )
    errors.extend(_validate_role_description(change.payload.get("description")))
    errors.extend(
        _validate_role_department(
            change.payload.get("department"),
            context=context,
            pending=pending,
        )
    )
    skills = change.payload.get("required_skills")
    if skills is not None:
        if not isinstance(skills, list | tuple):
            errors.append("create_role: 'required_skills' must be a list or tuple")
        else:
            errors.extend(_validate_skill_list(skills))
    errors.extend(_validate_authority_level(change.payload.get("authority_level")))
    errors.extend(_validate_tool_access(change.payload.get("tool_access")))
    if not errors:
        pending.new_roles.add(name)
        dept = change.payload.get("department")
        if isinstance(dept, str) and dept:
            pending.add_department_ref(dept=dept, from_role=name)
    return errors


def _validate_role_description(description: object) -> list[str]:
    """Validate the ``description`` field for a new role.

    ``description`` is the only required key in the create_role
    payload (see ``_CREATE_ROLE_REQUIRED``), so we reject ``None`` and
    blank strings here instead of treating them as "not provided".
    ``validate_payload_keys`` checks that the key is present; this
    helper ensures the value is a usable non-blank bounded string.

    Returns:
        List of the declared element type.
    """
    if description is None:
        return ["create_role: 'description' must not be None"]
    if not isinstance(description, str):
        return ["create_role: 'description' must be a string"]
    if not description.strip():
        return ["create_role: 'description' must not be blank"]
    if len(description) > _MAX_DESCRIPTION_CHARS:
        return [
            f"create_role: 'description' exceeds {_MAX_DESCRIPTION_CHARS} "
            f"chars (got {len(description)})"
        ]
    return []


def _validate_role_department(
    dept: object,
    *,
    context: ArchitectureApplierContext,
    pending: _PendingChanges,
) -> list[str]:
    """Validate the ``department`` reference for a new role.

    Returns:
        List of the declared element type.
    """
    if dept is None:
        return []
    if not isinstance(dept, str):
        return ["create_role: 'department' must be a string"]
    if not dept.strip():
        return ["create_role: 'department' must be a non-blank string"]
    known_dept = context.has_department(dept) or dept in pending.new_departments
    removed = dept in pending.removed_departments
    if not known_dept or removed:
        return [f"create_role: department {dept!r} does not exist"]
    return []


def _validate_skill_list(skills: Sequence[object]) -> list[str]:
    """Validate each entry in ``required_skills`` (type, length, count).

    Returns:
        List of the declared element type.
    """
    errors: list[str] = []
    if len(skills) > _MAX_SKILLS_PER_ROLE:
        errors.append(
            f"create_role: 'required_skills' exceeds "
            f"{_MAX_SKILLS_PER_ROLE} entries (got {len(skills)})"
        )
    for index, skill in enumerate(skills):
        if not isinstance(skill, str):
            errors.append(f"create_role: 'required_skills[{index}]' must be a string")
        elif not skill.strip():
            errors.append(f"create_role: 'required_skills[{index}]' must not be blank")
        elif len(skill) > _MAX_SKILL_NAME_CHARS:
            errors.append(
                f"create_role: 'required_skills[{index}]' exceeds "
                f"{_MAX_SKILL_NAME_CHARS} chars"
            )
    return errors


def _validate_authority_level(value: object) -> list[str]:
    """Validate the optional ``authority_level`` free-text field.

    Returns:
        List of the declared element type.
    """
    if value is None:
        return []
    if not isinstance(value, str):
        return ["create_role: 'authority_level' must be a string"]
    if not value.strip():
        return ["create_role: 'authority_level' must not be blank"]
    if len(value) > _MAX_AUTHORITY_LEVEL_CHARS:
        return [
            f"create_role: 'authority_level' exceeds "
            f"{_MAX_AUTHORITY_LEVEL_CHARS} chars (got {len(value)})"
        ]
    return []


def _validate_tool_access(value: object) -> list[str]:
    """Validate the optional ``tool_access`` list of tool identifiers.

    Returns:
        List of the declared element type.
    """
    if value is None:
        return []
    if not isinstance(value, list | tuple):
        return ["create_role: 'tool_access' must be a list or tuple"]
    errors: list[str] = []
    if len(value) > _MAX_TOOLS_PER_ROLE:
        errors.append(
            f"create_role: 'tool_access' exceeds "
            f"{_MAX_TOOLS_PER_ROLE} entries (got {len(value)})"
        )
    for index, entry in enumerate(value):
        if not isinstance(entry, str):
            errors.append(f"create_role: 'tool_access[{index}]' must be a string")
        elif not entry.strip():
            errors.append(f"create_role: 'tool_access[{index}]' must not be blank")
        elif len(entry) > _MAX_TOOL_NAME_CHARS:
            errors.append(
                f"create_role: 'tool_access[{index}]' exceeds "
                f"{_MAX_TOOL_NAME_CHARS} chars"
            )
    return errors


def _validate_dept_policies(value: object) -> list[str]:
    """Validate the optional ``policies`` list for a new department.

    Returns:
        List of the declared element type.
    """
    if value is None:
        return []
    if not isinstance(value, list | tuple):
        return ["create_department: 'policies' must be a list or tuple"]
    errors: list[str] = []
    if len(value) > _MAX_POLICIES_PER_DEPT:
        errors.append(
            f"create_department: 'policies' exceeds "
            f"{_MAX_POLICIES_PER_DEPT} entries (got {len(value)})"
        )
    for index, entry in enumerate(value):
        if not isinstance(entry, str):
            errors.append(f"create_department: 'policies[{index}]' must be a string")
        elif not entry.strip():
            errors.append(f"create_department: 'policies[{index}]' must not be blank")
        elif len(entry) > _MAX_POLICY_CHARS:
            errors.append(
                f"create_department: 'policies[{index}]' exceeds "
                f"{_MAX_POLICY_CHARS} chars"
            )
    return errors


def _validate_dept_head(value: object) -> list[str]:
    """Validate the optional ``head`` reference on a new department.

    Returns:
        List of the declared element type.
    """
    if value is None:
        return []
    if not isinstance(value, str):
        return ["create_department: 'head' must be a string"]
    if not value.strip():
        return ["create_department: 'head' must not be blank"]
    if len(value) > _MAX_ROLE_NAME_CHARS:
        return [f"create_department: 'head' exceeds {_MAX_ROLE_NAME_CHARS} chars"]
    return []


def _validate_create_department(
    change: ArchitectureChange,
    *,
    context: ArchitectureApplierContext,
    pending: _PendingChanges,
) -> list[str]:
    """Validate create department.

    Returns:
        ``list[str]`` instance.
    """
    errors: list[str] = []
    name = change.target_name
    if name in pending.new_departments:
        errors.append(f"create_department: duplicate target_name {name!r} in proposal")
    elif context.has_department(name):
        errors.append(f"create_department: department {name!r} already exists")
    errors.extend(
        validate_payload_keys(
            change.payload,
            required=_CREATE_DEPT_REQUIRED,
            allowed=_CREATE_DEPT_ALLOWED,
        )
    )
    head = change.payload.get("head")
    head_errors = _validate_dept_head(head)
    errors.extend(head_errors)
    # Only run existence / pending / context checks when basic
    # validation accepted the head.  Skipping prevents misleading
    # "does not exist" errors on malformed input and keeps context
    # calls out of the ``head=None`` / blank path.
    head_name: str | None = None
    if not head_errors and isinstance(head, str) and head:
        head_name = head
        if head_name in pending.removed_roles:
            errors.append(
                f"create_department: head role {head_name!r} is scheduled for "
                "removal earlier in this proposal"
            )
        elif head_name not in pending.new_roles and not context.has_role(head_name):
            errors.append(f"create_department: head role {head_name!r} does not exist")
    errors.extend(_validate_dept_policies(change.payload.get("policies")))
    if not errors:
        pending.new_departments.add(name)
        if head_name is not None:
            pending.add_role_ref(role=head_name, from_department=name)
    return errors


def _validate_modify_workflow(
    change: ArchitectureChange,
    *,
    context: ArchitectureApplierContext,
) -> list[str]:
    """Validate modify workflow.

    Returns:
        ``list[str]`` instance.
    """
    errors: list[str] = []
    if not context.has_workflow(change.target_name):
        errors.append(
            f"modify_workflow: workflow {change.target_name!r} does not exist"
        )
    if not change.payload:
        errors.append(
            "modify_workflow: payload must not be empty (no-op modify is rejected)"
        )
    return errors


def _validate_remove_role(
    change: ArchitectureChange,
    *,
    context: ArchitectureApplierContext,
    pending: _PendingChanges,
) -> list[str]:
    """Validate remove role.

    Returns:
        ``list[str]`` instance.
    """
    errors: list[str] = []
    name = change.target_name
    if change.payload:
        errors.append(
            f"remove_role: payload must be empty; got keys {sorted(change.payload)!r}"
        )
    if name in pending.removed_roles:
        errors.append(f"remove_role: duplicate target_name {name!r} in proposal")
    elif not (context.has_role(name) or name in pending.new_roles):
        errors.append(f"remove_role: role {name!r} does not exist")
    elif context.role_in_use(name) or pending.has_role_refs(name):
        errors.append(
            f"remove_role: role {name!r} still referenced by agents or departments"
        )
    if not errors:
        pending.removed_roles.add(name)
        # A subsequent remove_department may need to see that this
        # role is no longer introducing dept-ref provenance.
        pending.drop_refs_from_role(name)
    return errors


def _validate_remove_department(
    change: ArchitectureChange,
    *,
    context: ArchitectureApplierContext,
    pending: _PendingChanges,
) -> list[str]:
    """Validate remove department.

    Returns:
        ``list[str]`` instance.
    """
    errors: list[str] = []
    name = change.target_name
    if change.payload:
        errors.append(
            "remove_department: payload must be empty; "
            f"got keys {sorted(change.payload)!r}"
        )
    if name in pending.removed_departments:
        errors.append(f"remove_department: duplicate target_name {name!r} in proposal")
    elif not (context.has_department(name) or name in pending.new_departments):
        errors.append(f"remove_department: department {name!r} does not exist")
    elif context.department_in_use(name) or pending.has_department_refs(name):
        errors.append(f"remove_department: department {name!r} still referenced")
    if not errors:
        pending.removed_departments.add(name)
        # Clear any role-head refs that this department introduced so
        # a subsequent remove_role for that head is not blocked by a
        # stale reference.
        pending.drop_refs_from_department(name)
    return errors
