# module-kind: feature
"""Review-staffing feature manifest.

Declares the sweep that releases work a completion gate parked for want of a
holder. It owns no settings namespace and no state slice of its own: its
cadence lives in the ``engine`` namespace and its scheduler is published on
``EngineStateSlice``, because the release it performs is a task transition.
The manifest exists so the sweep's construction seams satisfy the
ghost-wiring parity gate next to the package they belong to, rather than
crowding the engine manifest, which is the same reason the nested
completion-oracle, cockpit and workspace packages carry their own.
"""

from synthorg._core.features import FeatureManifest, FeatureModule

FEATURE: FeatureModule = FeatureManifest(
    name="review_staffing",
    ghost_wired_symbols=(
        "ReviewStaffingReconciler",
        "ReviewStaffingScheduler",
    ),
    depends_on=(),
)
