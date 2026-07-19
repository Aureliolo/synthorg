"""Memory error hierarchy.

All memory-related errors inherit from ``MemoryError`` so callers can
catch the entire family with a single except clause.

Note: this shadows the built-in ``MemoryError`` (which signals
out-of-memory conditions in CPython).  Within the ``synthorg``
namespace the domain-specific meaning is unambiguous; callers outside
the package should import explicitly.
"""

from synthorg.core.domain_errors import DomainError


class MemoryError(DomainError):  # noqa: A001
    """Base exception for all memory operations.

    Inherits :class:`DomainError` so the prefix-vs-category validator
    runs on every subclass.
    Subclasses keep the inherited ``ErrorCode.INTERNAL_ERROR`` default
    (8000, ``INTERNAL`` category) -- callers that need a more specific
    code per memory failure mode should override the ClassVars on the
    subclass.
    """


class MemoryConnectionError(MemoryError):
    """Raised when a backend connection cannot be established or is lost."""


class MemoryStoreError(MemoryError):
    """Raised when a store operation fails."""


class MemoryEmbeddingError(MemoryError):
    """Raised when a text-embedding call fails.

    Distinct from :class:`MemoryEmbedderUnavailableError`, which means the
    embedder could not be constructed at all. This one means a wired,
    constructed embedder failed on a specific batch, so recall degrades
    for that call rather than the backend being unusable.
    """


class MemoryDenseSearchUnavailableError(MemoryError):
    """Raised when dense retrieval is required but the index is absent.

    SQLite loads ``sqlite-vec`` as a runtime extension. When it cannot be
    loaded, semantic recall is impossible; failing loudly here keeps that
    visible instead of silently degrading to lexical-only recall, which
    reads as "memory works" while quietly returning the wrong things.
    """


class MemoryRetrievalError(MemoryError):
    """Raised when a retrieve or search operation fails."""


class MemoryNotFoundError(MemoryError):
    """Raised when a specific memory ID is not found.

    Note: The ``MemoryBackend.get()`` protocol method returns ``None``
    for missing entries rather than raising this error.  This exception
    is available for concrete backend implementations that need to
    signal "not found" in non-protocol internal methods or batch
    operations.
    """


class MemoryConfigError(MemoryError):
    """Raised when memory configuration is invalid."""


class MemoryCapabilityError(MemoryError):
    """Raised when an unsupported operation is attempted for a backend."""


class FineTuneDependencyError(MemoryError):
    """Raised when fine-tuning ML dependencies are not installed.

    In the default Docker-orchestrated deployment ``torch`` and
    ``sentence-transformers`` ship inside the
    ``synthorg-fine-tune-gpu`` / ``synthorg-fine-tune-cpu`` container
    that the backend spawns on demand; this error indicates the
    feature is turned off for the current install (`synthorg config
    set fine_tuning true` enables it).  The optional
    ``synthorg[fine-tune-gpu]`` / ``synthorg[fine-tune-cpu]`` extras
    only apply when running fine-tuning in-process (dev / testing).
    """


class MemoryEmbedderUnavailableError(MemoryError):
    """Raised when a neural text embedder's optional extra is not installed.

    The ``sentence_transformer`` embedder backs the optional
    ``sentence-transformers`` extra; this signals the extra is absent so a
    caller can degrade to the dependency-free hashing embedder (or surface
    its own layer-specific error).
    """


class FineTuneCancelledError(MemoryError):
    """Raised when a fine-tuning pipeline run is cancelled."""


class FineTuneStageExecutionError(MemoryError):
    """Raised when a torch-bound pipeline stage fails to execute.

    Covers both execution backends: an in-process stage function that
    raised, and an ephemeral stage container that exited non-zero,
    timed out, or could not be launched.
    """


class FineTuneDataSourceError(MemoryError):
    """Raised when the selected finetune training-data source is unavailable.

    Trajectory mode requires a wired :class:`TrainingDataSource`; this signals
    the run was started in trajectory mode without one.
    """
