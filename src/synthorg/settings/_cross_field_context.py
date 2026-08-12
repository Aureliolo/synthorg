# module-kind: code
"""How the settings service answers the cross-field rules' questions.

The rules judge a write against settings it is not writing, so each needs a
different view of the store: what is in force, what the registry ships, and
which of the two an operator actually picked. Kept beside the service rather
than inside it because they are the rules' contract, and they change when a
rule needs a new kind of answer.
"""

from collections.abc import Awaitable, Callable, Sequence

from synthorg.settings.cross_field_rules import enforce_cross_field_rules
from synthorg.settings.enums import SettingSource
from synthorg.settings.models import SettingDefinition, SettingValue


async def guard_cross_field_rules(
    items: Sequence[tuple[str, str, str]],
    *,
    get_entry: Callable[[str, str], Awaitable[SettingValue]],
    get_definition: Callable[[str, str], SettingDefinition | None],
) -> None:
    """Reject a write whose combined result breaks a cross-setting rule.

    Args:
        items: The triples about to be written.
        get_entry: Resolves a key through the full precedence chain.
        get_definition: Resolves a key's registered definition.

    Raises:
        SettingValidationError: When the resulting combination is invalid.
            Raised before anything is persisted, so the caller sees the
            refusal rather than a 200 followed by a value the system never
            enforces.
    """

    async def _current(namespace: str, key: str) -> str | None:
        # Deliberately unguarded: a read that cannot answer must fail the
        # write, not let the rule compare against a default that is not
        # in force and approve the combination it exists to refuse.
        entry = await get_entry(namespace, key)
        return entry.value

    def _default(namespace: str, key: str) -> str | None:
        # ``definition.default`` is already ``str | None``; wrapping it in
        # ``str()`` turns an absent default into the literal "None", which
        # a rule then compares as if it were a configured value.
        definition = get_definition(namespace, key)
        return None if definition is None else definition.default

    async def _is_configured(namespace: str, key: str) -> bool:
        # The source, not a value comparison: an operator who deliberately
        # writes the same number the default happens to carry has still
        # chosen it, and a rule that refuses a write on their behalf owes
        # them the difference.
        entry = await get_entry(namespace, key)
        return entry.source is not SettingSource.DEFAULT

    await enforce_cross_field_rules(
        items,
        get_current=_current,
        get_default=_default,
        is_configured=_is_configured,
    )


__all__ = ["guard_cross_field_rules"]
