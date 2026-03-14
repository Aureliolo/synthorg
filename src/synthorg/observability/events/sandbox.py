"""Sandbox event constants."""

from typing import Final

SANDBOX_EXECUTE_START: Final[str] = "sandbox.execute.start"
SANDBOX_EXECUTE_SUCCESS: Final[str] = "sandbox.execute.success"
SANDBOX_EXECUTE_FAILED: Final[str] = "sandbox.execute.failed"
SANDBOX_EXECUTE_TIMEOUT: Final[str] = "sandbox.execute.timeout"
SANDBOX_SPAWN_FAILED: Final[str] = "sandbox.spawn.failed"
SANDBOX_ENV_FILTERED: Final[str] = "sandbox.env.filtered"
SANDBOX_WORKSPACE_VIOLATION: Final[str] = "sandbox.workspace.violation"
SANDBOX_CLEANUP: Final[str] = "sandbox.cleanup"
SANDBOX_PATH_FALLBACK: Final[str] = "sandbox.path.fallback"
SANDBOX_HEALTH_CHECK: Final[str] = "sandbox.health_check"
SANDBOX_KILL_FAILED: Final[str] = "sandbox.kill.failed"
SANDBOX_KILL_FALLBACK: Final[str] = "sandbox.kill.fallback"
