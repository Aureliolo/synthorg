"""Tests for the sentence-transformers training adapter.

Split in two by what each class needs. Most of it runs everywhere, including
the ordinary CI unit tier: bucketing, warmup arithmetic, the progress and
cancellation logic and the trainer wiring all touch no vendor code, so they
are covered with stand-ins and stay fast.

The classes guarded by :func:`_require_fine_tune_extra` import the real
``sentence_transformers`` package, because a stand-in agrees with whatever
signature it is handed: only a real import can observe a symbol that no longer
exists on the installed version. The extra is not installed in the default
sync, so that coverage exists only where something installs it, which is why
the guard turns into a hard failure when a caller declares it mandatory.
"""

import importlib
import os
import sys
from pathlib import Path
from typing import ClassVar, Final, Protocol

import pytest
from pydantic import ValidationError

from synthorg.memory.embedding import fine_tune_trainer
from synthorg.memory.embedding.cancellation import (
    CancellationToken,
    ProgressCallback,
)
from synthorg.memory.embedding.fine_tune_trainer import (
    _WARMUP_STEP_CAP,
    ContrastiveHyperparameters,
    TrainerApi,
    _ProgressReporter,
    bucket_name,
    bucket_triples,
    build_progress_callback,
    build_training_datasets,
    loss_scale_for,
    run_biencoder_training,
    save_checkpoint,
    warmup_steps_for,
)
from synthorg.memory.errors import (
    FineTuneCancelledError,
    FineTuneDependencyError,
    FineTuneTrainingDataError,
)
from synthorg.observability.events.memory import (
    MEMORY_FINE_TUNE_NO_HARD_NEGATIVES,
)
from tests._shared import CapturingErrorLogger

#: Set by the CI job that installs the fine-tune extra. A skip there would
#: leave the real-import coverage unrun while the job still reported green, so
#: the guard becomes a hard failure instead.
_REQUIRE_EXTRA_ENV: Final[str] = "SYNTHORG_REQUIRE_FINE_TUNE_EXTRA"

#: ``torch`` and ``accelerate`` are checked for completeness of the extra
#: rather than because the adapter imports them: the trainer needs both at
#: construction time, so their absence surfaces during a run rather than at
#: ``import_trainer_api``.
_EXTRA_PACKAGES: Final[tuple[str, ...]] = (
    "sentence_transformers",
    "torch",
    "datasets",
    "transformers",
    "accelerate",
)


def _missing_extra_packages() -> tuple[str, ...]:
    """Names from the fine-tune extra that this environment cannot import.

    Attempts a real import rather than a spec lookup: a half-installed package
    registers a spec and then raises when something first touches it, which
    would surface mid-test instead of as a clean skip.

    Returns:
        The absent package names, empty when every one imports.
    """
    missing: list[str] = []
    for name in _EXTRA_PACKAGES:
        try:
            importlib.import_module(name)
        except Exception:
            missing.append(name)
    return tuple(missing)


#: Probed once, at collection, because whether the extra is installed is a
#: property of the environment rather than of any one test. Where it IS
#: installed, importing the stack costs about thirteen seconds of cold
#: torch/transformers/datasets loading, and asking per test hands that whole
#: one-time cost to whichever test the collector happened to order first,
#: which then trips the six-second per-test wall-clock guard on an accident of
#: ordering. Collection sits outside every test's timing window, so the cost
#: lands where it belongs.
_MISSING_EXTRA_PACKAGES: Final[tuple[str, ...]] = _missing_extra_packages()


def _require_fine_tune_extra() -> None:
    """Skip unless the fine-tune extra is installed, or fail if it must be.

    Raises:
        AssertionError: When the environment declares the extra mandatory
            (the CI job sets ``SYNTHORG_REQUIRE_FINE_TUNE_EXTRA=1``) and a
            package is nonetheless absent.
    """
    if not _MISSING_EXTRA_PACKAGES:
        return
    detail = ", ".join(_MISSING_EXTRA_PACKAGES)
    # Compared against "1" rather than read for truthiness, matching the
    # sibling probe flag: "0" must mean off, not "any non-empty string is on".
    if os.environ.get(_REQUIRE_EXTRA_ENV, "").strip() == "1":  # lint-allow: env-read
        msg = (
            f"{_REQUIRE_EXTRA_ENV}=1 but these packages are missing: "
            f"{detail}. The guarded tests must run here, not skip."
        )
        raise AssertionError(msg)
    pytest.skip(f"fine-tune extra not installed in this environment: {detail}")


class _RefusingFinder:
    """Meta-path finder that refuses one module with a chosen exception.

    Sits ahead of the real finders, so the refusal happens whether or not the
    package is installed. That matters: these tests assert on failure shapes
    the ordinary CI environment cannot produce (a lazily-resolved submodule
    re-raising, a native extension that will not load), and it also keeps the
    absent case honest on a machine that has the extra.
    """

    def __init__(self, target: str, error: Exception) -> None:
        self._target = target
        self._error = error

    def find_spec(
        self,
        fullname: str,
        path: object = None,
        target: object = None,
    ) -> None:
        """Refuse the target module and its submodules.

        Returns:
            ``None``, deferring to the finders behind this one.

        Raises:
            Exception: The configured error, for the target module.
        """
        del path, target
        if fullname == self._target or fullname.startswith(f"{self._target}."):
            raise self._error


def _refuse_import(
    monkeypatch: pytest.MonkeyPatch, module: str, error: Exception
) -> None:
    """Make importing *module* raise *error* for the duration of a test."""
    monkeypatch.delitem(sys.modules, module, raising=False)
    monkeypatch.setattr(
        sys, "meta_path", [_RefusingFinder(module, error), *sys.meta_path]
    )


def _hyperparameters(**overrides: object) -> ContrastiveHyperparameters:
    """Build hyperparameters with the pipeline's own defaults.

    Returns:
        The populated hyperparameters.
    """
    values: dict[str, object] = {
        "epochs": 3,
        "learning_rate": 1e-5,
        "temperature": 0.02,
        "batch_size": 128,
    }
    values.update(overrides)
    return ContrastiveHyperparameters.model_validate(values)


@pytest.mark.unit
class TestBucketTriples:
    """Triples become rectangular per-bucket rows without losing anything."""

    def test_groups_by_negative_count(self) -> None:
        triples: list[dict[str, object]] = [
            {"query": "q0", "positive": "p0", "negatives": []},
            {"query": "q1", "positive": "p1", "negatives": ["a", "b"]},
            {"query": "q2", "positive": "p2", "negatives": ["c", "d"]},
            {"query": "q3", "positive": "p3", "negatives": ["e", "f", "g", "h"]},
        ]

        buckets = bucket_triples(triples)

        assert set(buckets) == {0, 2, 4}
        assert len(buckets[2]) == 2
        assert len(buckets[0]) == 1
        assert len(buckets[4]) == 1

    def test_column_names_and_order(self) -> None:
        triples: list[dict[str, object]] = [
            {"query": "q", "positive": "p", "negatives": ["n1", "n2"]},
        ]

        row = bucket_triples(triples)[2][0]

        assert list(row) == ["anchor", "positive", "negative_1", "negative_2"]
        assert row == {
            "anchor": "q",
            "positive": "p",
            "negative_1": "n1",
            "negative_2": "n2",
        }

    def test_pair_only_bucket_has_no_negative_columns(self) -> None:
        triples: list[dict[str, object]] = [
            {"query": "q", "positive": "p", "negatives": []},
        ]

        assert list(bucket_triples(triples)[0][0]) == ["anchor", "positive"]

    def test_uniform_triples_yield_one_bucket(self) -> None:
        triples: list[dict[str, object]] = [
            {"query": f"q{i}", "positive": f"p{i}", "negatives": ["x", "y", "z"]}
            for i in range(5)
        ]

        buckets = bucket_triples(triples)

        assert list(buckets) == [3]
        assert len(buckets[3]) == 5

    def test_missing_negatives_key_treated_as_empty(self) -> None:
        """Mining's margin can starve a query, so an absent field is ordinary."""
        triples: list[dict[str, object]] = [{"query": "q", "positive": "p"}]

        assert list(bucket_triples(triples)) == [0]

    def test_values_are_coerced_to_text(self) -> None:
        triples: list[dict[str, object]] = [
            {"query": 42, "positive": 7, "negatives": [1, 2]},
        ]

        assert bucket_triples(triples)[2][0] == {
            "anchor": "42",
            "positive": "7",
            "negative_1": "1",
            "negative_2": "2",
        }

    def test_buckets_are_ordered_by_negative_count(self) -> None:
        triples: list[dict[str, object]] = [
            {"query": "q0", "positive": "p0", "negatives": ["a", "b", "c"]},
            {"query": "q1", "positive": "p1", "negatives": []},
            {"query": "q2", "positive": "p2", "negatives": ["d"]},
        ]

        assert list(bucket_triples(triples)) == [0, 1, 3]

    def test_bucket_name_encodes_the_count(self) -> None:
        assert bucket_name(0) == "negatives_0"
        assert bucket_name(4) == "negatives_4"


@pytest.mark.unit
class TestBucketTriplesRejectsCorruption:
    """Shapes stage 2 cannot write mean the file was damaged after it wrote."""

    def test_empty_triples_rejected(self) -> None:
        with pytest.raises(FineTuneTrainingDataError, match="no training triples"):
            bucket_triples([])

    @pytest.mark.parametrize("field", ["query", "positive"])
    def test_missing_required_field_rejected(self, field: str) -> None:
        triple: dict[str, object] = {"query": "q", "positive": "p", "negatives": []}
        del triple[field]

        with pytest.raises(FineTuneTrainingDataError, match="corrupt") as excinfo:
            bucket_triples([triple])

        assert field in str(excinfo.value)

    @pytest.mark.parametrize("negatives", ["not-a-list", 3, {"a": 1}])
    def test_non_list_negatives_rejected(self, negatives: object) -> None:
        """Silently reading this as "no negatives" would train a degraded model.

        Stage 2 writes a list or nothing. Any other shape means damage, and
        coercing it away produces a checkpoint that only looks wrong hours
        later, as a weak score at the promotion gate.
        """
        triples: list[dict[str, object]] = [
            {"query": "q", "positive": "p", "negatives": negatives},
        ]

        with pytest.raises(FineTuneTrainingDataError, match="non-list"):
            bucket_triples(triples)

    def test_error_names_the_offending_record(self) -> None:
        triples: list[dict[str, object]] = [
            {"query": "q0", "positive": "p0", "negatives": []},
            {"query": "q1", "positive": "p1", "negatives": []},
            {"positive": "p2", "negatives": []},
        ]

        with pytest.raises(FineTuneTrainingDataError, match="index 2"):
            bucket_triples(triples)


@pytest.mark.unit
class TestWarmupStepsFor:
    """Warmup is a tenth of the run, capped."""

    @pytest.mark.parametrize(
        ("buckets", "batch_size", "epochs", "expected"),
        [
            # ceil(30/8) == 4 per bucket -> 8 per epoch -> 24 total -> 24 // 10
            ({0: [{}] * 30, 2: [{}] * 30}, 8, 3, 2),
            ({1: [{}] * 20_000}, 1, 1, _WARMUP_STEP_CAP),
            # ceil(11/10) == 2 per epoch, 200 epochs -> 400 // 10
            ({0: [{}] * 11}, 10, 200, 40),
            ({0: [{}]}, 128, 3, 0),
            ({}, 8, 3, 0),
        ],
        ids=[
            "sums-buckets",
            "clamps-at-cap",
            "partial-batch",
            "tiny-run",
            "no-buckets",
        ],
    )
    def test_warmup_arithmetic(
        self,
        buckets: dict[int, list[dict[str, str]]],
        batch_size: int,
        epochs: int,
        expected: int,
    ) -> None:
        assert warmup_steps_for(buckets, batch_size=batch_size, epochs=epochs) == (
            expected
        )


@pytest.mark.unit
class TestLossScale:
    """The InfoNCE temperature maps to the loss's similarity scale."""

    def test_scale_inverts_temperature(self) -> None:
        assert loss_scale_for(0.02) == pytest.approx(50.0)
        assert loss_scale_for(0.05) == pytest.approx(20.0)


@pytest.mark.unit
class TestContrastiveHyperparameters:
    """The four values the operator sets, validated where they are held."""

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("epochs", 0),
            ("learning_rate", 0.0),
            ("learning_rate", -1e-5),
            ("temperature", 0.0),
            ("batch_size", 0),
        ],
    )
    def test_rejects_out_of_range(self, field: str, value: float) -> None:
        with pytest.raises(ValidationError):
            _hyperparameters(**{field: value})

    def test_rejects_unknown_fields(self) -> None:
        with pytest.raises(ValidationError):
            _hyperparameters(warmup_steps=7)

    def test_rejects_non_finite_temperature(self) -> None:
        with pytest.raises(ValidationError):
            _hyperparameters(temperature=float("nan"))

    def test_is_frozen(self) -> None:
        params = _hyperparameters()

        with pytest.raises(ValidationError):
            # mypy already refuses the assignment on a frozen model; the test
            # asserts the runtime half, which is what a dynamic caller hits.
            params.epochs = 4  # type: ignore[misc]


class _RecordingDataset:
    """Stand-in for ``datasets.Dataset`` keeping the rows it was built from."""

    def __init__(self, rows: list[dict[str, str]]) -> None:
        self.rows = rows

    @classmethod
    def from_list(cls, rows: list[dict[str, str]]) -> _RecordingDataset:
        """Build a dataset from row mappings.

        Returns:
            The dataset holding *rows*.
        """
        return cls(rows)


class _RecordingLoss:
    """Stand-in for ``MultipleNegativesRankingLoss``."""

    def __init__(self, *, model: object, scale: float) -> None:
        self.model = model
        self.scale = scale


class _RecordingArgs:
    """Stand-in for ``SentenceTransformerTrainingArguments``.

    Keeps the arguments as a mapping so a test can assert the whole set at
    once, which catches an argument silently dropped as well as one changed.
    """

    def __init__(self, **kwargs: object) -> None:
        self.values: dict[str, object] = dict(kwargs)
        #: Read back by the training-started log line, so the attribute form
        #: has to exist and not only the recorded mapping.
        self.warmup_steps = kwargs["warmup_steps"]


class _TrainingCallback(Protocol):
    """The two callback methods the trainer invokes on a run."""

    def on_train_begin(self, args: object, state: object, control: object) -> None:
        """Called once before the first step."""
        ...

    def on_step_end(self, args: object, state: object, control: object) -> None:
        """Called after every optimiser step."""
        ...


class _RecordingTrainer:
    """Stand-in for ``SentenceTransformerTrainer`` recording its wiring.

    ``train`` drives the callbacks the way the real trainer does. Recording
    the call alone would leave every cancellation and progress assertion
    passing against a run that reported nothing at all.
    """

    instances: ClassVar[list[_RecordingTrainer]] = []

    def __init__(
        self,
        *,
        model: object,
        args: _RecordingArgs,
        train_dataset: dict[str, _RecordingDataset],
        loss: _RecordingLoss,
        callbacks: list[_TrainingCallback],
    ) -> None:
        self.model = model
        self.args = args
        self.train_dataset = train_dataset
        self.loss = loss
        self.callbacks = callbacks
        self.trained = False
        #: The real trainer calls ``add_model_card_callback`` from its own
        #: constructor. Counted here so a test can prove the override lands.
        self.model_card_callbacks = 0
        self.add_model_card_callback({})
        _RecordingTrainer.instances.append(self)

    def add_model_card_callback(self, default_args_dict: dict[str, object]) -> None:
        """Stand in for the base trainer's model-card wiring."""
        del default_args_dict
        self.model_card_callbacks += 1

    def train(self) -> None:
        """Run one step per row, announcing the run first."""
        state = _TrainerState(
            global_step=0,
            max_steps=sum(len(d.rows) for d in self.train_dataset.values()),
        )
        for callback in self.callbacks:
            callback.on_train_begin(self.args, state, None)
        for step in range(1, state.max_steps + 1):
            state.global_step = step
            for callback in self.callbacks:
                callback.on_step_end(self.args, state, None)
        self.trained = True


class _TrainerState:
    """Stand-in carrying the two counters the reporter reads."""

    def __init__(self, *, global_step: int, max_steps: int) -> None:
        self.global_step = global_step
        self.max_steps = max_steps


class _Model:
    """Stand-in for ``SentenceTransformer``.

    Distinct from a bare ``object()`` so an identity assertion means the model
    the trainer got is the model the loss got.
    """


class _Samplers:
    """Stand-in for ``BatchSamplers``."""

    NO_DUPLICATES = "no_duplicates"


class _MultiSamplers:
    """Stand-in for ``MultiDatasetBatchSamplers``."""

    PROPORTIONAL = "proportional"


def _fake_api() -> TrainerApi:
    """Build a TrainerApi whose members record instead of training.

    Returns:
        A TrainerApi backed by stand-ins.
    """
    return TrainerApi(
        dataset_cls=_RecordingDataset,
        loss_cls=_RecordingLoss,
        trainer_cls=_RecordingTrainer,
        args_cls=_RecordingArgs,
        callback_cls=object,
        batch_samplers=_Samplers,
        multi_dataset_batch_samplers=_MultiSamplers,
    )


@pytest.mark.unit
class TestRunBiencoderTraining:
    """The assembly: datasets, args, loss and callback reach one trainer."""

    def setup_method(self) -> None:
        _RecordingTrainer.instances.clear()

    def _run(
        self,
        tmp_path: Path,
        *,
        progress_callback: ProgressCallback | None = None,
        **overrides: object,
    ) -> _RecordingTrainer:
        """Train against the stand-ins and hand back the trainer that ran.

        Returns:
            The single trainer the run built.
        """
        settings: dict[str, object] = {"batch_size": 2, "epochs": 1}
        settings.update(overrides)
        run_biencoder_training(
            api=_fake_api(),
            model=_Model(),
            triples=[
                {"query": f"q{i}", "positive": f"p{i}", "negatives": ["n"][: i % 2]}
                for i in range(8)
            ],
            trainer_output_dir=tmp_path,
            hyperparameters=_hyperparameters(**settings),
            progress_callback=progress_callback,
            cancellation=None,
        )
        assert len(_RecordingTrainer.instances) == 1
        return _RecordingTrainer.instances[0]

    def test_trains_once(self, tmp_path: Path) -> None:
        assert self._run(tmp_path).trained is True

    def test_every_bucket_reaches_the_training_set(self, tmp_path: Path) -> None:
        assert set(self._run(tmp_path).train_dataset) == {
            "negatives_0",
            "negatives_1",
        }

    def test_one_loss_instance_serves_every_bucket(self, tmp_path: Path) -> None:
        """The trainer applies a single loss per dataset."""
        trainer = self._run(tmp_path, temperature=0.02)

        assert trainer.loss.scale == pytest.approx(50.0)

    def test_model_reaches_both_the_trainer_and_the_loss(self, tmp_path: Path) -> None:
        """Pairing a loss with a different model would train nothing useful."""
        trainer = self._run(tmp_path)

        assert trainer.loss.model is trainer.model

    def test_a_callback_is_wired(self, tmp_path: Path) -> None:
        assert len(self._run(tmp_path).callbacks) == 1

    def test_the_model_card_callback_never_attaches(self, tmp_path: Path) -> None:
        """The card collects during TRAINING, long before anything is saved.

        Refusing to write it (:func:`save_checkpoint`) leaves the collection
        running: the callback samples the training rows and asks the Hub about
        the base model while the run is still going.
        """
        trainer = self._run(tmp_path)

        assert isinstance(trainer, _RecordingTrainer)
        assert trainer.model_card_callbacks == 0

    def test_progress_reaches_the_caller_through_the_assembly(
        self, tmp_path: Path
    ) -> None:
        """The wiring, not just the reporter: the callback must be reached."""
        seen: list[float] = []

        self._run(tmp_path, progress_callback=seen.append)

        assert seen == sorted(seen)
        assert seen[-1] == pytest.approx(1.0)

    def test_training_arguments_carry_the_hyperparameters(self, tmp_path: Path) -> None:
        """Asserted whole, so an argument silently dropped fails too."""
        trainer = self._run(tmp_path, epochs=2, learning_rate=3e-5)

        # 8 rows, batch 2, two buckets of 4 -> 2 batches each -> 4 steps an
        # epoch, 8 over the run, a tenth of which floors to 0.
        assert trainer.args.values == {
            "output_dir": str(tmp_path),
            "num_train_epochs": 2,
            "learning_rate": pytest.approx(3e-5),
            "per_device_train_batch_size": 2,
            "warmup_steps": 0,
            "batch_sampler": "no_duplicates",
            "multi_dataset_batch_sampler": "proportional",
            "save_strategy": "no",
            "eval_strategy": "no",
            "report_to": "none",
            "disable_tqdm": True,
        }

    def test_empty_triples_refused_before_a_trainer_is_built(
        self, tmp_path: Path
    ) -> None:
        with pytest.raises(FineTuneTrainingDataError):
            run_biencoder_training(
                api=_fake_api(),
                model=_Model(),
                triples=[],
                trainer_output_dir=tmp_path,
                hyperparameters=_hyperparameters(),
                progress_callback=None,
                cancellation=None,
            )

        assert _RecordingTrainer.instances == []

    def test_a_cancelled_run_never_reaches_the_trainer(self, tmp_path: Path) -> None:
        token = CancellationToken()
        token.cancel()

        with pytest.raises(FineTuneCancelledError):
            run_biencoder_training(
                api=_fake_api(),
                model=_Model(),
                triples=[{"query": "q", "positive": "p", "negatives": []}],
                trainer_output_dir=tmp_path,
                hyperparameters=_hyperparameters(batch_size=1, epochs=1),
                progress_callback=None,
                cancellation=token,
            )


@pytest.mark.unit
class TestBuildTrainingDatasets:
    """Bucketed rows become one dataset per hard-negative count."""

    def test_dataset_per_bucket(self) -> None:
        buckets = bucket_triples(
            [
                {"query": "q0", "positive": "p0", "negatives": []},
                {"query": "q1", "positive": "p1", "negatives": ["n1", "n2"]},
            ]
        )

        datasets = build_training_datasets(_fake_api(), buckets)

        assert set(datasets) == {"negatives_0", "negatives_2"}
        assert datasets["negatives_2"].rows == [
            {
                "anchor": "q1",
                "positive": "p1",
                "negative_1": "n1",
                "negative_2": "n2",
            }
        ]

    def test_no_column_collides_with_a_reserved_label_name(self) -> None:
        """``label``/``labels``/``score``/``scores`` are read as targets."""
        buckets = bucket_triples([{"query": "q", "positive": "p", "negatives": ["n"]}])

        datasets = build_training_datasets(_fake_api(), buckets)

        reserved = {"label", "labels", "score", "scores"}
        for dataset in datasets.values():
            for row in dataset.rows:
                assert not reserved.intersection(row)

    def _warnings(
        self, monkeypatch: pytest.MonkeyPatch, triples: list[dict[str, object]]
    ) -> list[str]:
        """Build datasets from *triples* and return the warning events emitted.

        Returns:
            The captured event names, in order.
        """
        capturing = CapturingErrorLogger()
        monkeypatch.setattr(fine_tune_trainer, "logger", capturing)

        build_training_datasets(_fake_api(), bucket_triples(triples))

        return [event for event, _ in capturing.calls]

    def test_warns_when_mining_delivered_no_hard_negatives(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """In-batch negatives alone still train, so nothing else would say so."""
        events = self._warnings(
            monkeypatch,
            [{"query": f"q{i}", "positive": f"p{i}"} for i in range(4)],
        )

        assert MEMORY_FINE_TUNE_NO_HARD_NEGATIVES in events

    def test_no_warning_when_some_rows_carry_negatives(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        events = self._warnings(
            monkeypatch,
            [
                {"query": "q0", "positive": "p0"},
                {"query": "q1", "positive": "p1", "negatives": ["n"]},
            ],
        )

        assert MEMORY_FINE_TUNE_NO_HARD_NEGATIVES not in events


@pytest.mark.unit
class TestProgressReporter:
    """Progress and cancellation, independent of the vendor callback base."""

    def test_progress_is_monotonic_and_bounded(self) -> None:
        seen: list[float] = []
        reporter = _ProgressReporter(progress_callback=seen.append, cancellation=None)

        total = 20
        for step in range(1, total + 1):
            reporter.report(_TrainerState(global_step=step, max_steps=total))

        assert seen == sorted(seen)
        assert seen[0] > 0.0
        assert seen[-1] == pytest.approx(1.0)
        assert all(0.0 <= value <= 1.0 for value in seen)

    def test_progress_never_exceeds_one(self) -> None:
        seen: list[float] = []
        reporter = _ProgressReporter(progress_callback=seen.append, cancellation=None)

        reporter.report(_TrainerState(global_step=99, max_steps=10))

        assert seen == [pytest.approx(1.0)]

    def test_zero_max_steps_emits_nothing(self) -> None:
        seen: list[float] = []
        reporter = _ProgressReporter(progress_callback=seen.append, cancellation=None)

        reporter.report(_TrainerState(global_step=1, max_steps=0))

        assert seen == []

    def test_cancellation_raises_on_the_very_first_step(self) -> None:
        """A short run is cancellable too.

        An interval-based check cannot cancel a run with fewer steps than the
        interval, and a real 12-row epoch is three steps.
        """
        token = CancellationToken()
        token.cancel()
        reporter = _ProgressReporter(progress_callback=None, cancellation=token)

        with pytest.raises(FineTuneCancelledError):
            reporter.report(_TrainerState(global_step=1, max_steps=3))

    def test_cancellation_refuses_to_start_the_run(self) -> None:
        token = CancellationToken()
        token.cancel()
        reporter = _ProgressReporter(progress_callback=None, cancellation=token)

        with pytest.raises(FineTuneCancelledError):
            reporter.check_cancelled()

    def test_no_progress_is_emitted_once_cancelled(self) -> None:
        """Cancellation is checked first, so a cancelled step reports nothing."""
        seen: list[float] = []
        token = CancellationToken()
        token.cancel()
        reporter = _ProgressReporter(progress_callback=seen.append, cancellation=token)

        with pytest.raises(FineTuneCancelledError):
            reporter.report(_TrainerState(global_step=1, max_steps=10))

        assert seen == []

    def test_uncancelled_token_lets_the_run_proceed(self) -> None:
        reporter = _ProgressReporter(
            progress_callback=None, cancellation=CancellationToken()
        )

        reporter.check_cancelled()
        for step in range(1, 6):
            reporter.report(_TrainerState(global_step=step, max_steps=5))

    def test_no_cancellation_token_never_raises(self) -> None:
        reporter = _ProgressReporter(progress_callback=None, cancellation=None)

        reporter.check_cancelled()
        for step in range(1, 21):
            reporter.report(_TrainerState(global_step=step, max_steps=20))


@pytest.mark.unit
class TestProgressCallbackAdapter:
    """The vendor-protocol methods delegate to the reporter."""

    def test_on_step_end_reports(self) -> None:
        seen: list[float] = []
        callback = build_progress_callback(
            _fake_api(), progress_callback=seen.append, cancellation=None
        )

        callback.on_step_end(None, _TrainerState(global_step=2, max_steps=4), None)

        assert seen == [pytest.approx(0.5)]

    def test_on_train_begin_checks_cancellation(self) -> None:
        token = CancellationToken()
        token.cancel()
        callback = build_progress_callback(
            _fake_api(), progress_callback=None, cancellation=token
        )

        with pytest.raises(FineTuneCancelledError):
            callback.on_train_begin(
                None, _TrainerState(global_step=0, max_steps=100), None
            )


@pytest.mark.unit
class TestTrainerApiImportFailure:
    """An absent or broken extra raises the typed error, never a raw one.

    Runs everywhere: the failure branch is reached by making the import fail,
    which needs the package to be absent rather than present.
    """

    @pytest.mark.parametrize(
        "raised",
        [
            ImportError("No module named 'datasets'"),
            # transformers resolves its submodules lazily and re-raises the
            # underlying cause this way; its tokenizers version assertion
            # arrives as a RuntimeError, not an ImportError.
            RuntimeError("tokenizers>=0.22 is required"),
            # A torch build whose native extension will not load.
            OSError("cannot load library 'libtorch_cpu.so'"),
        ],
        ids=["absent", "lazy-submodule", "broken-native-extension"],
    )
    def test_every_import_failure_shape_becomes_a_dependency_error(
        self, monkeypatch: pytest.MonkeyPatch, raised: Exception
    ) -> None:
        _refuse_import(monkeypatch, "datasets", raised)

        with pytest.raises(FineTuneDependencyError, match="sentence-transformers"):
            fine_tune_trainer.import_trainer_api()

    def test_the_failure_names_the_install_route(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The message is what an operator acts on, so it carries the fix."""
        _refuse_import(
            monkeypatch, "datasets", ImportError("No module named 'datasets'")
        )

        with pytest.raises(FineTuneDependencyError) as excinfo:
            fine_tune_trainer.import_trainer_api()

        assert "fine-tune-cpu" in str(excinfo.value)


@pytest.mark.unit
class TestTrainerApiImport:
    """The real package must expose every symbol stage 3 dispatches through."""

    def test_every_symbol_resolves(self) -> None:
        _require_fine_tune_extra()
        from synthorg.memory.embedding.fine_tune_trainer import import_trainer_api

        api = import_trainer_api()

        assert api.dataset_cls.__name__ == "Dataset"
        assert api.loss_cls.__name__ == "MultipleNegativesRankingLoss"
        assert api.trainer_cls.__name__ == "SentenceTransformerTrainer"
        assert api.args_cls.__name__ == "SentenceTransformerTrainingArguments"
        assert api.batch_samplers.NO_DUPLICATES.value == "no_duplicates"
        assert api.multi_dataset_batch_samplers.PROPORTIONAL.value == "proportional"
        assert api.callback_cls.__name__ == "TrainerCallback"

    @pytest.mark.parametrize("attribute", ["losses", "datasets"])
    def test_top_level_attribute_access_raises_on_the_real_package(
        self, attribute: str
    ) -> None:
        """Module-path imports are required, not a style choice.

        ``sentence_transformers`` imports specific NAMES out of these
        subpackages, binding the names and not the subpackages, and its
        compatibility shim for the top-level paths is a ``sys.meta_path``
        finder that fires on an ``import`` statement, never on attribute
        access, so reading them off the package object raises.

        Reached through ``getattr`` rather than written as an attribute so the
        assertion means the same thing to mypy whether or not the extra is
        installed in the checking environment.

        A failure here means the package now binds the attribute directly, at
        which point the module-path imports should be re-checked against the
        new surface.
        """
        _require_fine_tune_extra()
        import sentence_transformers  # pyright: ignore[reportMissingImports]

        with pytest.raises(AttributeError):
            getattr(sentence_transformers, attribute)

    def test_the_real_state_object_carries_the_counters_read(self) -> None:
        """The stand-in above claims these two fields exist. Prove it."""
        _require_fine_tune_extra()
        from transformers import TrainerState  # pyright: ignore[reportMissingImports]

        state = TrainerState()

        assert hasattr(state, "global_step")
        assert hasattr(state, "max_steps")


@pytest.mark.unit
class TestRealTrainingArguments:
    """Every argument the adapter passes must still exist upstream."""

    def test_construct_with_the_arguments_the_adapter_passes(
        self, tmp_path: Path
    ) -> None:
        _require_fine_tune_extra()
        from synthorg.memory.embedding.fine_tune_trainer import (
            build_training_arguments,
            import_trainer_api,
        )

        api = import_trainer_api()

        args = build_training_arguments(
            api,
            trainer_output_dir=tmp_path,
            hyperparameters=_hyperparameters(epochs=3, batch_size=16),
            warmup_steps=7,
        )

        assert args.num_train_epochs == 3
        assert args.learning_rate == pytest.approx(1e-5)
        assert args.per_device_train_batch_size == 16
        assert args.warmup_steps == 7
        assert args.batch_sampler == api.batch_samplers.NO_DUPLICATES
        assert (
            args.multi_dataset_batch_sampler
            == api.multi_dataset_batch_samplers.PROPORTIONAL
        )
        assert args.save_strategy == "no"
        assert args.disable_tqdm is True
        # Normalised from "none" by the vendor's own post-init. Asserted
        # because construction proves the field exists, not that it still
        # means "register no reporting integration".
        assert args.report_to == []

    def test_output_dir_is_ours_not_the_process_cwd(self, tmp_path: Path) -> None:
        """Left unset, transformers writes to a relative path in the CWD."""
        _require_fine_tune_extra()
        from synthorg.memory.embedding.fine_tune_trainer import (
            build_training_arguments,
            import_trainer_api,
        )

        args = build_training_arguments(
            import_trainer_api(),
            trainer_output_dir=tmp_path,
            hyperparameters=_hyperparameters(),
            warmup_steps=0,
        )

        assert args.output_dir == str(tmp_path)


@pytest.mark.unit
class TestRealDatasets:
    """Bucketed rows become real ``datasets.Dataset`` objects."""

    def test_columns_survive_in_order(self) -> None:
        _require_fine_tune_extra()
        from synthorg.memory.embedding.fine_tune_trainer import import_trainer_api

        buckets = bucket_triples(
            [
                {"query": "q0", "positive": "p0", "negatives": []},
                {"query": "q1", "positive": "p1", "negatives": ["n1", "n2"]},
            ]
        )

        datasets = build_training_datasets(import_trainer_api(), buckets)

        assert datasets["negatives_0"].column_names == ["anchor", "positive"]
        assert datasets["negatives_2"].column_names == [
            "anchor",
            "positive",
            "negative_1",
            "negative_2",
        ]
        assert len(datasets["negatives_2"]) == 1


@pytest.mark.unit
class TestSaveCheckpoint:
    """The checkpoint must not carry a model card."""

    def test_no_model_card_is_written(self, tmp_path: Path) -> None:
        """A card renders the training rows verbatim into ``README.md``.

        Those rows are excerpts of the organisation's own documents, and a
        card is built to travel with the checkpoint.
        """
        saved: dict[str, object] = {}

        class _Model:
            def save(self, path: str, **kwargs: object) -> None:
                saved["path"] = path
                saved.update(kwargs)

        save_checkpoint(model=_Model(), destination=tmp_path)

        assert saved["path"] == str(tmp_path)
        assert saved["create_model_card"] is False
