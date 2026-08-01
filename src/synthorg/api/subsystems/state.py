# module-kind: code
"""State slice holding the subsystem reconciler.

Its own slice rather than a field on an existing one: the reconciler types
against :class:`AppState`, and hanging it off a slice that ``AppState``
already reaches would close an import cycle.
"""

from pydantic import ConfigDict

from synthorg._core.features import BaseFeatureStateSlice
from synthorg.api.subsystems.reconciler import SubsystemReconciler


class SubsystemsStateSlice(BaseFeatureStateSlice):
    """Holds the reconciler so any trigger can reach it.

    Attributes:
        reconciler: The reconciler, or ``None`` before boot builds it.
    """

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        allow_inf_nan=False,
        arbitrary_types_allowed=True,
    )

    reconciler: SubsystemReconciler | None = None
