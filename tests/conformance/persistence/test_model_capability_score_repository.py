"""Conformance tests for ``ModelCapabilityScoreRepository``.

Dual-backend parity: a single assertion set runs against SQLite and
Postgres via the ``backend`` fixture in
``tests/conformance/persistence/conftest.py``. The repo is reached
through ``backend.model_capability_scores``, the same accessor the
grading path uses, so a repository that works here but is not wired onto
the backend cannot pass.

Covers:

* CRUD round-trip on the composite key (save / get / list / delete).
* ``get`` returns ``None`` for an absent key.
* ``save`` upsert semantics: re-ingesting one score replaces its row.
* Two sources scoring the same model coexist rather than colliding, which
  is what lets a disagreement be shown instead of reconciled on write.
* ``save_many`` is all-or-nothing: a batch carrying one bad row leaves the
  table exactly as it was, so a half-parsed feed cannot half-apply.
* An empty batch is a no-op that leaves existing rows alone; a source which
  legitimately published nothing must not clear what it published before.
* ``list_items`` ordering (composite key ASC) + pagination.
* Invalid pagination args raise :class:`QueryError`.
"""

from datetime import UTC, datetime

import pytest

from synthorg.core.persistence_errors import PersistenceError, QueryError
from synthorg.core.types import NotBlankStr
from synthorg.persistence.model_capability_score_protocol import (
    ModelCapabilityScoreRepository,
)
from synthorg.persistence.protocol import PersistenceBackend
from synthorg.providers.capability_sources.models import (
    CapabilityAxis,
    CapabilityScore,
)

pytestmark = pytest.mark.integration

_MEASURED = datetime(2026, 5, 20, 12, 0, tzinfo=UTC)
_INGESTED = datetime(2026, 5, 21, 9, 30, tzinfo=UTC)


def _repo(backend: PersistenceBackend) -> ModelCapabilityScoreRepository:
    """Return the capability-score repository *backend* exposes."""
    return backend.model_capability_scores


def _score(
    *,
    source_label: str = "epoch",
    model_identifier: str = "example-expert-001",
    axis: CapabilityAxis = "reasoning",
    score: float = 82.5,
) -> CapabilityScore:
    return CapabilityScore(
        source_label=NotBlankStr(source_label),
        model_identifier=NotBlankStr(model_identifier),
        axis=axis,
        score=score,
        as_of=_MEASURED,
        ingested_at=_INGESTED,
    )


def _key(
    source_label: str = "epoch",
    model_identifier: str = "example-expert-001",
    axis: str = "reasoning",
) -> tuple[NotBlankStr, NotBlankStr, NotBlankStr]:
    return (
        NotBlankStr(source_label),
        NotBlankStr(model_identifier),
        NotBlankStr(axis),
    )


class TestModelCapabilityScoreRepository:
    async def test_save_and_get(self, backend: PersistenceBackend) -> None:
        repo = _repo(backend)
        await repo.save(_score())

        fetched = await repo.get(_key())
        assert fetched is not None
        assert fetched.source_label == "epoch"
        assert fetched.model_identifier == "example-expert-001"
        assert fetched.axis == "reasoning"
        assert fetched.score == pytest.approx(82.5)
        # Both timestamps survive the round trip as aware UTC, and they are
        # distinct: as_of answers "how old is this measurement", ingested_at
        # answers "when did we read it", and conflating them would make a
        # freshly-fetched stale feed look current.
        assert fetched.as_of == _MEASURED
        assert fetched.ingested_at == _INGESTED

    async def test_get_returns_none_when_absent(
        self, backend: PersistenceBackend
    ) -> None:
        repo = _repo(backend)
        assert await repo.get(_key()) is None

    async def test_save_upsert_replaces_existing(
        self, backend: PersistenceBackend
    ) -> None:
        repo = _repo(backend)
        await repo.save(_score(score=70.0))
        await repo.save(_score(score=91.0))

        fetched = await repo.get(_key())
        assert fetched is not None
        assert fetched.score == pytest.approx(91.0)
        assert len(await repo.list_items()) == 1

    async def test_two_sources_scoring_one_model_coexist(
        self, backend: PersistenceBackend
    ) -> None:
        """A disagreement is two rows, never one reconciled row.

        Averaging on write would destroy the only signal an operator has
        that a model's grading is contested.
        """
        repo = _repo(backend)
        await repo.save(_score(source_label="epoch", score=88.0))
        await repo.save(_score(source_label="lmarena", score=61.0))

        epoch = await repo.get(_key("epoch"))
        lmarena = await repo.get(_key("lmarena"))
        assert epoch is not None
        assert lmarena is not None
        assert epoch.score == pytest.approx(88.0)
        assert lmarena.score == pytest.approx(61.0)

    async def test_one_model_scored_on_several_axes(
        self, backend: PersistenceBackend
    ) -> None:
        repo = _repo(backend)
        await repo.save(_score(axis="coding", score=74.0))
        await repo.save(_score(axis="reasoning", score=88.0))

        assert len(await repo.list_items()) == 2

    async def test_save_many_is_all_or_nothing(
        self, backend: PersistenceBackend
    ) -> None:
        """A batch carrying a bad row leaves the table exactly as it was.

        A half-applied ingest is a source describing half an old feed and
        half a new one, which reads as healthy while grading models on a
        mixture nobody can reconstruct.
        """
        repo = _repo(backend)
        await repo.save(_score(model_identifier="already-there", score=50.0))

        good = _score(model_identifier="fresh-model", score=80.0)
        # A score outside the 0-100 band the column CHECKs. Built by
        # model_construct so the batch reaches the database: validating it
        # away in Python would test Pydantic rather than the transaction.
        bad = CapabilityScore.model_construct(
            source_label=NotBlankStr("epoch"),
            model_identifier=NotBlankStr("impossible-model"),
            axis="reasoning",
            score=9_999.0,
            as_of=_MEASURED,
            ingested_at=_INGESTED,
        )

        with pytest.raises(PersistenceError):
            await repo.save_many((good, bad))

        # Neither the good row from the failed batch nor a partial write of
        # it landed, and the pre-existing row is untouched.
        assert await repo.get(_key(model_identifier="fresh-model")) is None
        assert await repo.get(_key(model_identifier="impossible-model")) is None
        survivor = await repo.get(_key(model_identifier="already-there"))
        assert survivor is not None
        assert survivor.score == pytest.approx(50.0)

    async def test_save_many_empty_batch_leaves_rows_alone(
        self, backend: PersistenceBackend
    ) -> None:
        """A source that published nothing must not clear what it published.

        An empty feed and a broken feed look identical from the outside, so
        neither may be treated as an instruction to forget.
        """
        repo = _repo(backend)
        await repo.save(_score())

        await repo.save_many(())

        assert await repo.get(_key()) is not None

    async def test_save_many_upserts_a_whole_feed(
        self, backend: PersistenceBackend
    ) -> None:
        repo = _repo(backend)
        await repo.save_many(
            (
                _score(model_identifier="model-a", score=10.0),
                _score(model_identifier="model-b", score=20.0),
            ),
        )
        await repo.save_many(
            (
                _score(model_identifier="model-a", score=15.0),
                _score(model_identifier="model-c", score=30.0),
            ),
        )

        items = await repo.list_items()
        by_model = {str(s.model_identifier): s.score for s in items}
        # model-b survives a refresh that did not mention it: a feed which
        # drops a model leaves its last good row ageing visibly rather than
        # silently un-grading it.
        assert by_model == {
            "model-a": pytest.approx(15.0),
            "model-b": pytest.approx(20.0),
            "model-c": pytest.approx(30.0),
        }

    async def test_list_items_ordered_and_paginated(
        self, backend: PersistenceBackend
    ) -> None:
        repo = _repo(backend)
        await repo.save(_score(source_label="lmarena", model_identifier="model-z"))
        await repo.save(_score(source_label="epoch", model_identifier="model-a"))
        await repo.save(_score(source_label="epoch", model_identifier="model-m"))

        items = await repo.list_items()
        keys = [(str(s.source_label), str(s.model_identifier)) for s in items]
        assert keys == sorted(keys)

        page = await repo.list_items(limit=1, offset=1)
        assert len(page) == 1
        assert (str(page[0].source_label), str(page[0].model_identifier)) == keys[1]

    async def test_delete(self, backend: PersistenceBackend) -> None:
        repo = _repo(backend)
        await repo.save(_score())
        assert await repo.delete(_key()) is True
        assert await repo.delete(_key()) is False
        assert await repo.get(_key()) is None

    async def test_list_items_rejects_invalid_pagination(
        self, backend: PersistenceBackend
    ) -> None:
        repo = _repo(backend)
        with pytest.raises(QueryError):
            await repo.list_items(limit=-1)
