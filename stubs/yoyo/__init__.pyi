"""Minimal type stubs for the yoyo-migrations runtime API used by SynthOrg.

Covers only the surface consumed in
``synthorg.persistence.migrations`` and
``synthorg.persistence.migration_helpers``: migration discovery and the
backend factory.
"""

from yoyo.backends import Backend
from yoyo.migrations import MigrationList

def read_migrations(*sources: str) -> MigrationList: ...
def get_backend(uri: str) -> Backend: ...
