"""PEP 562 lazy-export contract for ``synthorg.ontology``.

``OntologyService`` / ``OntologyEntityRepository`` are exported via
``__getattr__`` to keep the heavy persistence/security graph off the
ontology package-import path (breaks a cross-package cycle). These
tests lock the resolve-and-cache behaviour and the unknown-name
contract so a regression in the lazy machinery fails loudly.
"""

import pytest

from synthorg import ontology

pytestmark = pytest.mark.unit


def test_unknown_attribute_raises_attribute_error() -> None:
    with pytest.raises(AttributeError, match="has no attribute"):
        _ = ontology.NotARealOntologyExport


def test_lazy_export_resolves_and_caches() -> None:
    from synthorg.ontology.service import OntologyService as _Direct

    resolved = ontology.OntologyService
    assert resolved is _Direct
    # Second access is served from the module ``globals()`` cache, so
    # it must return the identical object (proves the cache write).
    assert ontology.OntologyService is resolved
    assert "OntologyService" in vars(ontology)


def test_lazy_export_entity_repository_resolves() -> None:
    from synthorg.persistence.ontology_protocol import (
        OntologyEntityRepository as _Direct,
    )

    assert ontology.OntologyEntityRepository is _Direct


def test_dir_lists_lazy_names() -> None:
    names = dir(ontology)
    assert "OntologyService" in names
    assert "OntologyEntityRepository" in names
    # __dir__ returns the sorted public surface.
    assert names == sorted(names)
