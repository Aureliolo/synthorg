# module-kind: code
"""Nullable-union normalization for the OpenAPI post-processor.

Litestar wraps ``T | None`` fields in ``oneOf``; API doc renderers
expect the compact ``type: ["T", "null"]`` form for primitives and
``anyOf`` for ``$ref``-based nullables. ``_normalize_nullable_unions``
walks the generated schema and rewrites these shapes; the ``_flatten_*``
/ ``_collapse_*`` helpers implement the per-union rules. Called by
``inject_rfc9457_responses`` in ``synthorg.api.openapi``.

These helpers mutate the ``result`` dict passed to them in place; they
must only be called on a freshly constructed dict (the enclosing
comprehension in ``_normalize_nullable_unions``), never on the original
input schema.
"""

from typing import Final

from pydantic import JsonValue

_SCHEMAS_PREFIX: Final[str] = "#/components/schemas/"


def _flatten_nullable_ref(
    result: dict[str, JsonValue],
    keyword: str,
    branch: dict[str, JsonValue],
    all_schemas: dict[str, JsonValue],
) -> bool:
    """Inline a nullable ``$ref`` to an enum schema.

    When the ``$ref`` target is a simple enum (has ``type`` and
    ``enum``), inlines the enum values and flattens to
    ``{type: [T, "null"], enum: [..., null]}``.

    Returns ``True`` if the union was handled, ``False`` otherwise.

    Returns:
        ``True`` or ``False`` reflecting the condition.
    """
    ref = branch.get("$ref", "")
    if not isinstance(ref, str) or not ref.startswith(_SCHEMAS_PREFIX):
        return False

    target_name = ref.removeprefix(_SCHEMAS_PREFIX)
    target = all_schemas.get(target_name, {})

    if not isinstance(target, dict) or "enum" not in target or "type" not in target:
        return False
    target_enum = target["enum"]
    if not isinstance(target_enum, list):
        return False

    # ``default`` on the enum component schema reflects a default from
    # SOME OTHER field that uses the enum non-nullably; inlining it
    # here would falsely claim the current (nullable) field shares
    # that default. The field's own default (typically ``null`` for
    # ``AutonomyLevel | None = Field(default=None)``) does not survive
    # Litestar's oneOf wrapping, so the correct outcome is "no default"
    # rather than "the enum's default."
    prop_desc = result.get("description")
    merged: dict[str, JsonValue] = {
        k: v for k, v in target.items() if k not in ("title", "description", "default")
    }
    merged["type"] = [target["type"], "null"]
    merged["enum"] = [*target_enum, None]
    del result[keyword]
    result.update(merged)
    if prop_desc:
        result["description"] = prop_desc
    return True


def _flatten_nullable(
    result: dict[str, JsonValue],
    keyword: str,
    items: list[JsonValue],
    all_schemas: dict[str, JsonValue] | None = None,
) -> None:
    """Flatten a nullable union (``T | None``) in *result* in place.

    * Primitive branch (has ``type``): collapses to
      ``{type: [T, "null"], ...extras}``.
    * ``$ref`` to enum: delegates to :func:`_flatten_nullable_ref`.
    * Other ``$ref``: swaps ``oneOf`` to ``anyOf``.
    * Discriminated-union nullable (multiple ``$ref`` branches + null):
      swaps ``oneOf`` to ``anyOf`` so the null branch is tolerated.
    """
    null_entries = [i for i in items if isinstance(i, dict) and i.get("type") == "null"]
    if len(null_entries) != 1:
        return

    non_null = [i for i in items if i is not null_entries[0]]
    if not non_null:
        return

    # Multi-primitive nullable union (e.g. str | int | float | bool | None):
    # all non-null branches are primitive types -> collapse to type array.
    if len(non_null) > 1 and all(
        isinstance(b, dict) and "type" in b and len(b) == 1 for b in non_null
    ):
        types: list[JsonValue] = [b["type"] for b in non_null if isinstance(b, dict)]
        types.append("null")
        del result[keyword]
        result["type"] = types
        return

    if len(non_null) != 1:
        # Nullable multi-$ref union (typical shape for
        # ``Annotated[A | B, Field(discriminator=...)] | None`` -- Pydantic
        # emits ``oneOf: [$ref, $ref, ..., {type: "null"}]`` without the
        # ``"discriminator"`` marker surviving through Litestar schema
        # generation, so we cannot test for it directly).  Converting to
        # ``anyOf`` keeps every branch and tolerates null without losing
        # information: the underlying $ref schemas still carry their own
        # constraints, so validation against the union remains sound.
        # We intentionally do NOT touch non-``$ref`` multi-branch oneOfs
        # of bare scalar primitives here -- those represent genuinely
        # exclusive primitive unions that would be weakened by becoming
        # ``anyOf``.  The one exception is a ``JsonValue``-shaped union:
        # ``Mapping[str, JsonValue]`` is the only thing Litestar emits as
        # a oneOf carrying BOTH ``object`` and ``array`` branches plus the
        # scalar primitives and null, so a proper superset of
        # ``{object, array}`` uniquely identifies it.  Each value matches
        # at most one by-type branch, so ``anyOf`` loses no exclusivity,
        # and unlike a primitive ``type`` array it keeps the structural
        # ``items``/``additionalProperties`` branches.  A genuinely
        # exclusive structural union (``objectA | objectB | null``, or
        # object+array with no scalars) is NOT a superset, so it stays
        # an exclusive ``oneOf``; likewise a constrained-primitive union.
        branch_types: set[str] = {
            t
            for b in non_null
            if isinstance(b, dict)
            for t in (b.get("type"),)
            if isinstance(t, str)
        }
        all_ref = all(isinstance(b, dict) and "$ref" in b for b in non_null)
        if keyword == "oneOf" and (all_ref or branch_types > {"object", "array"}):
            result["anyOf"] = result.pop("oneOf")
        return

    branch = non_null[0]
    if isinstance(branch, dict) and "type" in branch:
        merged = {k: v for k, v in branch.items() if k != "type"}
        merged["type"] = [branch["type"], "null"]
        del result[keyword]
        result.update(merged)
        return

    if (
        isinstance(branch, dict)
        and "$ref" in branch
        and all_schemas
        and _flatten_nullable_ref(result, keyword, branch, all_schemas)
    ):
        return

    if keyword == "oneOf":
        result["anyOf"] = result.pop("oneOf")


_EXPECTED_UNION_BRANCHES: Final[int] = 2


def _collapse_redundant_union(
    result: dict[str, JsonValue],
    keyword: str,
    items: list[JsonValue],
) -> None:
    """Collapse a redundant ``oneOf`` with an empty schema.

    Litestar emits ``oneOf: [{$ref: ...}, {}]`` for tuple item
    schemas.  Only applies to ``oneOf`` -- collapsing ``anyOf``
    with ``{}`` would change semantics (``{}`` matches anything,
    so ``anyOf`` with ``{}`` means "accept anything").
    """
    if keyword != "oneOf" or len(items) != _EXPECTED_UNION_BRANCHES:
        return
    empty_entries = [i for i in items if isinstance(i, dict) and not i]
    if len(empty_entries) != 1:
        return
    concrete = [i for i in items if i is not empty_entries[0]]
    if concrete and isinstance(concrete[0], dict):
        del result[keyword]
        result.update(concrete[0])


def _normalize_nullable_unions(
    obj: JsonValue,
    all_schemas: dict[str, JsonValue] | None = None,
) -> JsonValue:
    """Flatten nullable union schemas to idiomatic JSON Schema 2020-12.

    Litestar wraps ``T | None`` fields in ``oneOf``, producing
    ``oneOf: [{type: "string"}, {type: "null"}]``.  API doc renderers
    expect the compact ``type: ["string", "null"]`` form for
    primitives, and ``anyOf`` for ``$ref``-based nullables.

    Args:
        obj: Any JSON-serialisable value (typically the full OpenAPI
            schema dict).
        all_schemas: ``components.schemas`` dict used to resolve
            ``$ref`` targets for enum inlining.  When ``None``,
            ``$ref``-based nullable unions are converted to ``anyOf``
            (enums cannot be inlined without schema resolution).

    Conversion rules (applied to both ``oneOf`` and ``anyOf``):

    * **Primitive nullable** -- non-null branch has a ``type`` key:
      merge into ``{type: [T, "null"], ...extras}``.
    * **Enum $ref nullable** -- non-null branch is a ``$ref`` to a
      simple enum: inline the enum values and flatten.
    * **Object $ref nullable** -- non-null branch is a ``$ref`` to
      a complex schema: convert to ``anyOf`` (known renderer
      bug -- see linked issue for details).
    * **Redundant union** -- one branch is an empty schema ``{}``:
      collapse to just the non-empty branch (Litestar emits this
      for ``tuple[T, ...]`` item schemas).
    * **Discriminated unions** -- no ``{"type": "null"}`` entry and
      no empty-schema branch: left unchanged.

    Returns:
        ``Any`` instance.
    """
    if isinstance(obj, dict):
        result: dict[str, JsonValue] = {
            k: _normalize_nullable_unions(v, all_schemas) for k, v in obj.items()
        }

        for keyword in ("oneOf", "anyOf"):
            branch_list = result.get(keyword)
            if not isinstance(branch_list, list):
                continue
            _flatten_nullable(result, keyword, branch_list, all_schemas)
            # Re-fetch: _flatten_nullable may have replaced the list.
            new_list = result.get(keyword)
            if isinstance(new_list, list):
                _collapse_redundant_union(result, keyword, new_list)

        return result

    if isinstance(obj, list):
        return [_normalize_nullable_unions(item, all_schemas) for item in obj]
    return obj
