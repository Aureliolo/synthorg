"""Unit tests for :class:`MemoryService`.

Exercises the in-process logic of the service layer (deploy / rollback
orchestration, rollback bookkeeping, JSON-backup validation, not-found
surfacing) against in-memory fakes so each case runs in milliseconds.
Integration-level behaviour against the real SQLite repo lives in the
conformance suite.
"""

import json
from datetime import UTC, datetime
from typing import override

import pytest

from synthorg.core.domain_errors import CheckpointActiveConflictError
from synthorg.core.memory_enums import MemoryCategory
from synthorg.core.types import NotBlankStr
from synthorg.memory.embedding.fine_tune_models import (
    CheckpointRecord,
    FineTuneRun,
)
from synthorg.memory.models import (
    MemoryEntry,
    MemoryQuery,
    MemoryStoreRequest,
    MemoryUpdateRequest,
)
from synthorg.memory.service import (
    CheckpointNotFoundError,
    CheckpointRollbackCorruptError,
    CheckpointRollbackUnavailableError,
    MemoryService,
)
from synthorg.settings import (
    definitions as _settings_definitions,  # noqa: F401 -- side-effect import populates the registry
)
from synthorg.settings.enums import SettingNamespace, SettingSource
from synthorg.settings.errors import SettingNotFoundError
from synthorg.settings.model_ref import ModelRef, serialize_model_ref
from synthorg.settings.models import SettingValue
from synthorg.settings.registry import get_registry
from synthorg.settings.type_validators import validate_by_type
from tests._shared import as_uuid, sid

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 4, 7, 12, 0, tzinfo=UTC)


def _checkpoint(
    *,
    checkpoint_id: str = "ckpt-1",
    is_active: bool = False,
    backup_config_json: str | None = None,
) -> CheckpointRecord:
    return CheckpointRecord(
        id=as_uuid(checkpoint_id),
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

    async def save(self, entity: CheckpointRecord) -> None:
        self._rows[str(entity.id)] = entity

    async def get(self, entity_id: str) -> CheckpointRecord | None:
        return self._rows.get(str(entity_id))

    async def list_items(
        self,
        *,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[CheckpointRecord, ...]:
        values = tuple(self._rows.values())
        return values[offset : offset + limit]

    async def list_items_page(
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

    async def delete(self, entity_id: str) -> bool:
        return self._rows.pop(str(entity_id), None) is not None

    async def get_active_checkpoint(self) -> CheckpointRecord | None:
        for row in self._rows.values():
            if row.is_active:
                return row
        return None


class _FakeRunRepo:
    """Minimal in-memory ``FineTuneRunRepository`` fake (read-only)."""

    async def save(self, entity: object) -> None:  # pragma: no cover - unused
        pass

    async def get(self, entity_id: str) -> None:  # pragma: no cover - unused
        return None

    async def delete(self, entity_id: str) -> bool:  # pragma: no cover - unused
        return False

    async def get_active_run(self) -> None:  # pragma: no cover - unused
        return None

    async def list_items(
        self,
        *,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[FineTuneRun, ...]:
        del limit, offset
        return ()

    async def list_items_page(
        self,
        *,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[tuple[FineTuneRun, ...], int]:
        del limit, offset
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
        # Enforce what the real service enforces. Accepting any string
        # let this suite pin writes production would reject: a bare
        # filesystem path into a MODEL_REF setting, and a key with no
        # definition at all, both stayed green here while failing for
        # every real caller.
        definition = get_registry().get(namespace, key)
        if definition is None:
            msg = f"Unknown setting: {namespace}/{key}"
            raise SettingNotFoundError(msg)
        validate_by_type(definition, value)
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
        await repo.save(_checkpoint(checkpoint_id="a"))
        await repo.save(_checkpoint(checkpoint_id="b"))
        service = MemoryService(
            checkpoint_repo=repo,
            run_repo=_FakeRunRepo(),
            settings_service=None,
        )

        page, total = await service.list_checkpoints(limit=limit, offset=offset)
        assert tuple(sorted(c.id for c in page)) == tuple(
            sorted(as_uuid(x) for x in expected_ids)
        )
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
        await repo.save(_checkpoint(checkpoint_id="a"))
        service = MemoryService(
            checkpoint_repo=repo,
            run_repo=_FakeRunRepo(),
            settings_service=None,
        )
        await service.delete_checkpoint(sid("a"))
        assert await repo.get(sid("a")) is None

    async def test_delete_active_checkpoint_raises_typed_conflict(self) -> None:
        """Deleting the active checkpoint is a typed 409, not a QueryError.

        The service rejects the business rule explicitly so a transient
        backend QueryError on the same path keeps its 500, instead of both
        collapsing to a 409 at the controller.
        """
        repo = _FakeCheckpointRepo()
        await repo.save(_checkpoint(checkpoint_id="a"))
        await repo.set_active(sid("a"))
        service = MemoryService(
            checkpoint_repo=repo,
            run_repo=_FakeRunRepo(),
            settings_service=None,
        )
        with pytest.raises(CheckpointActiveConflictError):
            await service.delete_checkpoint(sid("a"))
        # The row is untouched: the rejection happens before the delete.
        assert await repo.get(sid("a")) is not None


class TestMemoryServiceDeploy:
    """``deploy_checkpoint`` happy + rollback paths."""

    async def test_deploy_without_settings_service_activates_only(self) -> None:
        repo = _FakeCheckpointRepo()
        await repo.save(_checkpoint(checkpoint_id="a"))
        service = MemoryService(
            checkpoint_repo=repo,
            run_repo=_FakeRunRepo(),
            settings_service=None,
        )

        updated = await service.deploy_checkpoint(sid("a"))
        assert updated.is_active is True
        assert repo.set_active_calls == [sid("a")]

    async def test_deploy_writes_no_embedder_setting(self) -> None:
        """Activation is a checkpoint fact, not an embedder binding.

        A checkpoint is a local artefact path; ``memory.embedder_model``
        is a provider-bound reference the boot path dispatches on.
        Writing the former into the latter failed every deploy once the
        setting became a ``MODEL_REF``, and would have reached the
        provider registry as a model name before that.
        """
        repo = _FakeCheckpointRepo()
        await repo.save(_checkpoint(checkpoint_id="a"))
        settings = _FakeSettingsService()
        service = MemoryService(
            checkpoint_repo=repo,
            run_repo=_FakeRunRepo(),
            settings_service=settings,
        )

        updated = await service.deploy_checkpoint(sid("a"))

        assert updated.is_active is True
        assert repo.set_active_calls == [sid("a")]
        assert settings.set_calls == []

    async def test_deploy_survives_a_settings_service_entirely(self) -> None:
        """Deploy touches no setting, so a settings outage cannot fail it."""
        repo = _FakeCheckpointRepo()
        await repo.save(_checkpoint(checkpoint_id="prior", is_active=True))
        await repo.save(_checkpoint(checkpoint_id="a"))
        settings = _FakeSettingsService()
        settings.fail_next_set_keys.add(("memory", "embedder_model"))
        service = MemoryService(
            checkpoint_repo=repo,
            run_repo=_FakeRunRepo(),
            settings_service=settings,
        )

        updated = await service.deploy_checkpoint(sid("a"))

        assert updated.is_active is True
        assert repo.set_active_calls == [sid("a")]


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
        await repo.save(
            _checkpoint(checkpoint_id="a", backup_config_json=backup_json),
        )
        service = MemoryService(
            checkpoint_repo=repo,
            run_repo=_FakeRunRepo(),
            settings_service=(_FakeSettingsService() if attach_settings else None),
        )
        with pytest.raises(expected_exc, match=match):
            await service.rollback_checkpoint(sid("a"))

    async def test_rollback_with_valid_mapping_restores_settings(self) -> None:
        prev = serialize_model_ref(
            ModelRef(provider="test-provider", model_id="test-embed-001")
        )
        repo = _FakeCheckpointRepo()
        await repo.save(
            _checkpoint(
                checkpoint_id="a",
                backup_config_json=json.dumps({"embedder_model": prev}),
            ),
        )
        settings = _FakeSettingsService()
        service = MemoryService(
            checkpoint_repo=repo,
            run_repo=_FakeRunRepo(),
            settings_service=settings,
        )

        await service.rollback_checkpoint(sid("a"))
        assert ("memory", "embedder_model", prev) in settings.set_calls
        assert repo.deactivate_all_calls == 1

    async def test_rollback_skips_a_value_the_current_schema_rejects(self) -> None:
        """A backup outlives the schema that produced it.

        ``embedder_model`` held a bare model id before it became a
        MODEL_REF, so an old backup carries a value the validator now
        refuses. Restoring what still fits beats failing the whole
        rollback over one unrestorable key, and the skip is logged rather
        than reported as a completed restore.
        """
        repo = _FakeCheckpointRepo()
        await repo.save(
            _checkpoint(
                checkpoint_id="a",
                backup_config_json=json.dumps(
                    {"embedder_model": "legacy-bare-id", "embedder_dims": "768"}
                ),
            ),
        )
        settings = _FakeSettingsService()
        service = MemoryService(
            checkpoint_repo=repo,
            run_repo=_FakeRunRepo(),
            settings_service=settings,
        )

        await service.rollback_checkpoint(sid("a"))

        written = {(ns, key) for ns, key, _ in settings.set_calls}
        assert ("memory", "embedder_dims") in written
        assert ("memory", "embedder_model") not in written

    async def test_rollback_skips_a_key_retired_since_the_backup(self) -> None:
        """``embedder_provider`` was retired when the model ref absorbed it."""
        repo = _FakeCheckpointRepo()
        await repo.save(
            _checkpoint(
                checkpoint_id="a",
                backup_config_json=json.dumps(
                    {"embedder_provider": "gone", "embedder_dims": "512"}
                ),
            ),
        )
        settings = _FakeSettingsService()
        service = MemoryService(
            checkpoint_repo=repo,
            run_repo=_FakeRunRepo(),
            settings_service=settings,
        )

        await service.rollback_checkpoint(sid("a"))

        written = {(ns, key) for ns, key, _ in settings.set_calls}
        assert ("memory", "embedder_dims") in written
        assert ("memory", "embedder_provider") not in written

    async def test_rollback_returns_success_when_artifacts_consistent(
        self,
    ) -> None:
        """After rollback, the service re-reads the checkpoint; verify
        the normal return path when all artefacts are consistent.
        """
        repo = _FakeCheckpointRepo()
        await repo.save(
            _checkpoint(
                checkpoint_id="a",
                backup_config_json=json.dumps({"embedder_dims": "768"}),
            ),
        )
        service = MemoryService(
            checkpoint_repo=repo,
            run_repo=_FakeRunRepo(),
            settings_service=_FakeSettingsService(),
        )
        result = await service.rollback_checkpoint(sid("a"))
        assert result.id == as_uuid("a")


class TestMemoryServiceReReadFailure:
    """``deploy`` maps a vanished-after-activation row to the contracted
    ``CheckpointNotFoundError`` (a concurrent delete is the only
    realistic cause) rather than a generic ``QueryError``."""

    async def test_deploy_raises_when_activation_row_vanishes(self) -> None:
        class _VanishingRepo(_FakeCheckpointRepo):
            def __init__(self) -> None:
                super().__init__()
                self._vanish_after = False

            @override
            async def get(
                self,
                entity_id: str,
            ) -> CheckpointRecord | None:
                if self._vanish_after:
                    return None
                return await super().get(entity_id)

            @override
            async def set_active(self, checkpoint_id: str) -> None:
                await super().set_active(checkpoint_id)
                # After activation, simulate the row disappearing before
                # the service re-reads it.
                self._vanish_after = True

        repo = _VanishingRepo()
        await repo.save(_checkpoint(checkpoint_id="a"))
        service = MemoryService(
            checkpoint_repo=repo,
            run_repo=_FakeRunRepo(),
            settings_service=None,
        )
        with pytest.raises(CheckpointNotFoundError):
            await service.deploy_checkpoint(sid("a"))


class _FakeMemoryBackend:
    """Minimal :class:`MemoryBackend` fake; only ``delete`` is exercised."""

    def __init__(self, *, present: dict[tuple[str, str], bool] | None = None) -> None:
        # Map ``(agent_id, memory_id)`` -> exists. Defaults to empty;
        # tests stage by setting True before calling delete.
        self._present: dict[tuple[str, str], bool] = dict(present or {})
        self.delete_calls: list[tuple[str, str]] = []

    async def connect(self) -> None:
        return None

    async def disconnect(self) -> None:
        return None

    async def health_check(self) -> bool:
        return True

    @property
    def is_connected(self) -> bool:
        return True

    @property
    def backend_name(self) -> NotBlankStr:
        return NotBlankStr("fake")

    @property
    def supports_dense_search(self) -> bool:
        return False

    @property
    def dense_search_indexed(self) -> bool:
        return False

    async def store(
        self, agent_id: NotBlankStr, request: MemoryStoreRequest
    ) -> NotBlankStr:
        return NotBlankStr("mem-0")

    async def retrieve(
        self, agent_id: NotBlankStr, query: MemoryQuery
    ) -> tuple[MemoryEntry, ...]:
        return ()

    async def get(
        self, agent_id: NotBlankStr, memory_id: NotBlankStr
    ) -> MemoryEntry | None:
        return None

    async def delete(self, agent_id: str, memory_id: str) -> bool:
        self.delete_calls.append((str(agent_id), str(memory_id)))
        return self._present.pop((str(agent_id), str(memory_id)), False)

    async def update(
        self,
        agent_id: NotBlankStr,
        memory_id: NotBlankStr,
        request: MemoryUpdateRequest,
    ) -> MemoryEntry | None:
        return None

    async def count(
        self, agent_id: NotBlankStr, *, category: MemoryCategory | None = None
    ) -> int:
        return 0


class TestMemoryServiceDeleteEntry:
    """``delete_memory_entry`` routes through the wired ``MemoryBackend``."""

    async def test_returns_true_on_successful_delete(self) -> None:
        backend = _FakeMemoryBackend(present={("agent-a", "mem-1"): True})
        service = MemoryService(
            checkpoint_repo=_FakeCheckpointRepo(),
            run_repo=_FakeRunRepo(),
            settings_service=None,
            memory_backend=backend,
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
            memory_backend=backend,
        )

        result = await service.delete_memory_entry(
            NotBlankStr("agent-a"),
            NotBlankStr("missing"),
        )

        assert result is False
        assert backend.delete_calls == [("agent-a", "missing")]

    async def test_raises_backend_unsupported_when_no_backend(self) -> None:
        from synthorg.memory.fine_tune_plan import MemoryBackendUnsupportedError

        service = MemoryService(
            checkpoint_repo=_FakeCheckpointRepo(),
            run_repo=_FakeRunRepo(),
            settings_service=None,
        )

        with pytest.raises(MemoryBackendUnsupportedError):
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
