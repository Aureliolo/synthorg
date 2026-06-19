"""Type stubs for ``yoyo.migrations``: the migration item + list types."""

from collections.abc import Iterable

class Migration:
    id: str

class MigrationList(list[Migration]):
    post_apply: list[Migration]

    def __init__(
        self,
        items: Iterable[Migration] = ...,
        post_apply: Iterable[Migration] = ...,
    ) -> None: ...
