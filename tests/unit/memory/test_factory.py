"""Tests for memory backend factory."""

from unittest.mock import MagicMock

import pytest

from synthorg.core.types import NotBlankStr
from synthorg.memory.backends.composite import CompositeBackend
from synthorg.memory.backends.composite.config import CompositeBackendConfig
from synthorg.memory.backends.inmemory import InMemoryBackend
from synthorg.memory.backends.sqlvector import SqlVectorBackend
from synthorg.memory.config import CompanyMemoryConfig, MemoryOptionsConfig
from synthorg.memory.errors import MemoryConfigError
from synthorg.memory.factory import (
    MemoryBackendDeps,
    build_in_memory_backend,
    create_memory_backend,
)
from synthorg.persistence.memory_vector_protocol import MemoryVectorRepository

pytestmark = pytest.mark.unit


def _repository() -> MemoryVectorRepository:
    """A typed stand-in for the durable vector store."""
    return MagicMock(spec=MemoryVectorRepository)


class TestCreateMemoryBackend:
    def test_sqlvector_is_the_default_backend(self) -> None:
        backend = create_memory_backend(
            CompanyMemoryConfig(),
            deps=MemoryBackendDeps(repository=_repository()),
        )

        assert isinstance(backend, SqlVectorBackend)

    def test_sqlvector_without_repository_raises(self) -> None:
        # Failing loud here is deliberate: the alternative is silently
        # handing back an ephemeral store that looks like working memory
        # while losing everything on restart.
        with pytest.raises(MemoryConfigError, match="MemoryVectorRepository"):
            create_memory_backend(
                CompanyMemoryConfig(backend="sqlvector"),
                deps=MemoryBackendDeps(),
            )

    def test_sqlvector_without_embedder_builds_lexical_only(self) -> None:
        backend = create_memory_backend(
            CompanyMemoryConfig(backend="sqlvector"),
            deps=MemoryBackendDeps(repository=_repository()),
        )

        assert isinstance(backend, SqlVectorBackend)
        assert backend.supports_dense_search is False

    def test_inmemory_backend_is_selectable(self) -> None:
        backend = create_memory_backend(
            CompanyMemoryConfig(backend="inmemory"),
            deps=MemoryBackendDeps(),
        )

        assert isinstance(backend, InMemoryBackend)

    def test_deps_are_optional_for_backends_that_need_none(self) -> None:
        backend = create_memory_backend(CompanyMemoryConfig(backend="inmemory"))

        assert isinstance(backend, InMemoryBackend)

    def test_unknown_backend_rejected_by_config_validation(self) -> None:
        with pytest.raises(ValueError, match="Unknown memory backend"):
            CompanyMemoryConfig(backend="nonexistent")

    def test_unknown_backend_bypassing_validation_raises(self) -> None:
        config = CompanyMemoryConfig(backend="inmemory")
        smuggled = config.model_copy(update={"backend": "nonexistent"})

        with pytest.raises(MemoryConfigError, match="Unknown memory backend"):
            create_memory_backend(smuggled)

    def test_max_memories_is_threaded_through(self) -> None:
        backend = create_memory_backend(
            CompanyMemoryConfig(
                backend="inmemory",
                options=MemoryOptionsConfig(max_memories_per_agent=7),
            ),
        )

        assert isinstance(backend, InMemoryBackend)
        assert backend.max_memories_per_agent == 7


class TestCompositeBackend:
    def test_routes_namespaces_across_child_backends(self) -> None:
        backend = create_memory_backend(
            CompanyMemoryConfig(
                backend="composite",
                composite=CompositeBackendConfig(
                    routes={
                        NotBlankStr("scratch"): NotBlankStr("inmemory"),
                        NotBlankStr("memories"): NotBlankStr("sqlvector"),
                    },
                    default=NotBlankStr("sqlvector"),
                ),
            ),
            deps=MemoryBackendDeps(repository=_repository()),
        )

        assert isinstance(backend, CompositeBackend)

    def test_unknown_child_backend_raises(self) -> None:
        with pytest.raises(MemoryConfigError, match="not a recognised backend"):
            create_memory_backend(
                CompanyMemoryConfig(
                    backend="composite",
                    composite=CompositeBackendConfig(
                        default=NotBlankStr("nonexistent"),
                    ),
                ),
                deps=MemoryBackendDeps(repository=_repository()),
            )

    def test_durable_child_without_repository_raises(self) -> None:
        # The composite must not quietly downgrade a durable route to an
        # ephemeral one; that is the silent-degradation failure again.
        with pytest.raises(MemoryConfigError, match="MemoryVectorRepository"):
            create_memory_backend(
                CompanyMemoryConfig(
                    backend="composite",
                    composite=CompositeBackendConfig(
                        default=NotBlankStr("sqlvector"),
                    ),
                ),
                deps=MemoryBackendDeps(),
            )


class TestBuildInMemoryBackend:
    def test_builds_the_ephemeral_backend(self) -> None:
        assert isinstance(build_in_memory_backend(), InMemoryBackend)
