# module-kind: feature
"""Runtime tool-call feedback feature manifest.

Declares the runtime tool-call failure-feedback surface: its
:class:`ToolCallFeedbackStateSlice` (the installed
:class:`ToolCallFeedbackTracker`). The boot hook
``wire_tool_call_feedback`` (registered in ``api.lifecycle_assembly``)
builds the tracker and installs the process-global sink when persistence
and the provider management service are present; settings live under the
existing ``providers`` namespace. The manual "re-enable tool calling"
endpoint is served by the providers feature's
``ProviderModelsController``, so this feature mounts no controllers of
its own.
"""

from synthorg._core.features import FeatureManifest, FeatureModule
from synthorg.providers.tool_call_feedback.state import ToolCallFeedbackStateSlice

FEATURE: FeatureModule = FeatureManifest(
    name="tool_call_feedback",
    settings_namespace=None,
    state_slice=ToolCallFeedbackStateSlice,
    controllers=(),
    mcp_handlers=(),
    lifecycle_hooks=(),
    ghost_wired_symbols=(),
    depends_on=(),
)
