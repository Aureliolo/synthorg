"""Memory error hierarchy.

All memory-related errors inherit from ``MemoryError`` so callers can
catch the entire family with a single except clause.

Note: this shadows the built-in ``MemoryError`` (which signals
out-of-memory conditions in CPython).  Within the ``synthorg``
namespace the domain-specific meaning is unambiguous; callers outside
the package should import explicitly.
"""

from typing import Final

from synthorg.core.domain_errors import DomainError


class MemoryError(DomainError):  # noqa: A001
    """Base exception for all memory operations.

    Inherits :class:`DomainError` so the prefix-vs-category validator
    runs on every subclass.
    Subclasses keep the inherited ``ErrorCode.INTERNAL_ERROR`` default
    (8000, ``INTERNAL`` category) -- callers that need a more specific
    code per memory failure mode should override the ClassVars on the
    subclass.

    ``is_retryable`` mirrors the sibling
    :class:`~synthorg.core.persistence_errors.PersistenceError` and
    provider hierarchies so a caller implementing bounded retry can
    branch on the flag rather than on the exception type, and so the
    RFC 9457 response tells an API client whether repeating the call is
    worth anything. Default: ``False``.
    """

    is_retryable: bool = False


class MemoryConnectionError(MemoryError):
    """Raised when a backend connection cannot be established or is lost.

    Retryable: a dropped connection or an exhausted pool usually clears
    on its own.
    """

    is_retryable: bool = True


class MemoryStoreError(MemoryError):
    """Raised when a store operation fails.

    Retryable: the underlying failure is a database write, which is
    transient far more often than not.
    """

    is_retryable: bool = True


class MemoryEmbeddingError(MemoryError):
    """Raised when a text-embedding call fails.

    Scoped to one batch: a wired, constructed embedder failed on a
    specific call, so recall degrades for that call rather than the
    backend being unusable. A binding that cannot be resolved at all
    raises :class:`MemoryConfigError` instead, and leaves memory off.

    Retryable: the common cause is a provider rate limit or timeout.
    """

    is_retryable: bool = True


class MemoryDenseSearchUnavailableError(MemoryError):
    """Raised when dense retrieval is required but the index is absent.

    SQLite loads ``sqlite-vec`` as a runtime extension. When it cannot be
    loaded, semantic recall is impossible; failing loudly here keeps that
    visible instead of silently degrading to lexical-only recall, which
    reads as "memory works" while quietly returning the wrong things.
    """


class MemoryRetrievalError(MemoryError):
    """Raised when a retrieve or search operation fails.

    Retryable: a read that failed on a transient store condition can
    succeed unchanged on the next attempt.
    """

    is_retryable: bool = True


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


#: How to get the fine-tune dependency set, in the two deployment shapes that
#: can have it. Lives beside the error whose message it is, because three
#: separate import guards across two modules quote it and a copy per guard is
#: how install instructions come to disagree with each other.
FINE_TUNE_DOCKER_DEP_HINT: Final[str] = (
    "In a Docker-orchestrated install the backend spawns an ephemeral "
    "synthorg-fine-tune-gpu (default) or synthorg-fine-tune-cpu container "
    "on demand. Enable without re-init: `synthorg config set sandbox true "
    "&& synthorg config set fine_tuning true && synthorg config set "
    "fine_tuning_variant gpu && synthorg stop && synthorg start` "
    "(replace `gpu` with `cpu` on non-NVIDIA hosts). For hand-managed "
    "compose deployments see "
    "https://synthorg.io/docs/guides/deployment/#fine-tuning-optional."
)
FINE_TUNE_INPROCESS_DEP_HINT: Final[str] = (
    "For in-process execution install the extras directly: "
    "`pip install 'synthorg[fine-tune-gpu]'` or "
    "`pip install 'synthorg[fine-tune-cpu]'`."
)


class FineTuneDependencyError(MemoryError):
    """Raised when fine-tuning ML dependencies are not installed.

    The dependency set is ``torch``, ``sentence-transformers[train]``,
    ``datasets``, ``accelerate`` and ``transformers``. The last three are
    load-bearing rather than incidental: ``datasets`` supplies the training
    table, ``accelerate`` the trainer's device handling, and neither is a
    dependency of ``sentence-transformers`` itself (both live in its ``train``
    extra), so an install that pinned the bare package imports cleanly and
    still cannot train.

    In the default Docker-orchestrated deployment all of it ships inside the
    ``synthorg-fine-tune-gpu`` / ``synthorg-fine-tune-cpu`` container
    that the backend spawns on demand; this error indicates the
    feature is turned off for the current install (`synthorg config
    set fine_tuning true` enables it).  The optional
    ``synthorg[fine-tune-gpu]`` / ``synthorg[fine-tune-cpu]`` extras
    only apply when running fine-tuning in-process (dev / testing).
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


class FineTuneTrainingDataError(MemoryError):
    """Raised when the triples handed to contrastive training are unusable.

    Training on nothing produces a checkpoint indistinguishable from the
    base model, which the promotion gate would then score and reject
    hours later without ever saying why. Failing here names the empty
    input instead.
    """
