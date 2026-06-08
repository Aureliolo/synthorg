"""Tests for content density classification."""

from datetime import UTC, datetime

import pytest

from synthorg.core.memory_enums import MemoryCategory
from synthorg.memory.consolidation.density import ContentDensity, DensityClassifier
from synthorg.memory.models import MemoryEntry, MemoryMetadata


@pytest.mark.unit
class TestContentDensity:
    """ContentDensity enum values."""

    def test_values(self) -> None:
        assert ContentDensity.SPARSE.value == "sparse"
        assert ContentDensity.DENSE.value == "dense"

    def test_is_str(self) -> None:
        assert isinstance(ContentDensity.SPARSE, str)


@pytest.mark.unit
class TestDensityClassifierSparse:
    """DensityClassifier identifies sparse/conversational content."""

    def test_simple_conversation(self) -> None:
        classifier = DensityClassifier()
        result = classifier.classify("Hello, how are you today?")
        assert result == ContentDensity.SPARSE

    def test_narrative_text(self) -> None:
        classifier = DensityClassifier()
        text = (
            "We discussed the project timeline and agreed on next steps. "
            "The team decided to prioritize the authentication module "
            "before moving on to the dashboard implementation."
        )
        result = classifier.classify(text)
        assert result == ContentDensity.SPARSE

    def test_opinion_text(self) -> None:
        classifier = DensityClassifier()
        text = (
            "I think we should use a different approach for the caching "
            "layer. The current implementation feels too complex and "
            "might cause issues during peak traffic."
        )
        result = classifier.classify(text)
        assert result == ContentDensity.SPARSE


@pytest.mark.unit
class TestDensityClassifierDense:
    """DensityClassifier identifies dense/factual content."""

    def test_python_code(self) -> None:
        classifier = DensityClassifier()
        text = (
            "def calculate_total(items):\n"
            "    total = sum(item.price for item in items)\n"
            "    return total * 1.08  # tax\n"
        )
        result = classifier.classify(text)
        assert result == ContentDensity.DENSE

    def test_json_data(self) -> None:
        classifier = DensityClassifier()
        text = '{"user_id": "abc-123", "status": "active", "score": 0.95}'
        result = classifier.classify(text)
        assert result == ContentDensity.DENSE

    def test_structured_config(self) -> None:
        classifier = DensityClassifier()
        text = (
            "host: 192.168.1.100\n"
            "port: 5432\n"
            "database: synthorg_prod\n"
            "max_connections: 50\n"
            "timeout: 30s\n"
        )
        result = classifier.classify(text)
        assert result == ContentDensity.DENSE

    def test_identifiers_and_hashes(self) -> None:
        classifier = DensityClassifier()
        text = (
            "commit: a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2\n"
            "uuid: 550e8400-e29b-41d4-a716-446655440000\n"
            "url: https://api.example.com/v2/users/12345\n"
        )
        result = classifier.classify(text)
        assert result == ContentDensity.DENSE


@pytest.mark.unit
class TestDensityClassifierThreshold:
    """DensityClassifier respects threshold parameter."""

    def test_low_threshold_classifies_more_as_dense(self) -> None:
        """Threshold=0.0 classifies (nearly) everything as dense."""
        classifier = DensityClassifier(dense_threshold=0.01)
        result = classifier.classify("Some partially structured content: key=value")
        assert result == ContentDensity.DENSE

    def test_high_threshold_classifies_more_as_sparse(self) -> None:
        """Threshold=1.0 classifies everything as sparse."""
        classifier = DensityClassifier(dense_threshold=1.0)
        result = classifier.classify('{"key": "value"}')
        assert result == ContentDensity.SPARSE

    def test_invalid_threshold_rejected(self) -> None:
        with pytest.raises(ValueError, match="dense_threshold"):
            DensityClassifier(dense_threshold=-0.1)
        with pytest.raises(ValueError, match="dense_threshold"):
            DensityClassifier(dense_threshold=1.1)

    def test_empty_string_is_sparse(self) -> None:
        """Empty content classifies as SPARSE without error."""
        classifier = DensityClassifier()
        assert classifier.classify("") == ContentDensity.SPARSE


@pytest.mark.unit
class TestDensityClassifierBatch:
    """DensityClassifier.classify_batch behaviour."""

    def test_empty_batch(self) -> None:
        classifier = DensityClassifier()
        result = classifier.classify_batch(())
        assert result == ()

    def test_batch_returns_paired_results(self) -> None:
        classifier = DensityClassifier()
        now = datetime.now(UTC)
        entries = (
            MemoryEntry(
                id="sparse-1",
                agent_id="agent-1",
                category=MemoryCategory.EPISODIC,
                content="We had a great meeting about the roadmap",
                metadata=MemoryMetadata(),
                created_at=now,
            ),
            MemoryEntry(
                id="dense-1",
                agent_id="agent-1",
                category=MemoryCategory.PROCEDURAL,
                content='{"config": {"port": 8080}, "version": "2.1.0"}',
                metadata=MemoryMetadata(),
                created_at=now,
            ),
        )
        result = classifier.classify_batch(entries)
        assert len(result) == 2
        assert result[0][0].id == "sparse-1"
        assert result[0][1] == ContentDensity.SPARSE
        assert result[1][0].id == "dense-1"
        assert result[1][1] == ContentDensity.DENSE
