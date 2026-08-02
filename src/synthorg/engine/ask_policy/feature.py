# module-kind: feature
"""Ask-policy feature manifest.

The subsystem holds no state slice, no REST controller, and no settings
namespace of its own (its three keys live in ``engine``, like the
completion oracle's). It binds a process-global ambient provider the prompt
build reads, and re-binds it on a settings change. This manifest exists so
those dynamically-bound symbols satisfy the ghost-wiring parity gate.
"""

from synthorg._core.features import FeatureManifest, FeatureModule

FEATURE: FeatureModule = FeatureManifest(
    name="ask_policy",
    ghost_wired_symbols=(
        "rebuild_and_bind_ask_policy",
        "AskPolicySettingsSubscriber",
    ),
    depends_on=(),
)
