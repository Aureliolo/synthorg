"""Typed boundary for tool ``parameters_schema`` dicts.

Every :class:`~synthorg.tools.base.BaseTool` subclass passes a JSON
Schema describing its expected arguments. Today every caller produces
that schema from ``<Args>.model_json_schema()``; the wire shape is
JSON-valued. The :class:`ToolParametersSchema` ``RootModel`` validates
that shape at the construction boundary so a non-JSON value (e.g. a
``set``, a class instance, a custom dataclass) cannot silently slip in
and break downstream MCP serialisation or invoker validation.

The model deliberately does NOT model the JSON Schema vocabulary
itself (``type``, ``properties``, ``required``, ...). Pydantic's
``model_json_schema`` emits a wide superset (``$defs``, ``$ref``,
``anyOf``, ``oneOf``, etc.); pinning a hand-written subset would
reject valid emissions from new field types. The boundary check is
"is this a JSON-valued dict", not "is this a JSON Schema this hand-
written model recognises". JSON Schema *interpretation* lives in the
invocation-validation layer (``synthorg.tools.invoker_validation``),
not here.
"""

from pydantic import JsonValue, RootModel


class ToolParametersSchema(RootModel[dict[str, JsonValue]]):
    """JSON-valued dict shape for ``BaseTool.parameters_schema``.

    Constructed at the boundary in ``BaseTool.__init__`` so a tool
    whose schema contains a non-JSON value fails fast with a clear
    Pydantic ``ValidationError`` instead of corrupting downstream
    JSON emission. ``.root`` exposes the validated dict; ``.as_dict``
    returns a defensive deep copy.
    """

    def as_dict(self) -> dict[str, JsonValue]:
        """Return a defensive deep copy of the validated schema dict.

        BaseTool stores the result behind a ``MappingProxyType`` after
        ``deepcopy`` to prevent mutation of internal state; callers
        that need a fresh writable view use this method.

        Returns:
            Mapping from ``str`` to ``JsonValue``.
        """
        import copy as _copy  # noqa: PLC0415 -- stdlib alias, hot path

        return _copy.deepcopy(self.root)
