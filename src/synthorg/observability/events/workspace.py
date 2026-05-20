"""Workspace isolation event constants."""

from typing import Final

WORKSPACE_SETUP_START: Final[str] = "workspace.setup.start"
WORKSPACE_SETUP_COMPLETE: Final[str] = "workspace.setup.complete"
WORKSPACE_SETUP_FAILED: Final[str] = "workspace.setup.failed"
WORKSPACE_MERGE_START: Final[str] = "workspace.merge.start"
WORKSPACE_MERGE_COMPLETE: Final[str] = "workspace.merge.complete"
WORKSPACE_MERGE_CONFLICT: Final[str] = "workspace.merge.conflict"
WORKSPACE_MERGE_FAILED: Final[str] = "workspace.merge.failed"
WORKSPACE_TEARDOWN_START: Final[str] = "workspace.teardown.start"
WORKSPACE_TEARDOWN_COMPLETE: Final[str] = "workspace.teardown.complete"
WORKSPACE_TEARDOWN_FAILED: Final[str] = "workspace.teardown.failed"
WORKSPACE_LIMIT_REACHED: Final[str] = "workspace.limit.reached"
WORKSPACE_GROUP_MERGE_START: Final[str] = "workspace.group.merge.start"
WORKSPACE_GROUP_MERGE_COMPLETE: Final[str] = "workspace.group.merge.complete"
WORKSPACE_GROUP_SETUP_START: Final[str] = "workspace.group.setup.start"
WORKSPACE_GROUP_SETUP_COMPLETE: Final[str] = "workspace.group.setup.complete"
WORKSPACE_GROUP_TEARDOWN_START: Final[str] = "workspace.group.teardown.start"
WORKSPACE_GROUP_TEARDOWN_COMPLETE: Final[str] = "workspace.group.teardown.complete"
WORKSPACE_MERGE_ABORT_FAILED: Final[str] = "workspace.merge.abort.failed"
WORKSPACE_SORT_WORKSPACES_APPENDED: Final[str] = "workspace.sort.workspaces.appended"
WORKSPACE_GROUP_SETUP_FAILED: Final[str] = "workspace.group.setup.failed"
WORKSPACE_SEMANTIC_ANALYSIS_START: Final[str] = "workspace.semantic.analysis.start"
WORKSPACE_SEMANTIC_ANALYSIS_COMPLETE: Final[str] = (
    "workspace.semantic.analysis.complete"
)
WORKSPACE_SEMANTIC_CONFLICT: Final[str] = "workspace.semantic.conflict"
WORKSPACE_SEMANTIC_ANALYSIS_FAILED: Final[str] = "workspace.semantic.analysis.failed"
WORKSPACE_SEMANTIC_PARSE_SKIP: Final[str] = "workspace.semantic.parse.skip"

# ── Disk quota events ────────────────────────────────────────────
WORKSPACE_DISK_WARNING: Final[str] = "workspace.disk.warning"
WORKSPACE_DISK_EXCEEDED: Final[str] = "workspace.disk.exceeded"
WORKSPACE_DISK_CLEANUP: Final[str] = "workspace.disk.cleanup"
WORKSPACE_DISK_TRAVERSAL_ERROR: Final[str] = "workspace.disk.traversal.error"
WORKSPACE_DISK_CHECK_ERROR: Final[str] = "workspace.disk.check.error"

WORKSPACE_CONFIG_INVALID: Final[str] = "workspace.config.invalid"

# ── Git backend events ───────────────────────────────────────────
GIT_BACKEND_PROVISION_START: Final[str] = "git_backend.provision.start"
GIT_BACKEND_PROVISION_COMPLETE: Final[str] = "git_backend.provision.complete"
GIT_BACKEND_PROVISION_FAILED: Final[str] = "git_backend.provision.failed"
GIT_BACKEND_PUSH_COMPLETE: Final[str] = "git_backend.push.complete"
GIT_BACKEND_PUSH_FAILED: Final[str] = "git_backend.push.failed"
GIT_BACKEND_FETCH_COMPLETE: Final[str] = "git_backend.fetch.complete"
GIT_BACKEND_FETCH_FAILED: Final[str] = "git_backend.fetch.failed"
GIT_BACKEND_PUSH_RETRY: Final[str] = "git_backend.push.retry"
GIT_BACKEND_REMOTE_PROVISIONED: Final[str] = "git_backend.remote.provisioned"

# ── Forge REST API events ────────────────────────────────────────
FORGE_API_REPO_CREATED: Final[str] = "forge_api.repo.created"
FORGE_API_REPO_EXISTS_CHECK: Final[str] = "forge_api.repo.exists_check"
FORGE_API_REQUEST_FAILED: Final[str] = "forge_api.request.failed"
FORGE_API_RATE_LIMITED: Final[str] = "forge_api.rate_limited"

# ── Project workspace provisioning events ────────────────────────
PROJECT_WORKSPACE_PROVISIONED: Final[str] = "project_workspace.provisioned"
PROJECT_WORKSPACE_REUSED: Final[str] = "project_workspace.reused"
WORKSPACE_BACKEND_KIND_CHANGED: Final[str] = "workspace.backend_kind.changed"
WORKSPACE_PATH_TRAVERSAL_REJECTED: Final[str] = (
    "project_workspace.path.traversal.rejected"
)
WORKSPACE_GIT_DIR_CLEARED: Final[str] = "project_workspace.git_dir.cleared"

# ── Coordinator push-queue events ────────────────────────────────
WORKSPACE_PUSH_QUEUE_ENQUEUED: Final[str] = "workspace.push_queue.enqueued"
WORKSPACE_PUSH_QUEUE_MERGED: Final[str] = "workspace.push_queue.merged"
WORKSPACE_PUSH_QUEUE_FAILED: Final[str] = "workspace.push_queue.failed"
WORKSPACE_PUSH_QUEUE_WORKER_FAILED: Final[str] = "workspace.push_queue.worker_failed"
