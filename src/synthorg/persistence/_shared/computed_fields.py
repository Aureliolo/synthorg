# module-kind: code
"""Store what a model was built from, not what it derives.

A ``@computed_field`` is serialised by ``model_dump`` and refused by
``model_validate``, because every frozen model here sets ``extra="forbid"``.
So a model carrying one cannot survive its own round trip through a JSON
column: it writes fine and every later read raises, which is how an approvals
page came to report a count of 106 above an empty list. The stored copy is
also a second owner of a derived value, free to disagree with the property
once the inputs change underneath it.

Both directions are derived from ``model_computed_fields`` rather than named,
because a list of computed fields is one new property away from being wrong in
exactly the way that produced the defect.
"""

from pydantic import BaseModel


def dump_stored_json(model: BaseModel) -> dict[str, object]:
    """Serialise *model* for a JSON column, without its derived values.

    Args:
        model: The model to store.

    Returns:
        The JSON-ready mapping of the fields the model was built from.
    """
    return model.model_dump(mode="json", exclude=set(type(model).model_computed_fields))


def load_stored_json[ModelT: BaseModel](
    model_cls: type[ModelT], data: object
) -> ModelT:
    """Validate a stored mapping into *model_cls*, tolerating derived values.

    Rows written before the writer stopped storing computed fields still
    carry them, and no migration can recompute a value the model derives
    anyway, so they are dropped here rather than rejected.

    Args:
        model_cls: The model to build.
        data: The stored mapping.

    Returns:
        The validated model.
    """
    if not isinstance(data, dict):
        return model_cls.model_validate(data)
    computed = set(model_cls.model_computed_fields)
    return model_cls.model_validate(
        {key: value for key, value in data.items() if key not in computed}
    )


__all__ = ["dump_stored_json", "load_stored_json"]
