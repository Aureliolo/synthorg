# module-kind: feature
"""Output-style policy feature manifest.

Owns the ``output_style`` settings namespace and declares the ghost-wired
construction seams. The subsystem holds no state slice or REST controller of its
own: it binds a process-global ambient service (used by every output boundary)
and the soft-layer house-style provider at startup, and rebuilds both on a
settings change. This manifest exists so those dynamically-bound symbols satisfy
the ghost-wiring parity gate.
"""

from synthorg._core.features import FeatureManifest, FeatureModule
from synthorg.settings.enums import SettingNamespace

FEATURE: FeatureModule = FeatureManifest(
    name="output_style",
    settings_namespace=SettingNamespace.OUTPUT_STYLE,
    ghost_wired_symbols=(
        "rebuild_and_bind_output_style",
        "OutputStyleSettingsSubscriber",
    ),
    depends_on=(),
)
