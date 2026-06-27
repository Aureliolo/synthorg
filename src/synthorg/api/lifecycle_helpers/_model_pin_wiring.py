# module-kind: code
"""Pin-validation benchmark wiring for the evaluation loop.

Builds the ``ExternalBenchmarkRegistry`` the eval-loop coordinator runs:
the per-backend :class:`ModelPinValidationRepository`, the durable
:class:`ModelPinValidationLedger`, the deterministic
:class:`PinProbeRunner`, and the registered
:class:`ModelPinValidationBenchmark`. Grouped here so
:mod:`eval_loop_wiring` stays focused on the coordinator + scheduler.

The validator's persistence is optional: a dev / empty-company run with
no backend still wires the benchmark (drift checks run), but with no
ledger, so ``validated_at`` is not stamped until a backend is present.
"""

from typing import Final

from synthorg.api.state import AppState
from synthorg.core.critical_errors import reraise_critical
from synthorg.hr.evaluation.external_benchmark_registry import ExternalBenchmarkRegistry
from synthorg.hr.evaluation.pin_fingerprint import load_pin_golden
from synthorg.hr.evaluation.pin_probe_runner import PinProbeRunner
from synthorg.hr.evaluation.pin_validation_benchmark import ModelPinValidationBenchmark
from synthorg.hr.evaluation.pin_validation_ledger import ModelPinValidationLedger
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.api import API_APP_STARTUP
from synthorg.persistence.model_pin_validation_protocol import (
    ModelPinValidationRepository,
)
from synthorg.persistence.state import PersistenceStateSlice
from synthorg.providers.drivers.scripted import ScriptedDriver

logger = get_logger(__name__)

_PROBE_PROVIDER_NAME: Final[str] = "pin-validation-probe"


def _build_pin_validation_repo(
    app_state: AppState,
) -> ModelPinValidationRepository | None:
    """Build the per-backend pin-validation repository, or ``None``.

    Returns:
        The SQLite or Postgres :class:`ModelPinValidationRepository`, or
        ``None`` when no persistence backend is wired.
    """
    from synthorg.persistence.backend_dispatch import build_for_backend  # noqa: PLC0415
    from synthorg.persistence.db_handle import (  # noqa: PLC0415
        postgres_pool,
        sqlite_connection,
    )

    backend = app_state.slice(PersistenceStateSlice).backend
    if backend is None:
        return None

    def _build_sqlite() -> ModelPinValidationRepository:
        from synthorg.persistence.sqlite.model_pin_validation_repo import (  # noqa: PLC0415
            SQLiteModelPinValidationRepository,
        )

        return SQLiteModelPinValidationRepository(
            sqlite_connection(backend),
            write_context=backend.write_context,
        )

    def _build_postgres() -> ModelPinValidationRepository:
        from synthorg.persistence.postgres.model_pin_validation_repo import (  # noqa: PLC0415
            PostgresModelPinValidationRepository,
        )

        return PostgresModelPinValidationRepository(postgres_pool(backend))

    try:
        return build_for_backend(
            backend, sqlite=_build_sqlite, postgres=_build_postgres
        )
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        # A backend that is wired but from which the repo cannot be built (a
        # test double, an unregistered backend kind, a not-yet-connected
        # backend) is treated like an absent one: the drift benchmark still
        # runs, only the validator stamp is skipped. Never let it break the
        # startup lifespan. The note stays cause-agnostic (the error_type
        # field carries the real category) rather than guessing connectivity.
        reraise_critical(exc)
        logger.warning(
            API_APP_STARTUP,
            service="model_pin_validation",
            note="pin-validation repo construction failed; validated_at not stamped",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return None


def build_pin_validation_registry(app_state: AppState) -> ExternalBenchmarkRegistry:
    """Build the eval-loop benchmark registry with pin validation wired.

    Returns:
        An :class:`ExternalBenchmarkRegistry` carrying the deterministic
        probe runner and the registered pin-validation benchmark.
    """
    repo = _build_pin_validation_repo(app_state)
    ledger = (
        ModelPinValidationLedger(repo, clock=app_state.clock)
        if repo is not None
        else None
    )
    try:
        golden = dict(load_pin_golden())
    except ValueError as exc:
        # A malformed committed artifact must degrade to the absent-file
        # behaviour (empty golden, every pin reports drift) rather than crash
        # the startup lifespan. ERROR, not WARNING: a corrupt committed
        # artifact is an operator-actionable defect, not a transient event.
        logger.error(
            API_APP_STARTUP,
            service="model_pin_validation",
            note="pin golden artifact malformed; every pin will report drift",
            action="run scripts/refresh_model_pin_golden.py",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        golden = {}
    benchmark = ModelPinValidationBenchmark(golden=golden, ledger=ledger)
    runner = PinProbeRunner(
        provider=ScriptedDriver(provider_name=_PROBE_PROVIDER_NAME),
    )
    registry = ExternalBenchmarkRegistry(agent_runner=runner)
    registry.register(benchmark)
    if repo is None:
        logger.warning(
            API_APP_STARTUP,
            service="model_pin_validation",
            note="persistence absent; drift checks run, validated_at not stamped",
        )
    else:
        logger.info(
            API_APP_STARTUP,
            service="model_pin_validation",
            note="pin-validation benchmark registered",
        )
    return registry


__all__ = ["build_pin_validation_registry"]
