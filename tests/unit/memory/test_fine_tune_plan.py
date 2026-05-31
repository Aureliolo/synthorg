"""Unit tests for :class:`FineTunePlan` + :class:`MemoryBackendUnsupportedError`.

``FineTunePlan`` is the MCP-facing plan that shields the public
contract from the runner's internal :class:`FineTuneRequest` type.
Both models share the same path-traversal rejection invariants, so
the tests here focus on the MCP boundary: validator coverage,
``to_request`` round-trip fidelity, and the typed error's
``domain_code`` contract.
"""

import pytest
from pydantic import ValidationError

from synthorg.core.types import NotBlankStr
from synthorg.memory.embedding.fine_tune_models import (
    FineTuneDataSourceType,
    FineTuneExecutionConfig,
    FineTuneRequest,
)
from synthorg.memory.fine_tune_plan import (
    ActiveEmbedderSnapshot,
    FineTunePlan,
    MemoryBackendUnsupportedError,
)

pytestmark = pytest.mark.unit


class TestFineTunePlan:
    """Construction + validation + to_request round trip."""

    def test_minimal_construction(self) -> None:
        plan = FineTunePlan(source_dir=NotBlankStr("/data/org-docs"))
        assert plan.source_dir == "/data/org-docs"
        assert plan.data_source is FineTuneDataSourceType.DIRECTORY
        assert plan.base_model is None
        assert plan.output_dir is None
        assert plan.resume_run_id is None
        assert plan.epochs is None
        assert plan.execution is None

    def test_default_data_source_is_directory(self) -> None:
        """Omitting ``data_source`` defaults to directory mode."""
        plan = FineTunePlan(source_dir=NotBlankStr("/data/org-docs"))
        assert plan.data_source is FineTuneDataSourceType.DIRECTORY

    def test_trajectory_mode_omits_source_dir(self) -> None:
        """Trajectory mode harvests org history, so ``source_dir`` is optional."""
        plan = FineTunePlan(data_source=FineTuneDataSourceType.TRAJECTORY)
        assert plan.data_source is FineTuneDataSourceType.TRAJECTORY
        assert plan.source_dir is None

    def test_directory_mode_requires_source_dir(self) -> None:
        """Directory mode without a ``source_dir`` is rejected."""
        with pytest.raises(ValidationError) as info:
            FineTunePlan(data_source=FineTuneDataSourceType.DIRECTORY)
        assert "source_dir is required" in str(info.value)

    def test_to_request_forwards_trajectory_data_source(self) -> None:
        """``to_request`` carries trajectory mode (and a ``None`` source_dir)."""
        plan = FineTunePlan(data_source=FineTuneDataSourceType.TRAJECTORY)
        request = plan.to_request()
        assert request.data_source is FineTuneDataSourceType.TRAJECTORY
        assert request.source_dir is None

    def test_to_request_defaults_directory_data_source(self) -> None:
        """A directory-mode plan forwards ``DIRECTORY`` to the runner request."""
        plan = FineTunePlan(source_dir=NotBlankStr("/data/org-docs"))
        request = plan.to_request()
        assert request.data_source is FineTuneDataSourceType.DIRECTORY
        assert request.source_dir == "/data/org-docs"

    def test_full_construction(self) -> None:
        execution = FineTuneExecutionConfig(
            backend="docker",
            image=NotBlankStr("synthorg-fine-tune-gpu"),
            gpu_enabled=True,
            memory_limit=NotBlankStr("16g"),
            timeout_seconds=3600.0,
        )
        plan = FineTunePlan(
            source_dir=NotBlankStr("/data/org-docs"),
            base_model=NotBlankStr("test-small-001"),
            output_dir=NotBlankStr("/data/fine-tune/v1"),
            resume_run_id=NotBlankStr("run-42"),
            epochs=5,
            learning_rate=2e-5,
            temperature=0.03,
            top_k=8,
            batch_size=64,
            validation_split=0.15,
            execution=execution,
        )
        assert plan.epochs == 5
        assert plan.validation_split == 0.15
        assert plan.execution is execution

    @pytest.mark.parametrize(
        "field_name",
        ["source_dir", "output_dir"],
    )
    def test_rejects_parent_traversal(self, field_name: str) -> None:
        kwargs: dict[str, object] = {
            "source_dir": NotBlankStr("/data/org-docs"),
        }
        kwargs[field_name] = NotBlankStr("/data/../etc")
        with pytest.raises(ValidationError) as info:
            FineTunePlan(**kwargs)  # type: ignore[arg-type]
        assert "parent-directory traversal" in str(info.value)

    @pytest.mark.parametrize(
        "bad_value",
        [
            r"C:\Users\attacker\docs",
            "/data/org\\docs",
        ],
    )
    def test_rejects_windows_paths(self, bad_value: str) -> None:
        with pytest.raises(ValidationError) as info:
            FineTunePlan(source_dir=NotBlankStr(bad_value))
        assert "POSIX path" in str(info.value)

    @pytest.mark.parametrize(
        ("field_name", "value"),
        [
            ("epochs", 0),
            ("learning_rate", 0.0),
            ("temperature", 0.0),
            ("top_k", 0),
            ("batch_size", 0),
            ("validation_split", 0.0),
            ("validation_split", 1.0),
        ],
    )
    def test_numeric_bound_rejections(
        self,
        field_name: str,
        value: float,
    ) -> None:
        with pytest.raises(ValidationError):
            FineTunePlan(
                source_dir=NotBlankStr("/data/org-docs"),
                **{field_name: value},  # type: ignore[arg-type]
            )

    def test_frozen(self) -> None:
        plan = FineTunePlan(source_dir=NotBlankStr("/data/org-docs"))
        with pytest.raises(ValidationError):
            plan.source_dir = NotBlankStr("/data/replaced")  # type: ignore[misc]

    def test_to_request_preserves_overrides(self) -> None:
        plan = FineTunePlan(
            source_dir=NotBlankStr("/data/org-docs"),
            base_model=NotBlankStr("test-small-001"),
            epochs=5,
            learning_rate=2e-5,
            batch_size=64,
        )
        request = plan.to_request()
        assert isinstance(request, FineTuneRequest)
        assert request.source_dir == plan.source_dir
        assert request.base_model == plan.base_model
        assert request.epochs == 5
        assert request.learning_rate == 2e-5
        assert request.batch_size == 64
        assert request.resume_run_id is None

    def test_to_request_drops_execution_field(self) -> None:
        """``execution`` is MCP-only; the runner request does not carry it."""
        plan = FineTunePlan(
            source_dir=NotBlankStr("/data/org-docs"),
            execution=FineTuneExecutionConfig(),
        )
        request = plan.to_request()
        assert not hasattr(request, "execution")


class TestMemoryBackendUnsupportedError:
    """Typed exception carrying ``domain_code="not_supported"``."""

    def test_domain_code_is_not_supported(self) -> None:
        exc = MemoryBackendUnsupportedError("postgres backend lacks fine-tune repos")
        assert exc.domain_code == "not_supported"

    def test_reason_preserved(self) -> None:
        reason = "postgres backend lacks fine-tune repos"
        exc = MemoryBackendUnsupportedError(reason)
        assert exc.reason == reason
        assert str(exc) == reason

    def test_is_exception_subclass(self) -> None:
        msg = "msg"
        with pytest.raises(MemoryBackendUnsupportedError):
            raise MemoryBackendUnsupportedError(msg)

    def test_is_retryable_is_false(self) -> None:
        """The error is deterministic; the provider-retry layer must surface it."""
        exc = MemoryBackendUnsupportedError("backend cannot support this")
        assert exc.is_retryable is False

    @pytest.mark.parametrize(
        "bad_reason",
        ["", " ", "\t", "\n", "   \t\n  "],
        ids=[
            "empty_string",
            "single_space",
            "tab",
            "newline",
            "mixed_whitespace",
        ],
    )
    def test_empty_or_whitespace_reason_rejected(self, bad_reason: str) -> None:
        """Reason must carry operator-actionable text; blank is rejected."""
        with pytest.raises(ValueError, match="non-empty"):
            MemoryBackendUnsupportedError(bad_reason)


class TestActiveEmbedderSnapshot:
    """Frozen read-only snapshot."""

    def test_default_fields_are_none(self) -> None:
        snap = ActiveEmbedderSnapshot(read_from_settings=False)
        assert snap.provider is None
        assert snap.model is None
        assert snap.checkpoint_id is None
        assert snap.read_from_settings is False

    def test_populated_fields(self) -> None:
        snap = ActiveEmbedderSnapshot(
            provider=NotBlankStr("local"),
            model=NotBlankStr("local/models/ckpt-42"),
            checkpoint_id=NotBlankStr("ckpt-42"),
            read_from_settings=True,
        )
        assert snap.provider == "local"
        assert snap.model == "local/models/ckpt-42"
        assert snap.checkpoint_id == "ckpt-42"
        assert snap.read_from_settings is True

    def test_frozen(self) -> None:
        snap = ActiveEmbedderSnapshot(read_from_settings=False)
        with pytest.raises(ValidationError):
            snap.provider = NotBlankStr("local")  # type: ignore[misc]
