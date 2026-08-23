# module-kind: adapter
"""Contrastive-training adapter over the sentence-transformers trainer API.

Every symbol stage 3 needs is resolved here, by module path, in one place.
That is load-bearing rather than tidy: ``sentence_transformers`` binds
``SentenceTransformer`` and the trainer classes on the package but does NOT
bind ``losses`` or ``datasets`` as attributes, and its compatibility shim for
those top-level paths is a ``sys.meta_path`` finder, which fires on an
``import`` statement and never on attribute access. Reaching them off the
package object raises ``AttributeError`` on the pinned version.

``datasets`` and ``accelerate`` are absent from sentence-transformers' own
dependency list (they live in its ``train`` extra), so the trainer is
unavailable on an install that pinned the bare package. Resolution therefore
either yields every symbol or raises ``FineTuneDependencyError``; there is no
partial success, and both dependency probes ask this question rather than the
narrower "does the package import".

The dataset side is columnar, one feature dict per column, so rows carrying
different numbers of hard negatives cannot share a table. Hard-negative mining
emits between zero and ``top_k`` of them per query because its similarity
margin can starve a row, so triples are bucketed by negative count into a
multi-dataset training set. Every row trains, at its own hardness, and nothing
mining paid for is discarded.

Cancellation is checked on every step rather than on an interval. An interval
buys a ``threading.Event`` flag read against a forward and backward pass, which
is not a trade worth making, and a run with fewer steps than the interval never
reaches a multiple of it, so it cannot be cancelled at all.
"""

import math
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final, NoReturn

from pydantic import BaseModel, ConfigDict, Field

from synthorg.memory.embedding.cancellation import (
    CancellationToken,
    ProgressCallback,
)
from synthorg.memory.errors import (
    FINE_TUNE_DOCKER_DEP_HINT,
    FINE_TUNE_INPROCESS_DEP_HINT,
    FineTuneDependencyError,
    FineTuneTrainingDataError,
)
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.memory import (
    MEMORY_FINE_TUNE_DEPENDENCY_MISSING,
    MEMORY_FINE_TUNE_MODEL_CARD_DISABLED,
    MEMORY_FINE_TUNE_NO_HARD_NEGATIVES,
    MEMORY_FINE_TUNE_TRAINER_API_RESOLVED,
    MEMORY_FINE_TUNE_TRAINING_BUCKETED,
    MEMORY_FINE_TUNE_TRAINING_COMPLETED,
    MEMORY_FINE_TUNE_TRAINING_STARTED,
    MEMORY_FINE_TUNE_VALIDATION_FAILED,
)

logger = get_logger(__name__)

#: Warmup ramps over a tenth of the run so a short run still gets a
#: proportionate ramp, capped so a very long one does not warm up for ever.
_WARMUP_STEP_CAP: Final[int] = 100
_WARMUP_FRACTION_DIVISOR: Final[int] = 10

#: Column names are arbitrary to the loss, which reads position, but they must
#: avoid ``label`` / ``labels`` / ``score`` / ``scores``: the trainer treats
#: those four as targets and would drop the column from the inputs entirely.
_ANCHOR_COLUMN: Final[str] = "anchor"
_POSITIVE_COLUMN: Final[str] = "positive"
_NEGATIVE_COLUMN_PREFIX: Final[str] = "negative_"

#: Stage 2 writes both on every record, so a record without them is damage.
_REQUIRED_TRIPLE_FIELDS: Final[tuple[str, ...]] = ("query", "positive")

#: Dataset names carry their negative count so a log line names what trained.
_BUCKET_NAME_PREFIX: Final[str] = "negatives_"

#: Left unset, transformers defaults its output directory to a relative path in
#: the process working directory, which for the backend is not the run's tree.
TRAINER_OUTPUT_SUBDIR: Final[str] = "trainer_output"

#: Keyed by hard-negative count rather than by the vendor-facing dataset name,
#: so ordering the buckets is a property of the key instead of a string parse.
type _TrainingRow = dict[str, str]
type _TrainingBuckets = dict[int, list[_TrainingRow]]

#: The vendor's own types are invisible to the checker: the packages are an
#: optional extra that the default sync never installs, and they ship no
#: stubs. Nothing held under this alias is inspected structurally here; each
#: value is handed straight back to the library that produced it, so the
#: annotation would carry no information even if the types were available.
_Vendor = Any  # type: ignore[explicit-any]


class ContrastiveHyperparameters(BaseModel):
    """What the operator configured for one contrastive run.

    Travels as one value because the four move together: the stage reads
    them from a single `FineTuneRunConfig`, and every consumer below wants
    all four or none.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    epochs: int = Field(ge=1)
    learning_rate: float = Field(gt=0.0)
    #: InfoNCE tau. Its reciprocal is the loss's similarity scale, so zero is
    #: not merely invalid, it is undefined.
    temperature: float = Field(gt=0.0)
    batch_size: int = Field(ge=1)


@dataclass(frozen=True, slots=True)
class TrainerApi:
    """The sentence-transformers training symbols, resolved once.

    Held as a value rather than imported at call sites so the whole vendor
    surface has exactly one resolution point, and so a caller cannot reach a
    symbol that ``import_trainer_api`` never proved reachable.
    """

    dataset_cls: type[_Vendor]
    loss_cls: type[_Vendor]
    trainer_cls: type[_Vendor]
    args_cls: type[_Vendor]
    callback_cls: type[_Vendor]
    batch_samplers: type[_Vendor]
    multi_dataset_batch_samplers: type[_Vendor]


def import_trainer_api() -> TrainerApi:
    """Resolve every training symbol, or fail naming the missing extra.

    Returns:
        The resolved training API.

    Raises:
        FineTuneDependencyError: If any part of the fine-tune extra is absent
            or broken. The net is wider than ``ImportError`` because a
            half-installed stack does not report itself that way: transformers
            resolves its submodules lazily and re-raises the underlying cause
            as ``RuntimeError`` (its tokenizers version assertion arrives that
            way), and a torch build whose native extension will not load
            surfaces as ``OSError``. Both would otherwise escape untyped and
            reach an operator with none of the install guidance below.
    """
    # Each import carries its own unresolvable-module suppression because the
    # extra is absent from the type-check environment by design. mypy is told
    # the same fact once, in its ``ignore_missing_imports`` overrides; pyright
    # has no per-module equivalent, so the claim is made at each site.
    try:
        from datasets import (  # noqa: PLC0415  # pyright: ignore[reportMissingImports]
            Dataset,
        )
        from sentence_transformers import (  # noqa: PLC0415  # pyright: ignore[reportMissingImports]
            SentenceTransformerTrainer,
            SentenceTransformerTrainingArguments,
        )
        from sentence_transformers.base.sampler import (  # noqa: PLC0415  # pyright: ignore[reportMissingImports]
            BatchSamplers,
            MultiDatasetBatchSamplers,
        )
        from sentence_transformers.sentence_transformer.losses import (  # noqa: PLC0415  # pyright: ignore[reportMissingImports]
            MultipleNegativesRankingLoss,
        )
        from transformers import (  # noqa: PLC0415  # pyright: ignore[reportMissingImports]
            TrainerCallback,
        )
    except (ImportError, RuntimeError, OSError) as exc:
        _trainer_dependency_missing(exc)
    else:
        # Which versions actually loaded. A training failure hours later is
        # routinely a version skew, and this is the only record of what the
        # deployment resolved at the time.
        logger.debug(
            MEMORY_FINE_TUNE_TRAINER_API_RESOLVED,
            sentence_transformers=_module_version("sentence_transformers"),
            transformers=_module_version("transformers"),
            datasets=_module_version("datasets"),
        )
        return TrainerApi(
            dataset_cls=Dataset,
            loss_cls=MultipleNegativesRankingLoss,
            trainer_cls=SentenceTransformerTrainer,
            args_cls=SentenceTransformerTrainingArguments,
            callback_cls=TrainerCallback,
            batch_samplers=BatchSamplers,
            multi_dataset_batch_samplers=MultiDatasetBatchSamplers,
        )


def _trainer_dependency_missing(exc: Exception) -> NoReturn:
    """Report an unusable training stack and fail with install guidance.

    Args:
        exc: Why the resolution failed.

    Raises:
        FineTuneDependencyError: Always.
    """
    msg = (
        "sentence-transformers[train] (with datasets and accelerate) is "
        f"required for contrastive fine-tuning. {FINE_TUNE_DOCKER_DEP_HINT} "
        f"{FINE_TUNE_INPROCESS_DEP_HINT}"
    )
    logger.warning(
        MEMORY_FINE_TUNE_DEPENDENCY_MISSING,
        package="sentence-transformers[train]",
        # Which of the six imports broke, and how. Without these every
        # failure in this branch logs identically.
        missing_module=getattr(exc, "name", None),
        error_type=type(exc).__name__,
        error=safe_error_description(exc),
    )
    raise FineTuneDependencyError(msg) from exc


def _module_version(name: str) -> str:
    """Report an installed distribution's version for a diagnostic log line.

    Returns:
        The version, or ``"unknown"`` when the metadata is unreadable.
    """
    from importlib.metadata import PackageNotFoundError, version  # noqa: PLC0415

    try:
        return version(name)
    except PackageNotFoundError:
        return "unknown"


def _reject_corrupt_triple(reason: str, *, index: int, field: str) -> NoReturn:
    """Refuse a triple stage 2 could not have written.

    Args:
        reason: What is wrong with the record.
        index: Its position in the triples file.
        field: The offending field name.

    Raises:
        FineTuneTrainingDataError: Always.
    """
    msg = f"training triple at index {index} {reason}; stage 2 output is corrupt"
    logger.warning(
        MEMORY_FINE_TUNE_VALIDATION_FAILED,
        field=field,
        record_index=index,
    )
    raise FineTuneTrainingDataError(msg)


def _negatives_of(triple: Mapping[str, object], *, index: int) -> list[str]:
    """Read a triple's hard negatives as text.

    Args:
        triple: One stage 2 record.
        index: Its position in the triples file, for the error message.

    Returns:
        The negatives, empty when the field is absent. An absent field is
        ordinary: mining's similarity margin can starve a query.

    Raises:
        FineTuneTrainingDataError: If ``negatives`` is present but not a list.
            Stage 2 writes a list or nothing, so any other shape means the
            file was damaged after it was written, and silently reading it as
            "no negatives" would train a degraded checkpoint that only shows
            up as a weak score hours later at the promotion gate.
    """
    if "negatives" not in triple:
        return []
    negatives = triple["negatives"]
    if not isinstance(negatives, list):
        _reject_corrupt_triple(
            f"has a non-list 'negatives' field ({type(negatives).__name__})",
            index=index,
            field="negatives",
        )
    return [str(negative) for negative in negatives]


def bucket_triples(triples: list[dict[str, object]]) -> _TrainingBuckets:
    """Group triples into rectangular row sets, one per negative count.

    Args:
        triples: Stage 2 output; each carries a query, its positive passage,
            and zero or more mined hard negatives.

    Returns:
        Buckets keyed by negative count, each holding rows whose columns run
        anchor, positive, then one column per negative in mined order.

    Raises:
        FineTuneTrainingDataError: If the triples are empty or malformed.
    """
    if not triples:
        msg = (
            "contrastive fine-tuning received no training triples; stage 2 "
            "produced an empty file"
        )
        logger.warning(MEMORY_FINE_TUNE_VALIDATION_FAILED, field="triples")
        raise FineTuneTrainingDataError(msg)

    buckets: _TrainingBuckets = {}
    for index, triple in enumerate(triples):
        missing = [field for field in _REQUIRED_TRIPLE_FIELDS if field not in triple]
        if missing:
            _reject_corrupt_triple(
                f"is missing field(s) {missing}", index=index, field="training_triple"
            )
        negatives = _negatives_of(triple, index=index)
        row: _TrainingRow = {
            _ANCHOR_COLUMN: str(triple["query"]),
            _POSITIVE_COLUMN: str(triple["positive"]),
        }
        for position, negative in enumerate(negatives, start=1):
            row[f"{_NEGATIVE_COLUMN_PREFIX}{position}"] = negative
        buckets.setdefault(len(negatives), []).append(row)

    return {count: buckets[count] for count in sorted(buckets)}


def bucket_name(negative_count: int) -> str:
    """Name the dataset the trainer trains on for *negative_count* negatives.

    Returns:
        The bucket's vendor-facing dataset name.
    """
    return f"{_BUCKET_NAME_PREFIX}{negative_count}"


def build_training_datasets(
    api: TrainerApi,
    buckets: _TrainingBuckets,
) -> dict[str, _Vendor]:
    """Turn bucketed rows into one dataset per hard-negative count.

    Args:
        api: The resolved training API.
        buckets: Output of :func:`bucket_triples`.

    Returns:
        Datasets keyed by bucket name, ready for the trainer's multi-dataset
        training set.
    """
    logger.info(
        MEMORY_FINE_TUNE_TRAINING_BUCKETED,
        bucket_count=len(buckets),
        row_count=sum(len(rows) for rows in buckets.values()),
        buckets={bucket_name(count): len(rows) for count, rows in buckets.items()},
    )
    if set(buckets) == {0}:
        # Every row lost its negatives to the mining margin, so training falls
        # back to in-batch negatives alone. That still trains, and still scores,
        # so nothing downstream would ever say stage 2 delivered nothing usable.
        logger.warning(
            MEMORY_FINE_TUNE_NO_HARD_NEGATIVES,
            row_count=sum(len(rows) for rows in buckets.values()),
        )
    return {
        # Column order follows the row keys, which is what the loss reads.
        bucket_name(count): api.dataset_cls.from_list(rows)
        for count, rows in buckets.items()
    }


def warmup_steps_for(
    buckets: Mapping[int, list[_TrainingRow]],
    *,
    batch_size: int,
    epochs: int,
) -> int:
    """Compute the linear-warmup step count for a bucketed training set.

    Proportional multi-dataset sampling draws each batch from a single
    bucket, so an epoch is the sum of the buckets' own batch counts rather
    than one division over the whole row set.

    Args:
        buckets: The bucketed rows.
        batch_size: Rows per training batch.
        epochs: Passes over the training set.

    Returns:
        Warmup steps, a tenth of the run capped at ``_WARMUP_STEP_CAP``.
    """
    steps_per_epoch = sum(
        math.ceil(len(rows) / batch_size) for rows in buckets.values()
    )
    total_steps = steps_per_epoch * epochs
    return min(_WARMUP_STEP_CAP, total_steps // _WARMUP_FRACTION_DIVISOR)


def loss_scale_for(temperature: float) -> float:
    """Convert an InfoNCE temperature into the loss's similarity scale.

    Returns:
        The reciprocal of *temperature*.
    """
    return 1.0 / temperature


def build_training_arguments(
    api: TrainerApi,
    *,
    trainer_output_dir: Path,
    hyperparameters: ContrastiveHyperparameters,
    warmup_steps: int,
) -> _Vendor:
    """Assemble the trainer's arguments for one contrastive run.

    Args:
        api: The resolved training API.
        trainer_output_dir: Where the trainer may write its own scratch state.
        hyperparameters: What the operator configured for this run.
        warmup_steps: Linear-warmup steps.

    Returns:
        The populated training arguments.
    """
    return api.args_cls(
        output_dir=str(trainer_output_dir),
        num_train_epochs=hyperparameters.epochs,
        learning_rate=hyperparameters.learning_rate,
        per_device_train_batch_size=hyperparameters.batch_size,
        warmup_steps=warmup_steps,
        # The sampler upstream documents for in-batch-negative losses: every
        # other row's positive doubles as a negative for this row, so a batch
        # that repeated a pair would score a text against itself.
        batch_sampler=api.batch_samplers.NO_DUPLICATES,
        # Draws each batch from one bucket, which is what keeps a batch's
        # column count uniform now that buckets differ in width.
        multi_dataset_batch_sampler=api.multi_dataset_batch_samplers.PROPORTIONAL,
        # The checkpoint this stage promotes is the one it saves itself, after
        # training returns. Left at its default the trainer would additionally
        # litter periodic checkpoints the promotion gate never reads.
        save_strategy="no",
        eval_strategy="no",
        # Already the upstream default, passed anyway: the alternative value
        # auto-detects installed reporting integrations, so a future transitive
        # could otherwise start shipping training telemetry off-box silently.
        report_to="none",
        disable_tqdm=True,
    )


class _ProgressReporter:
    """Reports progress and interrupts a cancelled run.

    Holds the two hooks rather than closing over them so the callback class
    itself carries no state, and so this half is testable without the vendor
    base class: the trainer only ever calls it through the two methods below.
    """

    __slots__ = ("_cancellation", "_progress_callback")

    def __init__(
        self,
        *,
        progress_callback: ProgressCallback | None,
        cancellation: CancellationToken | None,
    ) -> None:
        self._progress_callback = progress_callback
        self._cancellation = cancellation

    def check_cancelled(self) -> None:
        """Interrupt the run if cancellation was requested.

        Raises:
            FineTuneCancelledError: If cancellation was requested. Raised
                rather than setting ``control.should_training_stop``, which
                would end the run cleanly and hand back a checkpoint nobody
                asked for.
        """
        if self._cancellation is not None:
            self._cancellation.check(stage="contrastive training")

    def report(self, state: _Vendor) -> None:
        """Check cancellation, then report how far the run has got."""
        self.check_cancelled()
        if self._progress_callback is not None and state.max_steps > 0:
            self._progress_callback(min(state.global_step / state.max_steps, 1.0))


def build_progress_callback(
    api: TrainerApi,
    *,
    progress_callback: ProgressCallback | None,
    cancellation: CancellationToken | None,
) -> _Vendor:
    """Build the trainer callback carrying progress and cancellation.

    Progress is read from the trainer's own step counters rather than tallied
    here, so it stays monotonic across every bucket and epoch by construction.

    Args:
        api: The resolved training API.
        progress_callback: Called with a fraction in ``0.0..1.0``, or ``None``.
        cancellation: Checked before the run and after every step, or ``None``.

    Returns:
        A ``TrainerCallback`` instance for the trainer's callback list.
    """
    reporter = _ProgressReporter(
        progress_callback=progress_callback, cancellation=cancellation
    )

    class _TrainingProgressCallback(api.callback_cls):  # type: ignore[misc, name-defined]
        """Adapts the reporter to the vendor's callback protocol."""

        def on_train_begin(
            self,
            args: object,  # noqa: ARG002
            state: _Vendor,  # noqa: ARG002
            control: object,  # noqa: ARG002
            **kwargs: object,  # noqa: ARG002
        ) -> None:
            """Refuse to start a run that was already cancelled."""
            reporter.check_cancelled()

        def on_step_end(
            self,
            args: object,  # noqa: ARG002
            state: _Vendor,
            control: object,  # noqa: ARG002
            **kwargs: object,  # noqa: ARG002
        ) -> None:
            """Check cancellation, then report progress."""
            reporter.report(state)

    return _TrainingProgressCallback()


def run_biencoder_training(
    *,
    api: TrainerApi,
    model: object,
    triples: list[dict[str, object]],
    trainer_output_dir: Path,
    hyperparameters: ContrastiveHyperparameters,
    progress_callback: ProgressCallback | None,
    cancellation: CancellationToken | None,
) -> None:
    """Train *model* contrastively on *triples* until the run completes.

    Blocking and compute-bound; callers dispatch it off the event loop.

    Args:
        api: The resolved training API.
        model: The loaded ``SentenceTransformer`` to fine-tune in place.
        triples: Stage 2 output.
        trainer_output_dir: Where the trainer may write its own scratch state.
        hyperparameters: What the operator configured for this run.
        progress_callback: Called with a fraction in ``0.0..1.0``, or ``None``.
        cancellation: Checked before the run and after every step, or ``None``.

    Raises:
        FineTuneTrainingDataError: If the triples are empty or malformed.
        FineTuneCancelledError: If cancellation fires before or during the
            run. Raised from the trainer callback and propagated unchanged.
    """
    buckets = bucket_triples(triples)
    trainer = _build_trainer(
        api=api,
        model=model,
        buckets=buckets,
        trainer_output_dir=trainer_output_dir,
        hyperparameters=hyperparameters,
        progress_callback=progress_callback,
        cancellation=cancellation,
    )
    logger.info(
        MEMORY_FINE_TUNE_TRAINING_STARTED,
        bucket_count=len(buckets),
        row_count=sum(len(rows) for rows in buckets.values()),
        epochs=hyperparameters.epochs,
        batch_size=hyperparameters.batch_size,
        learning_rate=hyperparameters.learning_rate,
        temperature=hyperparameters.temperature,
        warmup_steps=trainer.args.warmup_steps,
    )
    trainer.train()
    logger.info(MEMORY_FINE_TUNE_TRAINING_COMPLETED, bucket_count=len(buckets))


def card_free_trainer_class(api: TrainerApi) -> type[_Vendor]:
    """Subclass the trainer so its model-card callback never attaches.

    The trainer adds that callback in its own constructor, and it does two
    things this pipeline wants no part of, both DURING training and so both
    long before anything is saved. It samples the training rows to build
    "widget examples", which are excerpts of the organisation's own documents
    held for a document built to travel with the checkpoint. And it asks
    huggingface.co about the base model, which for a local checkpoint path
    sends that path to a third party to answer a question with no answer, and
    on a host with no egress makes the run wait for the request to fail.

    Removing the callback by overriding ``add_model_card_callback`` is what
    upstream documents for this ("can be overridden by subclassing the trainer
    to remove/customize this callback"), so it is an extension point rather
    than a reach into internals. Refusing to WRITE the card
    (:func:`save_checkpoint`) stays in place as the second line: it covers a
    future version that collects somewhere else.

    Args:
        api: The resolved training API.

    Returns:
        A trainer class that behaves as its base does, minus the card.
    """

    class _CardFreeTrainer(api.trainer_cls):  # type: ignore[misc, name-defined]
        """The trainer, with model-card tracking removed."""

        def add_model_card_callback(self, default_args_dict: _Vendor) -> None:
            """Attach nothing, in place of the base's card callback."""
            del default_args_dict
            logger.debug(MEMORY_FINE_TUNE_MODEL_CARD_DISABLED)

    return _CardFreeTrainer


def _build_trainer(
    *,
    api: TrainerApi,
    model: object,
    buckets: _TrainingBuckets,
    trainer_output_dir: Path,
    hyperparameters: ContrastiveHyperparameters,
    progress_callback: ProgressCallback | None,
    cancellation: CancellationToken | None,
) -> _Vendor:
    """Assemble the datasets, arguments, loss and callback into one trainer.

    Args:
        api: The resolved training API.
        model: The loaded ``SentenceTransformer`` to fine-tune in place.
        buckets: Output of :func:`bucket_triples`.
        trainer_output_dir: Where the trainer may write its own scratch state.
        hyperparameters: What the operator configured for this run.
        progress_callback: Called with a fraction in ``0.0..1.0``, or ``None``.
        cancellation: Checked before the run and after every step, or ``None``.

    Returns:
        The configured trainer, not yet run.
    """
    return card_free_trainer_class(api)(
        model=model,
        args=build_training_arguments(
            api,
            trainer_output_dir=trainer_output_dir,
            hyperparameters=hyperparameters,
            warmup_steps=warmup_steps_for(
                buckets,
                batch_size=hyperparameters.batch_size,
                epochs=hyperparameters.epochs,
            ),
        ),
        train_dataset=build_training_datasets(api, buckets),
        # One loss instance serves every bucket: the trainer applies it per
        # dataset, and the loss reads however many candidate columns it is
        # handed.
        loss=api.loss_cls(
            model=model,
            scale=loss_scale_for(hyperparameters.temperature),
        ),
        callbacks=[
            build_progress_callback(
                api,
                progress_callback=progress_callback,
                cancellation=cancellation,
            )
        ],
    )


def save_checkpoint(*, model: _Vendor, destination: Path) -> None:
    """Write the fine-tuned *model* to *destination* without a model card.

    The generated card is not documentation, it is a disclosure: the trainer
    attaches a callback that samples the training rows, and the card renders
    those samples verbatim into ``README.md`` (a 16 KB card carried every
    anchor, positive and negative from a 12-row run). Those rows are excerpts
    of the organisation's own documents, and a card is built to travel with the
    checkpoint, so writing one puts internal text into every copy anybody
    shares. Suppressing it also drops the hub lookup the card does to pin the
    base model's revision, which otherwise reaches the network from a run whose
    base model may be a local path.

    Blocking; callers dispatch it off the event loop.

    Args:
        model: The trained ``SentenceTransformer``.
        destination: Directory to write the checkpoint into.
    """
    model.save(str(destination), create_model_card=False)
