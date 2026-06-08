"""Unit tests for knowledge config, errors, and the new enum values."""

import pytest
from pydantic import ValidationError

from synthorg.core.error_taxonomy import ErrorCategory, ErrorCode
from synthorg.core.memory_enums import MemoryCategory
from synthorg.knowledge.config import KnowledgeConfig
from synthorg.knowledge.enums import ContentKind, SourceStatus, SourceType
from synthorg.knowledge.errors import (
    KnowledgeDependencyError,
    KnowledgeError,
    KnowledgeIngestError,
    KnowledgeRetrievalError,
    KnowledgeSourceNotFoundError,
    KnowledgeSourceUnavailableError,
    KnowledgeValidationError,
)

pytestmark = pytest.mark.unit


class TestEnums:
    def test_memory_category_knowledge_value(self) -> None:
        assert MemoryCategory.KNOWLEDGE.value == "knowledge"

    def test_memory_category_round_trip(self) -> None:
        assert MemoryCategory("knowledge") is MemoryCategory.KNOWLEDGE

    def test_source_type_values(self) -> None:
        assert {s.value for s in SourceType} == {
            "pdf",
            "web",
            "repo",
            "ticket",
            "design_doc",
        }

    def test_content_kind_values(self) -> None:
        assert ContentKind.CODE.value == "code"
        assert ContentKind.PDF_PAGE.value == "pdf_page"

    def test_source_status_values(self) -> None:
        assert {s.value for s in SourceStatus} == {
            "pending",
            "indexed",
            "stale",
            "failed",
        }


class TestKnowledgeConfig:
    def test_defaults_disabled(self) -> None:
        cfg = KnowledgeConfig()
        assert cfg.enabled is False
        assert cfg.pdf_loader == "pdfplumber"
        assert cfg.code_chunker == "tree_sitter"

    def test_frozen(self) -> None:
        cfg = KnowledgeConfig()
        with pytest.raises(ValidationError):
            cfg.enabled = True  # type: ignore[misc]

    def test_rejects_unknown_loader(self) -> None:
        with pytest.raises(ValidationError):
            KnowledgeConfig(pdf_loader="pymupdf")  # type: ignore[arg-type]

    def test_rejects_extra_field(self) -> None:
        with pytest.raises(ValidationError):
            KnowledgeConfig(surprise=1)  # type: ignore[call-arg]


class TestErrors:
    def test_all_inherit_knowledge_error(self) -> None:
        for exc_cls in (
            KnowledgeSourceNotFoundError,
            KnowledgeValidationError,
            KnowledgeIngestError,
            KnowledgeRetrievalError,
            KnowledgeDependencyError,
            KnowledgeSourceUnavailableError,
        ):
            assert issubclass(exc_cls, KnowledgeError)

    def test_not_found_metadata(self) -> None:
        assert (
            KnowledgeSourceNotFoundError.error_code
            is ErrorCode.KNOWLEDGE_SOURCE_NOT_FOUND
        )
        assert KnowledgeSourceNotFoundError.error_category is ErrorCategory.NOT_FOUND
        assert KnowledgeSourceNotFoundError.status_code == 404

    def test_validation_metadata(self) -> None:
        assert KnowledgeValidationError.error_category is ErrorCategory.VALIDATION
        assert KnowledgeValidationError.status_code == 422

    def test_ingest_is_retryable(self) -> None:
        assert KnowledgeIngestError.retryable is True

    def test_dependency_not_retryable(self) -> None:
        assert KnowledgeDependencyError.retryable is False

    def test_source_unavailable_is_503(self) -> None:
        assert KnowledgeSourceUnavailableError.status_code == 503

    def test_catchable_as_family(self) -> None:
        with pytest.raises(KnowledgeError):
            raise KnowledgeIngestError
