"""Tests for the sentence-transformers training adapter.

Split in two by what each class needs. The pure-function classes run
everywhere, including CI, because bucketing and warmup arithmetic touch no
vendor code. The classes below them import the real ``sentence_transformers``
package behind an availability guard: that is the only coverage that can
observe an API path going away, which is exactly how ``st.losses`` and
``st.datasets`` stayed unreachable across two pinned versions while a
``SimpleNamespace`` fake reported success.
"""

import importlib.util
import os
from typing import Final

import pytest

from synthorg.memory.embedding.cancellation import CancellationToken
from synthorg.memory.embedding.fine_tune_trainer import (
    _WARMUP_STEP_CAP,
    bucket_triples,
    warmup_steps_for,
)
from synthorg.memory.errors import (
    FineTuneCancelledError,
    FineTuneTrainingDataError,
)

#: Set by the CI job that installs the fine-tune extra. A skip there would
#: restore the blind spot the job exists to remove, so the guard turns into a
#: hard failure whenever the environment claims to carry the extra.
_REQUIRE_EXTRA_ENV: Final[str] = "SYNTHORG_REQUIRE_FINE_TUNE_EXTRA"

_EXTRA_PACKAGES: Final[tuple[str, ...]] = (
    "sentence_transformers",
    "torch",
    "datasets",
    "transformers",
    "accelerate",
)


def _missing_extra_packages() -> tuple[str, ...]:
    """Names from the fine-tune extra that this environment cannot import.

    Returns:
        The absent package names, empty when every one resolves.
    """
    return tuple(
        name for name in _EXTRA_PACKAGES if importlib.util.find_spec(name) is None
    )


def _require_fine_tune_extra() -> None:
    """Skip unless the fine-tune extra is installed, or fail if it must be.

    Raises:
        AssertionError: When the environment declares the extra mandatory
            (the CI job sets ``SYNTHORG_REQUIRE_FINE_TUNE_EXTRA``) and a
            package is nonetheless absent.
    """
    missing = _missing_extra_packages()
    if not missing:
        return
    detail = ", ".join(missing)
    if os.environ.get(_REQUIRE_EXTRA_ENV):  # lint-allow: env-read
        msg = (
            f"{_REQUIRE_EXTRA_ENV} is set but these packages are missing: "
            f"{detail}. The guarded tests must run here, not skip."
        )
        raise AssertionError(msg)
    pytest.skip(f"fine-tune extra not installed in this environment: {detail}")


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

        assert set(buckets) == {"negatives_0", "negatives_2", "negatives_4"}
        assert len(buckets["negatives_2"]) == 2
        assert len(buckets["negatives_0"]) == 1
        assert len(buckets["negatives_4"]) == 1

    def test_column_names_and_order(self) -> None:
        triples: list[dict[str, object]] = [
            {"query": "q", "positive": "p", "negatives": ["n1", "n2"]},
        ]

        row = bucket_triples(triples)["negatives_2"][0]

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

        row = bucket_triples(triples)["negatives_0"][0]

        assert list(row) == ["anchor", "positive"]

    def test_uniform_triples_yield_one_bucket(self) -> None:
        triples: list[dict[str, object]] = [
            {"query": f"q{i}", "positive": f"p{i}", "negatives": ["x", "y", "z"]}
            for i in range(5)
        ]

        buckets = bucket_triples(triples)

        assert list(buckets) == ["negatives_3"]
        assert len(buckets["negatives_3"]) == 5

    def test_non_list_negatives_treated_as_empty(self) -> None:
        triples: list[dict[str, object]] = [
            {"query": "q", "positive": "p", "negatives": "not-a-list"},
        ]

        buckets = bucket_triples(triples)

        assert list(buckets) == ["negatives_0"]

    def test_missing_negatives_key_treated_as_empty(self) -> None:
        triples: list[dict[str, object]] = [{"query": "q", "positive": "p"}]

        buckets = bucket_triples(triples)

        assert list(buckets) == ["negatives_0"]

    def test_values_are_coerced_to_text(self) -> None:
        triples: list[dict[str, object]] = [
            {"query": 42, "positive": 7, "negatives": [1, 2]},
        ]

        row = bucket_triples(triples)["negatives_2"][0]

        assert row == {
            "anchor": "42",
            "positive": "7",
            "negative_1": "1",
            "negative_2": "2",
        }

    def test_empty_triples_rejected(self) -> None:
        with pytest.raises(FineTuneTrainingDataError, match="no training triples"):
            bucket_triples([])

    def test_buckets_are_ordered_by_negative_count(self) -> None:
        triples: list[dict[str, object]] = [
            {"query": "q0", "positive": "p0", "negatives": ["a", "b", "c"]},
            {"query": "q1", "positive": "p1", "negatives": []},
            {"query": "q2", "positive": "p2", "negatives": ["d"]},
        ]

        buckets = bucket_triples(triples)

        assert list(buckets) == ["negatives_0", "negatives_1", "negatives_3"]


@pytest.mark.unit
class TestWarmupStepsFor:
    """Warmup keeps the legacy ``min(100, total_steps // 10)`` shape."""

    def test_sums_batches_across_buckets(self) -> None:
        buckets = {
            "negatives_0": [{"anchor": "a", "positive": "p"}] * 30,
            "negatives_2": [{"anchor": "a", "positive": "p"}] * 30,
        }

        # ceil(30/8) == 4 per bucket -> 8 steps per epoch -> 24 total -> 24 // 10
        assert warmup_steps_for(buckets, batch_size=8, epochs=3) == 2

    def test_clamps_at_the_cap(self) -> None:
        buckets = {"negatives_1": [{"anchor": "a", "positive": "p"}] * 20_000}

        assert warmup_steps_for(buckets, batch_size=1, epochs=1) == _WARMUP_STEP_CAP

    def test_partial_batch_counts_as_a_step(self) -> None:
        buckets = {"negatives_0": [{"anchor": "a", "positive": "p"}] * 11}

        # ceil(11/10) == 2 steps per epoch, 200 epochs -> 400 // 10 == 40
        assert warmup_steps_for(buckets, batch_size=10, epochs=200) == 40

    def test_tiny_run_warms_up_on_nothing(self) -> None:
        buckets = {"negatives_0": [{"anchor": "a", "positive": "p"}]}

        assert warmup_steps_for(buckets, batch_size=128, epochs=3) == 0


@pytest.mark.unit
class TestTrainerApiImport:
    """The real package must expose every symbol stage 3 dispatches through."""

    def test_every_symbol_resolves(self) -> None:
        _require_fine_tune_extra()
        from synthorg.memory.embedding.fine_tune_trainer import _import_trainer_api

        api = _import_trainer_api()

        assert api.dataset_cls.__name__ == "Dataset"
        assert api.loss_cls.__name__ == "MultipleNegativesRankingLoss"
        assert api.trainer_cls.__name__ == "SentenceTransformerTrainer"
        assert api.args_cls.__name__ == "SentenceTransformerTrainingArguments"
        assert api.batch_samplers.NO_DUPLICATES.value == "no_duplicates"
        assert api.multi_dataset_batch_samplers.PROPORTIONAL.value == "proportional"
        assert api.callback_cls.__name__ == "TrainerCallback"

    @pytest.mark.parametrize("attribute", ["losses", "datasets"])
    def test_legacy_attribute_paths_are_still_unreachable(self, attribute: str) -> None:
        """The defect this port fixes, asserted directly.

        The package imports specific NAMES out of these subpackages, which
        binds the names and not the subpackages, and upstream's shim for the
        old paths is a ``sys.meta_path`` finder that fires on an ``import``
        statement rather than on attribute access. Reading them is what stage
        3 used to do.

        Reached through ``getattr`` rather than written as an attribute so the
        assertion means the same thing to mypy whether or not the extra is
        installed in the checking environment: with it present mypy resolves
        the real package and rejects the attribute outright, without it the
        module is untyped and the same line checks clean.

        If upstream ever binds these attributes the port is still correct,
        but this test failing is the signal that the reason for the
        module-path imports has changed.
        """
        _require_fine_tune_extra()
        import sentence_transformers

        with pytest.raises(AttributeError):
            getattr(sentence_transformers, attribute)


@pytest.mark.unit
class TestBuildTrainingDatasets:
    """Bucketed rows become real ``datasets.Dataset`` objects."""

    def test_columns_survive_in_order(self) -> None:
        _require_fine_tune_extra()
        from synthorg.memory.embedding.fine_tune_trainer import (
            _import_trainer_api,
            build_training_datasets,
        )

        api = _import_trainer_api()
        triples: list[dict[str, object]] = [
            {"query": "q0", "positive": "p0", "negatives": []},
            {"query": "q1", "positive": "p1", "negatives": ["n1", "n2"]},
        ]

        datasets = build_training_datasets(api, triples)

        assert set(datasets) == {"negatives_0", "negatives_2"}
        assert datasets["negatives_0"].column_names == ["anchor", "positive"]
        assert datasets["negatives_2"].column_names == [
            "anchor",
            "positive",
            "negative_1",
            "negative_2",
        ]
        assert len(datasets["negatives_2"]) == 1

    def test_no_column_collides_with_a_reserved_label_name(self) -> None:
        """``label``/``labels``/``score``/``scores`` are inputs the loss skips."""
        _require_fine_tune_extra()
        from synthorg.memory.embedding.fine_tune_trainer import (
            _import_trainer_api,
            build_training_datasets,
        )

        api = _import_trainer_api()
        triples: list[dict[str, object]] = [
            {"query": "q", "positive": "p", "negatives": ["n"]},
        ]

        datasets = build_training_datasets(api, triples)

        reserved = {"label", "labels", "score", "scores"}
        for dataset in datasets.values():
            assert not reserved.intersection(dataset.column_names)


@pytest.mark.unit
class TestTrainingArguments:
    """Every argument the adapter passes must still exist upstream."""

    def test_construct_with_the_arguments_the_adapter_passes(
        self, tmp_path: object
    ) -> None:
        _require_fine_tune_extra()
        from synthorg.memory.embedding.fine_tune_trainer import (
            _import_trainer_api,
            build_training_arguments,
        )

        api = _import_trainer_api()

        args = build_training_arguments(
            api,
            trainer_output_dir=tmp_path,  # type: ignore[arg-type]
            epochs=3,
            learning_rate=1e-5,
            batch_size=16,
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

    def test_loss_scale_inverts_temperature(self) -> None:
        _require_fine_tune_extra()
        from synthorg.memory.embedding.fine_tune_trainer import loss_scale_for

        assert loss_scale_for(0.02) == pytest.approx(50.0)
        assert loss_scale_for(0.05) == pytest.approx(20.0)


class _FakeTrainerState:
    """Stand-in for ``transformers.TrainerState`` in callback tests."""

    def __init__(self, *, global_step: int, max_steps: int) -> None:
        self.global_step = global_step
        self.max_steps = max_steps


@pytest.mark.unit
class TestProgressCallback:
    """Progress and cancellation keep the contracts the legacy hook had."""

    def test_progress_is_monotonic_and_bounded(self) -> None:
        _require_fine_tune_extra()
        from synthorg.memory.embedding.fine_tune_trainer import (
            _import_trainer_api,
            build_progress_callback,
        )

        api = _import_trainer_api()
        seen: list[float] = []
        callback = build_progress_callback(
            api, progress_callback=seen.append, cancellation=None
        )

        total = 20
        for step in range(1, total + 1):
            callback.on_step_end(
                None,
                _FakeTrainerState(global_step=step, max_steps=total),
                None,
            )

        assert seen == sorted(seen)
        assert seen[0] > 0.0
        assert seen[-1] == pytest.approx(1.0)
        assert all(0.0 <= value <= 1.0 for value in seen)

    def test_progress_never_exceeds_one(self) -> None:
        _require_fine_tune_extra()
        from synthorg.memory.embedding.fine_tune_trainer import (
            _import_trainer_api,
            build_progress_callback,
        )

        api = _import_trainer_api()
        seen: list[float] = []
        callback = build_progress_callback(
            api, progress_callback=seen.append, cancellation=None
        )

        callback.on_step_end(
            None, _FakeTrainerState(global_step=99, max_steps=10), None
        )

        assert seen == [pytest.approx(1.0)]

    def test_zero_max_steps_emits_nothing(self) -> None:
        _require_fine_tune_extra()
        from synthorg.memory.embedding.fine_tune_trainer import (
            _import_trainer_api,
            build_progress_callback,
        )

        api = _import_trainer_api()
        seen: list[float] = []
        callback = build_progress_callback(
            api, progress_callback=seen.append, cancellation=None
        )

        callback.on_step_end(None, _FakeTrainerState(global_step=1, max_steps=0), None)

        assert seen == []

    def test_cancellation_raises_on_the_very_first_step(self) -> None:
        """A short run is cancellable too.

        An interval-based check cannot cancel a run with fewer steps than the
        interval: ``global_step`` never reaches a multiple of it. A real
        12-row, 1-epoch run is three steps.
        """
        _require_fine_tune_extra()
        from synthorg.memory.embedding.fine_tune_trainer import (
            _import_trainer_api,
            build_progress_callback,
        )

        api = _import_trainer_api()
        token = CancellationToken()
        token.cancel()
        callback = build_progress_callback(
            api, progress_callback=None, cancellation=token
        )

        with pytest.raises(FineTuneCancelledError):
            callback.on_step_end(
                None, _FakeTrainerState(global_step=1, max_steps=3), None
            )

    def test_cancellation_refuses_to_start_the_run(self) -> None:
        """A run cancelled before it begins pays no step at all."""
        _require_fine_tune_extra()
        from synthorg.memory.embedding.fine_tune_trainer import (
            _import_trainer_api,
            build_progress_callback,
        )

        api = _import_trainer_api()
        token = CancellationToken()
        token.cancel()
        callback = build_progress_callback(
            api, progress_callback=None, cancellation=token
        )

        with pytest.raises(FineTuneCancelledError):
            callback.on_train_begin(
                None, _FakeTrainerState(global_step=0, max_steps=100), None
            )

    def test_uncancelled_token_lets_the_run_proceed(self) -> None:
        _require_fine_tune_extra()
        from synthorg.memory.embedding.fine_tune_trainer import (
            _import_trainer_api,
            build_progress_callback,
        )

        api = _import_trainer_api()
        callback = build_progress_callback(
            api, progress_callback=None, cancellation=CancellationToken()
        )

        callback.on_train_begin(
            None, _FakeTrainerState(global_step=0, max_steps=5), None
        )
        for step in range(1, 6):
            callback.on_step_end(
                None, _FakeTrainerState(global_step=step, max_steps=5), None
            )

    def test_no_cancellation_token_never_raises(self) -> None:
        _require_fine_tune_extra()
        from synthorg.memory.embedding.fine_tune_trainer import (
            _import_trainer_api,
            build_progress_callback,
        )

        api = _import_trainer_api()
        callback = build_progress_callback(
            api, progress_callback=None, cancellation=None
        )

        callback.on_train_begin(
            None, _FakeTrainerState(global_step=0, max_steps=20), None
        )
        for step in range(1, 21):
            callback.on_step_end(
                None, _FakeTrainerState(global_step=step, max_steps=20), None
            )
