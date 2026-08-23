# module-kind: adapter
"""Contrastive-training adapter over the sentence-transformers trainer API.

Every symbol stage 3 needs is resolved here, by module path, in one place.
That is load-bearing rather than tidy: ``sentence_transformers`` binds
``SentenceTransformer`` and the trainer classes on the package but does NOT
bind ``losses`` or ``datasets`` as attributes, and upstream's compatibility
shim for the old paths is a ``sys.meta_path`` finder, which fires on an
``import`` statement and never on attribute access. Reading them off the
module object raises ``AttributeError`` on every published version.

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
from typing import Any, Final

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
from synthorg.observability import get_logger
from synthorg.observability.events.memory import (
    MEMORY_FINE_TUNE_DEPENDENCY_MISSING,
    MEMORY_FINE_TUNE_TRAINING_BUCKETED,
)

logger = get_logger(__name__)

#: Warmup is a tenth of the run, capped. Both halves carry over unchanged from
#: the legacy ``fit()`` call this adapter replaced.
_WARMUP_STEP_CAP: Final[int] = 100
_WARMUP_FRACTION_DIVISOR: Final[int] = 10

#: Column names are arbitrary to the loss, which reads position, but they must
#: avoid ``label`` / ``labels`` / ``score`` / ``scores``: the trainer treats
#: those four as targets and would drop the column from the inputs entirely.
_ANCHOR_COLUMN: Final[str] = "anchor"
_POSITIVE_COLUMN: Final[str] = "positive"
_NEGATIVE_COLUMN_PREFIX: Final[str] = "negative_"

#: Bucket keys carry their negative count so a log line names what trained.
_BUCKET_NAME_PREFIX: Final[str] = "negatives_"

#: Left unset, transformers defaults its output directory to a relative path in
#: the process working directory, which for the backend is not the run's tree.
TRAINER_OUTPUT_SUBDIR: Final[str] = "trainer_output"

_TrainingRow = dict[str, str]
_TrainingBuckets = dict[str, list[_TrainingRow]]

#: The vendor's own types are invisible to the checker: the packages are an
#: optional extra that the default sync never installs, and they ship no
#: stubs. Nothing held under this alias is inspected structurally here; each
#: value is handed straight back to the library that produced it, so the
#: annotation would carry no information even if the types were available.
_Vendor = Any  # type: ignore[explicit-any]


@dataclass(frozen=True, slots=True)
class TrainerApi:
    """The sentence-transformers training symbols, resolved once.

    Held as a value rather than imported at call sites so the whole vendor
    surface has exactly one resolution point, and so a caller cannot reach a
    symbol that ``_import_trainer_api`` never proved reachable.
    """

    dataset_cls: type[_Vendor]
    loss_cls: type[_Vendor]
    trainer_cls: type[_Vendor]
    args_cls: type[_Vendor]
    callback_cls: type[_Vendor]
    batch_samplers: _Vendor
    multi_dataset_batch_samplers: _Vendor


def _import_trainer_api() -> TrainerApi:
    """Resolve every training symbol, or fail naming the missing extra.

    Returns:
        The resolved training API.

    Raises:
        FineTuneDependencyError: If any part of the fine-tune extra is absent.
    """
    try:
        from datasets import Dataset  # noqa: PLC0415
        from sentence_transformers import (  # noqa: PLC0415
            SentenceTransformerTrainer,
            SentenceTransformerTrainingArguments,
        )
        from sentence_transformers.base.sampler import (  # noqa: PLC0415
            BatchSamplers,
            MultiDatasetBatchSamplers,
        )
        from sentence_transformers.sentence_transformer.losses import (  # noqa: PLC0415
            MultipleNegativesRankingLoss,
        )
        from transformers import TrainerCallback  # noqa: PLC0415
    except ImportError as exc:
        msg = (
            "sentence-transformers[train] (with datasets and accelerate) is "
            f"required for contrastive fine-tuning. {FINE_TUNE_DOCKER_DEP_HINT} "
            f"{FINE_TUNE_INPROCESS_DEP_HINT}"
        )
        logger.warning(
            MEMORY_FINE_TUNE_DEPENDENCY_MISSING,
            package="sentence-transformers[train]",
        )
        raise FineTuneDependencyError(msg) from exc
    else:
        return TrainerApi(
            dataset_cls=Dataset,
            loss_cls=MultipleNegativesRankingLoss,
            trainer_cls=SentenceTransformerTrainer,
            args_cls=SentenceTransformerTrainingArguments,
            callback_cls=TrainerCallback,
            batch_samplers=BatchSamplers,
            multi_dataset_batch_samplers=MultiDatasetBatchSamplers,
        )


def _negatives_of(triple: Mapping[str, object]) -> list[str]:
    """Read a triple's hard negatives as text.

    Returns:
        The negatives, empty when the field is absent or not a list.
    """
    negatives = triple.get("negatives", [])
    if not isinstance(negatives, list):
        return []
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
        FineTuneTrainingDataError: If there is nothing to train on.
    """
    if not triples:
        msg = (
            "contrastive fine-tuning received no training triples; stage 2 "
            "produced an empty file"
        )
        raise FineTuneTrainingDataError(msg)

    buckets: _TrainingBuckets = {}
    for triple in triples:
        negatives = _negatives_of(triple)
        row: _TrainingRow = {
            _ANCHOR_COLUMN: str(triple["query"]),
            _POSITIVE_COLUMN: str(triple["positive"]),
        }
        for index, negative in enumerate(negatives, start=1):
            row[f"{_NEGATIVE_COLUMN_PREFIX}{index}"] = negative
        buckets.setdefault(f"{_BUCKET_NAME_PREFIX}{len(negatives)}", []).append(row)

    return {key: buckets[key] for key in sorted(buckets, key=_bucket_sort_key)}


def _bucket_sort_key(name: str) -> int:
    """Order buckets by their negative count rather than lexically.

    Returns:
        The negative count encoded in the bucket name.
    """
    return int(name.removeprefix(_BUCKET_NAME_PREFIX))


def build_training_datasets(
    api: TrainerApi,
    triples: list[dict[str, object]],
) -> dict[str, _Vendor]:
    """Turn stage 2 triples into one dataset per hard-negative count.

    Args:
        api: The resolved training API.
        triples: Stage 2 output.

    Returns:
        Datasets keyed by bucket name, ready for the trainer's multi-dataset
        training set.

    Raises:
        FineTuneTrainingDataError: If there is nothing to train on.
    """
    buckets = bucket_triples(triples)
    logger.info(
        MEMORY_FINE_TUNE_TRAINING_BUCKETED,
        bucket_count=len(buckets),
        row_count=sum(len(rows) for rows in buckets.values()),
        buckets={name: len(rows) for name, rows in buckets.items()},
    )
    return {
        name: api.dataset_cls.from_list(rows)  # column order follows the row keys
        for name, rows in buckets.items()
    }


def warmup_steps_for(
    buckets: Mapping[str, list[_TrainingRow]],
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
    epochs: int,
    learning_rate: float,
    batch_size: int,
    warmup_steps: int,
) -> _Vendor:
    """Assemble the trainer's arguments for one contrastive run.

    Args:
        api: The resolved training API.
        trainer_output_dir: Where the trainer may write its own scratch state.
        epochs: Passes over the training set.
        learning_rate: Optimiser step size.
        batch_size: Rows per training batch.
        warmup_steps: Linear-warmup steps.

    Returns:
        The populated training arguments.
    """
    return api.args_cls(
        output_dir=str(trainer_output_dir),
        num_train_epochs=epochs,
        learning_rate=learning_rate,
        per_device_train_batch_size=batch_size,
        warmup_steps=warmup_steps,
        # The sampler upstream documents for in-batch-negative losses, and the
        # direct replacement for the dataloader the legacy path used.
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

    def _check_cancelled() -> None:
        """Interrupt the run if cancellation was requested.

        Raises:
            FineTuneCancelledError: If cancellation was requested. Raised
                rather than setting ``control.should_training_stop``, which
                would end the run cleanly and hand back a checkpoint nobody
                asked for.
        """
        if cancellation is not None:
            cancellation.check()

    class _TrainingProgressCallback(api.callback_cls):  # type: ignore[misc, name-defined]
        """Reports progress and interrupts a cancelled run."""

        def on_train_begin(
            self,
            args: object,  # noqa: ARG002
            state: _Vendor,  # noqa: ARG002
            control: object,  # noqa: ARG002
            **kwargs: object,  # noqa: ARG002
        ) -> None:
            """Refuse to start a run that was already cancelled."""
            _check_cancelled()

        def on_step_end(
            self,
            args: object,  # noqa: ARG002
            state: _Vendor,
            control: object,  # noqa: ARG002
            **kwargs: object,  # noqa: ARG002
        ) -> None:
            """Check cancellation, then report progress."""
            _check_cancelled()
            if progress_callback is not None and state.max_steps > 0:
                progress_callback(min(state.global_step / state.max_steps, 1.0))

    return _TrainingProgressCallback()


def run_biencoder_training(  # noqa: PLR0913
    *,
    api: TrainerApi,
    model: object,
    triples: list[dict[str, object]],
    trainer_output_dir: Path,
    epochs: int,
    learning_rate: float,
    temperature: float,
    batch_size: int,
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
        epochs: Passes over the training set.
        learning_rate: Optimiser step size.
        temperature: InfoNCE temperature; its reciprocal is the loss scale.
        batch_size: Rows per training batch.
        progress_callback: Called with a fraction in ``0.0..1.0``, or ``None``.
        cancellation: Checked on the step interval, or ``None``.

    Raises:
        FineTuneTrainingDataError: If there is nothing to train on.
    """
    buckets = bucket_triples(triples)
    datasets = build_training_datasets(api, triples)
    args = build_training_arguments(
        api,
        trainer_output_dir=trainer_output_dir,
        epochs=epochs,
        learning_rate=learning_rate,
        batch_size=batch_size,
        warmup_steps=warmup_steps_for(buckets, batch_size=batch_size, epochs=epochs),
    )
    trainer = api.trainer_cls(
        model=model,
        args=args,
        # One loss instance serves every bucket: the trainer applies it per
        # dataset, and the loss reads however many candidate columns it is
        # handed.
        train_dataset=datasets,
        loss=api.loss_cls(model=model, scale=loss_scale_for(temperature)),
        callbacks=[
            build_progress_callback(
                api,
                progress_callback=progress_callback,
                cancellation=cancellation,
            )
        ],
    )
    trainer.train()
