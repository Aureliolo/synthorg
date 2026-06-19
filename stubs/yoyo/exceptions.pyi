"""Type stubs for ``yoyo.exceptions``: the orchestration error subset."""

class BadMigration(Exception): ...  # noqa: N818 -- mirrors yoyo's real class name
class LockTimeout(Exception): ...  # noqa: N818 -- mirrors yoyo's real class name
class MigrationConflict(Exception): ...  # noqa: N818 -- mirrors yoyo's real class name
