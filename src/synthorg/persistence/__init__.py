"""Pluggable persistence layer for operational data (see Memory design page).

This package uses explicit per-module imports rather than re-exporting
the protocol, repository protocols, config models, factory, and error
hierarchy from the top level. Import specific symbols from their
defining submodule, e.g.::

    from synthorg.persistence.protocol import PersistenceBackend
    from synthorg.persistence.factory import create_backend
    from synthorg.persistence.config import PersistenceConfig, SQLiteConfig
    from synthorg.core.persistence_errors import PersistenceError

Eagerly importing ``factory -> protocol`` (and ~60 repository protocols)
here meant that any importer of a persistence *leaf* (most commonly the
``persistence._shared`` datetime/marshalling helpers, used by 150+ call
sites across the codebase) transitively loaded ``persistence.protocol``,
which references domain models and DTOs from app-layer packages and
reached back up into the partially-initialised provider/config/engine
graph. That accidental drag is the spine of the cold-import cycle; a
leaf utility import has no business loading the whole backend. Keeping
this init empty stops it. Backend registration still happens eagerly
inside :mod:`synthorg.persistence.factory` (the registry is built in the
module body), so ``create_backend`` consumers that import the factory
submodule directly are unaffected.
"""
