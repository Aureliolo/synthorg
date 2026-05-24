# Lifecycle Synchronization

On-demand reference. The short rule in `CLAUDE.md` is: services with async `start()` / `stop()` use a dedicated `self._lifecycle_lock: asyncio.Lock` separate from any hot-path lock, and a timed-out stop must mark the service unrestartable.

## The rule in detail

Services with async `start()` / `stop()` methods MUST:

1. Use a dedicated `self._lifecycle_lock: asyncio.Lock` to serialise the `_running` check-and-set and any background-task spawn / drain sequence.
2. Hold the lock across the full body of both `start()` and `stop()`, so that a racing start cannot see `_running=False` mid-drain and spawn a new task that the outgoing stop never waits on.
3. Scope the lifecycle lock *separately* from any hot-path lock (`_metrics_lock`, `_cooldown_lock`, bus `_lock`, TaskEngine's `_admission_lock`) so normal traffic is not serialised against lifecycle transitions.

## Drain timeout + unrestartable flag

For services whose `stop()` drains across `await` boundaries, wrap the drain in `asyncio.wait_for(..., timeout=hard_deadline)` so the lock cannot be held indefinitely if a drain stage hangs post-cancel.

**After a timed-out stop the service MUST mark itself unrestartable** (set `self._stop_failed = True` or equivalent; TaskEngine uses `_unrestartable`) and the next `start()` MUST refuse to start until a fresh instance is constructed. Otherwise a late `start()` can stack a second generation of background tasks on top of orphaned ones that ignored cancellation.

## Canonical examples

- `TaskEngine` (`engine/task_engine.py`)
- `MessageBusBridge` (`api/bus_bridge.py`)
- `SettingsChangeDispatcher` (`settings/dispatcher.py`)
- `MeetingScheduler` (`communication/meeting/scheduler.py`)
- `IntegrationsHealthProber`
- `EscalationNotifySubscriber`
- `EscalationSweeper`
- `ProviderHealthProber` (`providers/health_prober.py`)
- `OrgInflectionMonitor` (`meta/chief_of_staff/monitor.py`)
- `BackupScheduler` (`backup/scheduler.py`)
- `PruningService` (`hr/pruning/service.py`)
- `NgrokAdapter` (`integrations/tunnel/ngrok_adapter.py`): lifecycle lock only; no spawned background task, so the drain timeout / unrestartable flag do not apply.

### In-place runner variant

`ContinuousMode` (`client/continuous.py`) is **not** a background-task service: `start()` runs the simulation loop on the calling coroutine and only returns when `stop()` signals the stop event. The lifecycle lock therefore guards only the `_running` flag transition (acquire briefly at the top of `start()` to check-and-set, release before the loop body, re-acquire in the `finally` to clear the flag). Holding the lock across the full body would deadlock a second concurrent caller: it would queue on the lock until the first finished and then enter an empty state. Document this distinction when adding new in-place runners.
