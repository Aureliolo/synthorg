"""Unit tests for :class:`MemoryService`.

Exercises the in-process logic of the service layer (deploy / rollback
orchestration, rollback bookkeeping, JSON-backup validation, not-found
surfacing) against in-memory fakes so each case runs in milliseconds.
Integration-level behaviour against the real SQLite repo lives in the
conformance suite.
"""

from datetime import UTC, datetime

import pytest

from synthorg.core.persistence_errors import QueryError
from synthorg.core.types import NotBlankStr
from synthorg.memory.embedding.fine_tune_models import (
    CheckpointRecord,
    FineTuneRun,
)
from synthorg.memory.service import (
    CheckpointNotFoundError,
    CheckpointRollbackCorruptError,
    CheckpointRollbackUnavailableError,
    MemoryService,
)
from synthorg.settings.enums import SettingNamespace, SettingSource
from synthorg.settings.errors import SettingNotFoundError
from synthorg.settings.models import SettingValue

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 4, 7, 12, 0, tzinfo=UTC)


def _checkpoint(
    *,
    checkpoint_id: str = "ckpt-1",
    is_active: bool = False,
    backup_config_json: str | None = None,
) -> CheckpointRecord:
    return CheckpointRecord(
        id=NotBlankStr(checkpoint_id),
        run_id=NotBlankStr("run-1"),
        model_path=NotBlankStr("local/models/ckpt-1"),
        base_model=NotBlankStr("example-small-001"),
        doc_count=10,
        eval_metrics=None,
        size_bytes=1024,
        created_at=_NOW,
        is_active=is_active,
        backup_config_json=backup_config_json,
    )


class _FakeCheckpointRepo:
    """Minimal in-memory ``FineTuneCheckpointRepository`` fake."""

    def __init__(self) -> None:
        self._rows: dict[str, CheckpointRecord] = {}
        self.set_active_calls: list[str] = []
        self.deactivate_all_calls: int = 0

    async def save_checkpoint(self, checkpoint: CheckpointRecord) -> None:
        self._rows[str(checkpoint.id)] = checkpoint

    async def get_checkpoint(
        self,
        checkpoint_id: str,
    ) -> CheckpointRecord | None:
        return self._rows.get(str(checkpoint_id))

    async def list_checkpoints(
        self,
        *,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[tuple[CheckpointRecord, ...], int]:
        values = tuple(self._rows.values())
        return values[offset : offset + limit], len(values)

    async def set_active(self, checkpoint_id: str) -> None:
        self.set_active_calls.append(checkpoint_id)
        for key, row in list(self._rows.items()):
            self._rows[key] = row.model_copy(
                update={"is_active": key == checkpoint_id},
            )

    async def deactivate_all(self) -> None:
        self.deactivate_all_calls += 1
        for key, row in list(self._rows.items()):
            self._rows[key] = row.model_copy(update={"is_active": False})

    async def delete_checkpoint(self, checkpoint_id: str) -> None:
        self._rows.pop(str(checkpoint_id), None)

    async def get_active_checkpoint(self) -> CheckpointRecord | None:
        for row in self._rows.values():
            if row.is_active:
                return row
        return None


class _FakeRunRepo:
    """Minimal in-memory ``FineTuneRunRepository`` fake (read-only)."""

    async def save_run(self, run: object) -> None:  # pragma: no cover - unused
        pass

    async def get_run(self, run_id: str) -> None:  # pragma: no cover - unused
        return None

    async def get_active_run(self) -> None:  # pragma: no cover - unused
        return None

    async def list_runs(
        self,
        *,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[tuple[FineTuneRun, ...], int]:
        # Match the ``FineTuneRunRepository`` protocol signature so
        # mypy strict mode accepts this fake as a Protocol instance;
        # the tests that touch ``list_runs`` never stage rows, so
        # returning an empty tuple is enough.
        return (), 0

    async def update_run(self, run: object) -> None:  # pragma: no cover - unused
        pass

    async def mark_interrupted(self) -> int:  # pragma: no cover - unused
        return 0


class _FakeSettingsService:
    """Records set/get/delete calls so assertions can verify rollback."""

    def __init__(
        self,
        *,
        initial: dict[tuple[str, str], str] | None = None,
        missing_keys: set[tuple[str, str]] | None = None,
    ) -> None:
        self._values: dict[tuple[str, str], str] = dict(initial or {})
        self._missing = set(missing_keys or ())
        self.set_calls: list[tuple[str, str, str]] = []
        self.delete_calls: list[tuple[str, str]] = []
        self.fail_next_set_keys: set[tuple[str, str]] = set()

    async def get(self, namespace: str, key: str) -> SettingValue:
        if (namespace, key) in self._missing:
            msg = f"Unknown setting: {namespace}/{key}"
            raise SettingNotFoundError(msg)
        if (namespace, key) not in self._values:
            msg = f"Unknown setting: {namespace}/{key}"
            raise SettingNotFoundError(msg)
        return SettingValue(
            namespace=SettingNamespace(namespace),
            key=NotBlankStr(key),
            value=self._values[(namespace, key)],
            source=SettingSource.DATABASE,
            updated_at=None,
        )

    async def set(self, namespace: str, key: str, value: str) -> None:
        if (namespace, key) in self.fail_next_set_keys:
            self.fail_next_set_keys.discard((namespace, key))
            msg = f"set({namespace}, {key}) configured to fail"
            raise RuntimeError(msg)
        self.set_calls.append((namespace, key, value))
        self._values[(namespace, key)] = value

    async def delete(self, namespace: str, key: str) -> None:
        self.delete_calls.append((namespace, key))
        self._values.pop((namespace, key), None)


class TestMemoryServiceCheckpoints:
    """Happy-path coverage for list / get / delete."""

    @pytest.mark.parametrize(
        ("limit", "offset", "expected_ids"),
        [
            # Full page covers both rows (unordered set comparison).
            (10, 0, ("a", "b")),
            # Bounded: non-zero offset skips the first row.
            (1, 1, ("b",)),
            # Bounded: offset past the end returns an empty page.
            (10, 5, ()),
        ],
    )
    async def test_list_checkpoints_paginates(
        self,
        limit: int,
        offset: int,
        expected_ids: tuple[str, ...],
    ) -> None:
        """``list_checkpoints`` honours bounded limit/offset.

        Parametrized over the three interesting cases -- full-page,
        skip-leading, and past-end -- so adding another case is a
        one-line tuple rather than a whole new test.
        """
        repo = _FakeCheckpointRepo()
        await repo.save_checkpoint(_checkpoint(checkpoint_id="a"))
        await repo.save_checkpoint(_checkpoint(checkpoint_id="b"))
        service = MemoryService(
            checkpoint_repo=repo,
            run_repo=_FakeRunRepo(),
            settings_service=None,
        )

        page, total = await service.list_checkpoints(limit=limit, offset=offset)
        assert tuple(sorted(c.id for c in page)) == tuple(sorted(expected_ids))
        assert total == 2

    async def test_get_checkpoint_miss_returns_none(self) -> None:
        service = MemoryService(
            checkpoint_repo=_FakeCheckpointRepo(),
            run_repo=_FakeRunRepo(),
            settings_service=None,
        )
        assert await service.get_checkpoint(NotBlankStr("ghost")) is None

    @pytest.mark.parametrize(
        "operation",
        ["delete_checkpoint", "deploy_checkpoint", "rollback_checkpoint"],
    )
    async def test_operation_on_missing_checkpoint_raises_not_found(
        self,
        operation: str,
    ) -> None:
        """All id-targeted operations raise ``CheckpointNotFoundError``.

        Consolidates what would otherwise be three near-identical tests
        by iterating over every service method that resolves an id to a
        stored checkpoint before acting.
        """
        service = MemoryService(
            checkpoint_repo=_FakeCheckpointRepo(),
            run_repo=_FakeRunRepo(),
            settings_service=None,
        )
        with pytest.raises(CheckpointNotFoundError):
            await getattr(service, operation)(NotBlankStr("ghost"))

    async def test_delete_existing_delegates_to_repo(self) -> None:
        repo = _FakeCheckpointRepo()
        await repo.save_checkpoint(_checkpoint(checkpoint_id="a"))
        service = MemoryService(
            checkpoint_repo=repo,
            run_repo=_FakeRunRepo(),
            settings_service=None,
        )
        await service.delete_checkpoint(NotBlankStr("a"))
        assert await repo.get_checkpoint("a") is None


class TestMemoryServiceDeploy:
    """``deploy_checkpoint`` happy + rollback paths."""

    async def test_deploy_without_settings_service_activates_only(self) -> None:
        repo = _FakeCheckpointRepo()
        await repo.save_checkpoint(_checkpoint(checkpoint_id="a"))
        service = MemoryService(
            checkpoint_repo=repo,
            run_repo=_FakeRunRepo(),
            settings_service=None,
        )

        updated = await service.deploy_checkpoint(NotBlankStr("a"))
        assert updated.is_active is True
        assert repo.set_active_calls == ["a"]

    async def test_deploy_with_settings_pushes_embedder_config(self) -> None:
        repo = _FakeCheckpointRepo()
        await repo.save_checkpoint(_checkpoint(checkpoint_id="a"))
        settings = _FakeSettingsService()
        service = MemoryService(
            checkpoint_repo=repo,
            run_repo=_FakeRunRepo(),
            settings_service=settings,  # type: ignore[arg-type]
        )

        await service.deploy_checkpoint(NotBlankStr("a"))
        assert ("memory", "embedder_model", "local/models/ckpt-1") in settings.set_calls
        assert ("memory", "embedder_provider", "local") in settings.set_calls

    async def test_deploy_rollback_deletes_newly_written_missing_settings(
        self,
    ) -> None:
        repo = _FakeCheckpointRepo()
        await repo.save_checkpoint(_checkpoint(checkpoint_id="a"))
        # No prior value exists for either embedder setting AND the
        # second ``set`` raises -- rollback must explicitly delete
        # ``embedder_model`` so the setting does not remain after
        # rollback.
        settings = _FakeSettingsService(
            missing_keys={
                ("memory", "embedder_model"),
                ("memory", "embedder_provider"),
            },
        )
        settings.fail_next_set_keys.add(("memory", "embedder_provider"))
        service = MemoryService(
            checkpoint_repo=repo,
            run_repo=_FakeRunRepo(),
            settings_service=settings,  # type: ignore[arg-type]
        )

        with pytest.raises(RuntimeError):
            await service.deploy_checkpoint(NotBlankStr("a"))

        assert ("memory", "embedder_model") in settings.delete_calls
        assert ("memory", "embedder_provider") in settings.delete_calls
        # The rollback path invokes ``deactivate_all`` exactly once when
        # there is no prior active checkpoint to reactivate; asserting
        # ``== 1`` (not ``>= 1``) locks down the contract so an extra
        # call introduced later trips the test.
        assert repo.deactivate_all_calls == 1

    async def test_deploy_rollback_reactivates_prior_active_checkpoint(
        self,
    ) -> None:
        """When a prior active checkpoint exists, rollback re-activates it.

        Complements ``test_deploy_rollback_deletes_newly_written_missing_settings``
        (which exercises the no-prior branch): here the service must
        pick the prior-reactivation path, NOT call ``deactivate_all``,
        and still delete the newly-written settings whose prior values
        were absent.
        """
        repo = _FakeCheckpointRepo()
        await repo.save_checkpoint(
            _checkpoint(checkpoint_id="prior", is_active=True),
        )
        await repo.save_checkpoint(_checkpoint(checkpoint_id="a"))
        # Track the pre-deploy ``deactivate_all`` count so we can assert
        # the rollback path does not increment it (the prior-exists
        # branch routes through ``set_active(prior.id)`` instead).
        baseline_deactivate_calls = repo.deactivate_all_calls
        settings = _FakeSettingsService(
            missing_keys={
                ("memory", "embedder_model"),
                ("memory", "embedder_provider"),
            },
        )
        settings.fail_next_set_keys.add(("memory", "embedder_provider"))
        service = MemoryService(
            checkpoint_repo=repo,
            run_repo=_FakeRunRepo(),
            settings_service=settings,  # type: ignore[arg-type]
        )

        with pytest.raises(RuntimeError):
            await service.deploy_checkpoint(NotBlankStr("a"))

        # Rollback restored the prior active checkpoint in the expected
        # order: first the attempted activation for "a", then the
        # rollback reactivation for "prior". Asserting the exact
        # sequence catches a future regression where rollback fires an
        # extra ``set_active`` or flips the order.
        assert repo.set_active_calls == ["a", "prior"]
        # ``deactivate_all`` must NOT be invoked on this branch -- the
        # prior-active-exists path routes through ``set_active(prior)``
        # instead. Asserting unchanged-from-baseline catches a
        # regression that would otherwise fall through to the no-prior
        # branch.
        assert repo.deactivate_all_calls == baseline_deactivate_calls
        # Newly-written settings whose prior values were absent must
        # still be explicitly deleted so rollback leaves a pristine
        # state.
        assert ("memory", "embedder_model") in settings.delete_calls
        assert ("memory", "embedder_provider") in settings.delete_calls


class TestMemoryServiceRollback:
    """``rollback_checkpoint`` -- unavailable / corrupt / success.

    Missing-id cases are covered by the parametrized
    ``test_operation_on_missing_checkpoint_raises_not_found`` on
    :class:`TestMemoryServiceCheckpoints`.
    """

    @pytest.mark.parametrize(
        ("backup_json", "attach_settings", "expected_exc", "match"),
        [
            # No backup payload -> Unavailable (no settings service needed
            # because the corrupt-JSON branch never runs).
            (None, False, CheckpointRollbackUnavailableError, None),
            # Malformed JSON -> Corrupt.
            ("{not-json", True, CheckpointRollbackCorruptError, None),
            # JSON that parses to a non-mapping (list) -> Corrupt with
            # the explicit "JSON object" message so the second guard is
            # covered distinctly from the decode-failure branch.
            ("[]", True, CheckpointRollbackCorruptError, "JSON object"),
        ],
        ids=["missing_backup", "corrupt_json", "non_mapping_json"],
    )
    async def test_rollback_error_cases(
        self,
        backup_json: str | None,
        attach_settings: bool,
        expected_exc: type[Exception],
        match: str | None,
    ) -> None:
        """Consolidated rollback failure matrix.

        Each row represents a distinct reason the rollback must refuse
        to restore: no backup recorded, a backup that fails JSON
        parsing, and a backup that parses to the wrong shape. A single
        parametrized test keeps the matrix explicit and the setup DRY.
        """
        repo = _FakeCheckpointRepo()
        await repo.save_checkpoint(
            _checkpoint(checkpoint_id="a", backup_config_json=backup_json),
        )
        service = MemoryService(
            checkpoint_repo=repo,
            run_repo=_FakeRunRepo(),
            settings_service=(
                _FakeSettingsService() if attach_settings else None  # type: ignore[arg-type]
            ),
        )
        with pytest.raises(expected_exc, match=match):
            await service.rollback_checkpoint(NotBlankStr("a"))

    async def test_rollback_with_valid_mapping_restores_settings(self) -> None:
        repo = _FakeCheckpointRepo()
        await repo.save_checkpoint(
            _checkpoint(
                checkpoint_id="a",
                backup_config_json='{"embedder_model": "prev-model"}',
            ),
        )
        settings = _FakeSettingsService()
        service = MemoryService(
            checkpoint_repo=repo,
            run_repo=_FakeRunRepo(),
            settings_service=settings,  # type: ignore[arg-type]
        )

        await service.rollback_checkpoint(NotBlankStr("a"))
        assert (
            "memory",
            "embedder_model",
            "prev-model",
        ) in settings.set_calls
        assert repo.deactivate_all_calls == 1

    async def test_rollback_returns_success_when_artifacts_consistent(
        self,
    ) -> None:
        """After rollback, the service re-reads the checkpoint; verify
        the normal return path when all artefacts are consistent.
        """
        repo = _FakeCheckpointRepo()
        await repo.save_checkpoint(
            _checkpoint(
                checkpoint_id="a",
                backup_config_json='{"embedder_model": "prev"}',
            ),
        )
        service = MemoryService(
            checkpoint_repo=repo,
            run_repo=_FakeRunRepo(),
            settings_service=_FakeSettingsService(),  # type: ignore[arg-type]
        )
        result = await service.rollback_checkpoint(NotBlankStr("a"))
        assert result.id == "a"


class TestMemoryServiceReReadFailure:
    """``deploy`` detects missing-after-write and raises ``QueryError``."""

    async def test_deploy_raises_when_activation_row_vanishes(self) -> None:
        class _VanishingRepo(_FakeCheckpointRepo):
            def __init__(self) -> None:
                super().__init__()
                self._vanish_after = False

            async def get_checkpoint(
                self,
                checkpoint_id: str,
            ) -> CheckpointRecord | None:
                if self._vanish_after:
                    return None
                return await super().get_checkpoint(checkpoint_id)

            async def set_active(self, checkpoint_id: str) -> None:
                await super().set_active(checkpoint_id)
                # After activation, simulate the row disappearing before
                # the service re-reads it.
                self._vanish_after = True

        repo = _VanishingRepo()
        await repo.save_checkpoint(_checkpoint(checkpoint_id="a"))
        service = MemoryService(
            checkpoint_repo=repo,
            run_repo=_FakeRunRepo(),
            settings_service=None,
        )
        with pytest.raises(QueryError):
            await service.deploy_checkpoint(NotBlankStr("a"))


class _FakeMemoryBackend:
    """Minimal :class:`MemoryBackend` fake exposing only ``delete``."""

    def __init__(self, *, present: dict[tuple[str, str], bool] | None = None) -> None:
        # Map ``(agent_id, memory_id)`` -> exists. Defaults to empty;
        # tests stage by setting True before calling delete.
        self._present: dict[tuple[str, str], bool] = dict(present or {})
        self.delete_calls: list[tuple[str, str]] = []

    async def delete(self, agent_id: str, memory_id: str) -> bool:
        self.delete_calls.append((str(agent_id), str(memory_id)))
        return self._present.pop((str(agent_id), str(memory_id)), False)


class TestMemoryServiceDeleteEntry:
    """``delete_memory_entry`` routes through the wired ``MemoryBackend``."""

    async def test_returns_true_on_successful_delete(self) -> None:
        backend = _FakeMemoryBackend(present={("agent-a", "mem-1"): True})
        service = MemoryService(
            checkpoint_repo=_FakeCheckpointRepo(),
            run_repo=_FakeRunRepo(),
            settings_service=None,
            memory_backend=backend,  # type: ignore[arg-type]
        )

        result = await service.delete_memory_entry(
            NotBlankStr("agent-a"),
            NotBlankStr("mem-1"),
        )

        assert result is True
        assert backend.delete_calls == [("agent-a", "mem-1")]

    async def test_returns_false_when_not_found(self) -> None:
        backend = _FakeMemoryBackend()
        service = MemoryService(
            checkpoint_repo=_FakeCheckpointRepo(),
            run_repo=_FakeRunRepo(),
            settings_service=None,
            memory_backend=backend,  # type: ignore[arg-type]
        )

        result = await service.delete_memory_entry(
            NotBlankStr("agent-a"),
            NotBlankStr("missing"),
        )

        assert result is False
        assert backend.delete_calls == [("agent-a", "missing")]

    async def test_raises_backend_unsupported_when_no_backend(self) -> None:
        from synthorg.memory.fine_tune_plan import BackendUnsupportedError

        service = MemoryService(
            checkpoint_repo=_FakeCheckpointRepo(),
            run_repo=_FakeRunRepo(),
            settings_service=None,
        )

        with pytest.raises(BackendUnsupportedError):
            await service.delete_memory_entry(
                NotBlankStr("agent-a"),
                NotBlankStr("mem-1"),
            )

    async def test_blank_agent_id_rejected_at_type_boundary(self) -> None:
        """``NotBlankStr`` rejects blank agent ids at the type boundary."""
        from pydantic import TypeAdapter, ValidationError

        adapter = TypeAdapter(NotBlankStr)
        with pytest.raises(ValidationError):
            adapter.validate_python("")
        with pytest.raises(ValidationError):
            adapter.validate_python("   ")

    async def test_blank_memory_id_rejected_at_type_boundary(self) -> None:
        """Same boundary check for ``memory_id``."""
        from pydantic import TypeAdapter, ValidationError

        adapter = TypeAdapter(NotBlankStr)
        with pytest.raises(ValidationError):
            adapter.validate_python("")
        with pytest.raises(ValidationError):
            adapter.validate_python("\t\n")
