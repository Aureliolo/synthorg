"""Per-setting write-time JSON-shape validators.

The base ``_validate_json`` in ``SettingsService`` only checks
parseability; the runtime bridge-config validators in
:mod:`synthorg.settings.bridge_configs` enforce the deeper structural
contract (canonical origins, port range, no userinfo, etc.). When
``/settings`` writes a new value the runtime contract has not yet run,
so an operator can persist a structurally invalid payload and only see
it rejected at the next process boot via the bridge-config fallback.

This module bridges that gap. Each entry in :data:`_JSON_VALIDATORS`
maps a ``(namespace, key)`` to a callable that takes the parsed JSON
value and raises :class:`ValueError` if the payload would not pass the
runtime validator. Adding a setting here is a single-line registration
plus the validator function itself; the dispatcher in
``service._validate_json`` consults this map after JSON parsing
succeeds.

Validators MUST raise ``ValueError`` (the existing error path in
``service._validate_json`` translates to ``SettingValidationError`` so
the message reaches the operator). They MUST NOT mutate the parsed
value -- the dispatcher is fail-fast at the validation boundary, not
a normalisation hook.
"""

from collections.abc import Callable
from typing import Final

#: Reject a ``company`` structural blob nesting deeper than this. The persisted
#: department / agent shapes are shallow (department -> teams -> members); a
#: pathologically deep payload from the generic settings-write MCP tool is a
#: denial-of-service (``RecursionError``) vector, so it is refused before any
#: model validation walks it.
_MAX_JSON_DEPTH: Final[int] = 32

#: Universal raw-text nesting ceiling for ANY JSON setting, checked on the
#: unparsed string *before* ``json.loads`` runs. ``json.loads`` recurses once
#: per nesting level and raises an uncaught ``RecursionError`` on a
#: pathologically deep payload, so the parse itself -- not just the post-parse
#: :func:`_reject_deep_nesting` company guard -- must be shielded. Sits far
#: above any legitimate setting's nesting yet far below the depth at which
#: ``json.loads`` recurses dangerously.
_MAX_RAW_JSON_DEPTH: Final[int] = 64


def reject_raw_json_over_depth(text: str, max_depth: int = _MAX_RAW_JSON_DEPTH) -> None:
    """Reject a raw JSON string nesting past *max_depth* before it is parsed.

    Scans the unparsed text counting ``[`` / ``{`` nesting (ignoring brackets
    inside string literals), so ``json.loads`` is never handed a payload deep
    enough to raise ``RecursionError``. A single O(n) pass, no recursion, so
    the guard itself cannot blow the stack on the input it defends against.

    Raises:
        ValueError: If bracket/brace nesting exceeds *max_depth*.
    """
    depth = 0
    in_string = False
    escaped = False
    for ch in text:
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch in "[{":
            depth += 1
            if depth > max_depth:
                msg = f"JSON nests deeper than {max_depth} levels"
                raise ValueError(msg)
        elif ch in "]}" and depth > 0:
            # Clamp at zero: unmatched closing brackets must not drive the
            # counter negative, else a later run of openers could reach a
            # genuine deep nesting while the counter stays under max_depth.
            depth -= 1


#: Keys every persisted ``company.agents`` element must carry as a non-empty
#: string (mirrors ``setup_agents._REQUIRED_AGENT_KEYS`` without importing up
#: into the controller layer).
_REQUIRED_AGENT_KEYS: Final[frozenset[str]] = frozenset({"name", "role"})


def _validate_csp_docs_external_origins(value: object) -> None:
    """Reject any non-canonical ``csp_docs_external_origins`` payload.

    Reuses :class:`synthorg.settings.bridge_configs.ApiBridgeConfig`'s
    field validator so write-time and runtime contracts cannot drift.
    The bridge field expects ``tuple[str, ...]``; we coerce a JSON
    list and let the validator surface its own ValueError.

    Raises:
        ValueError: If *value* is not a JSON array of strings, or if the
            reused ``ApiBridgeConfig`` field validator rejects an origin
            (non-canonical scheme/host, bad port, userinfo, path, etc.).
    """
    from synthorg.settings.bridge_configs import (  # noqa: PLC0415
        ApiBridgeConfig,
    )

    # The dispatcher in ``service._validate_json`` only catches
    # ValueError, so raise ValueError for shape failures even when the
    # underlying issue is a type mismatch -- TypeError would bypass the
    # SettingValidationError translation and surface as an unhandled
    # 500.
    if not isinstance(value, list):
        msg = (
            "csp_docs_external_origins must be a JSON array of canonical HTTPS origins"
        )
        raise ValueError(msg)  # noqa: TRY004 -- dispatcher contract requires ValueError
    for entry in value:
        if not isinstance(entry, str):
            msg = (
                "csp_docs_external_origins entries must be strings;"
                f" got {type(entry).__name__}"
            )
            raise ValueError(msg)  # noqa: TRY004 -- dispatcher contract requires ValueError
    # The bridge field validator handles empty-tuple, scheme, host,
    # port-range, userinfo, and path/query/fragment rejection.
    ApiBridgeConfig(csp_docs_external_origins=tuple(value))


def _reject_deep_nesting(value: object, key: str) -> None:
    """Reject a ``company/*`` payload nesting past :data:`_MAX_JSON_DEPTH`.

    Walks the parsed structure iteratively (an explicit stack, never
    recursion) so the guard itself cannot ``RecursionError`` on the very
    input it defends against.

    Raises:
        ValueError: If any node sits deeper than :data:`_MAX_JSON_DEPTH`.
    """
    stack: list[tuple[object, int]] = [(value, 1)]
    while stack:
        node, depth = stack.pop()
        if depth > _MAX_JSON_DEPTH:
            msg = f"company/{key} nests deeper than {_MAX_JSON_DEPTH} levels"
            raise ValueError(msg)
        if isinstance(node, dict):
            stack.extend((child, depth + 1) for child in node.values())
        elif isinstance(node, list):
            stack.extend((child, depth + 1) for child in node)


def _validate_company_departments(value: object) -> None:
    """Reject a ``company.departments`` payload of the wrong shape.

    The generic settings-write MCP tool can target this key directly,
    bypassing the ``Team`` validation the team CRUD path applies, so each
    persisted team is re-validated as :class:`~synthorg.core.company_departments.Team`
    here. The department wrapper itself is checked loosely (the persisted
    blob is a looser dict than the in-memory ``Department`` model, e.g. it
    carries ``head_role`` / ``budget_percent``), so only the load-bearing
    invariants are enforced: a list of objects, each with a non-empty
    string ``name`` and, when present, well-formed ``teams``.

    Raises:
        ValueError: If the payload is not a list of department objects, a
            department lacks a string ``name``, ``teams`` is not a list, or
            a team entry fails ``Team`` validation.
    """
    from synthorg.core.company_departments import Team  # noqa: PLC0415

    if not isinstance(value, list):
        msg = "company/departments must be a JSON array of department objects"
        raise ValueError(msg)  # noqa: TRY004 -- dispatcher contract requires ValueError
    _reject_deep_nesting(value, "departments")
    for idx, dept in enumerate(value):
        if not isinstance(dept, dict):
            msg = (
                f"company/departments[{idx}] must be an object, "
                f"got {type(dept).__name__}"
            )
            raise ValueError(msg)  # noqa: TRY004 -- dispatcher contract requires ValueError
        name = dept.get("name")
        if not isinstance(name, str) or not name.strip():
            msg = f"company/departments[{idx}].name must be a non-empty string"
            raise ValueError(msg)
        raw_teams = dept.get("teams", [])
        if not isinstance(raw_teams, list):
            msg = f"company/departments[{idx}].teams must be an array"
            raise ValueError(msg)  # noqa: TRY004 -- dispatcher contract requires ValueError
        for team_idx, team in enumerate(raw_teams):
            try:
                Team.model_validate(team)
            except (ValueError, TypeError) as exc:
                msg = (
                    f"company/departments[{idx}].teams[{team_idx}] is not a "
                    f"valid team: {exc}"
                )
                raise ValueError(msg) from exc


def _validate_company_agents(value: object) -> None:
    """Reject a ``company.agents`` payload of the wrong shape.

    Mirrors the setup-agent element check at the settings-write boundary so
    the generic settings-write MCP tool cannot persist a corrupt agents
    blob: a list of objects, each carrying a non-empty string ``name`` and
    ``role``.

    Raises:
        ValueError: If the payload is not a list of agent objects, or an
            element lacks a non-empty string ``name`` / ``role``.
    """
    if not isinstance(value, list):
        msg = "company/agents must be a JSON array of agent objects"
        raise ValueError(msg)  # noqa: TRY004 -- dispatcher contract requires ValueError
    _reject_deep_nesting(value, "agents")
    for idx, agent in enumerate(value):
        if not isinstance(agent, dict):
            msg = f"company/agents[{idx}] must be an object, got {type(agent).__name__}"
            raise ValueError(msg)  # noqa: TRY004 -- dispatcher contract requires ValueError
        missing = _REQUIRED_AGENT_KEYS - agent.keys()
        if missing:
            msg = f"company/agents[{idx}] missing required keys: {sorted(missing)}"
            raise ValueError(msg)
        for required in sorted(_REQUIRED_AGENT_KEYS):
            field = agent[required]
            if not isinstance(field, str) or not field.strip():
                msg = f"company/agents[{idx}].{required} must be a non-empty string"
                raise ValueError(msg)


def _validate_output_style_exemptions(value: object) -> None:
    """Reject an ``output_style.exemptions`` payload of the wrong shape.

    A sanctioned exemption weakens the guardrail, so a malformed entry must be
    rejected at write time (visible to the operator) rather than silently
    dropped at the next pack rebuild. Each entry is re-validated as the same
    :class:`~synthorg.engine.output_style.models.SanctionedExemption` the
    enforcement path parses, so write-time and runtime contracts cannot drift.

    Raises:
        ValueError: If the payload is not a JSON array, or any entry fails
            ``SanctionedExemption`` validation (unknown ``scope_kind``, blank
            field, etc.).
    """
    from synthorg.engine.output_style.models import (  # noqa: PLC0415
        SanctionedExemption,
    )

    if not isinstance(value, list):
        msg = "output_style/exemptions must be a JSON array of exemption objects"
        raise ValueError(msg)  # noqa: TRY004 -- dispatcher contract requires ValueError
    for idx, entry in enumerate(value):
        try:
            SanctionedExemption.model_validate(entry)
        except (ValueError, TypeError) as exc:
            msg = f"output_style/exemptions[{idx}] is not a valid exemption: {exc}"
            raise ValueError(msg) from exc


_JSON_VALIDATORS: Final[dict[tuple[str, str], Callable[[object], None]]] = {
    ("api", "csp_docs_external_origins"): _validate_csp_docs_external_origins,
    ("company", "departments"): _validate_company_departments,
    ("company", "agents"): _validate_company_agents,
    ("output_style", "exemptions"): _validate_output_style_exemptions,
}


def get_json_validator(namespace: str, key: str) -> Callable[[object], None] | None:
    """Return the registered write-time validator for *namespace*/*key*.

    Returns ``None`` when the setting has no JSON-shape validator
    beyond the base ``json.loads`` parseability check. Service-layer
    code calls this after ``json.loads`` succeeds and dispatches if
    the result is non-None.
    """
    return _JSON_VALIDATORS.get((namespace, key))
