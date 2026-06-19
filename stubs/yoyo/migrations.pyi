from collections.abc import Iterable, Iterator

class Migration:
    id: str

class MigrationList:
    post_apply: MigrationList
    def __init__(
        self,
        items: Iterable[Migration] = ...,
        post_apply: Iterable[Migration] = ...,
    ) -> None: ...
    def __iter__(self) -> Iterator[Migration]: ...
