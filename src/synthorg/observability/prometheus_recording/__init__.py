"""Push-time recording methods for :class:`PrometheusCollector`.

``RecordingMixin`` composes the per-metric-family mixins; the
collector inherits it so the public ``record_*`` API is unchanged
(``collector.record_task_run(...)`` still works). The metric
attributes the mixins consume are declared on ``_RecordingMetricsBase``
and populated by ``PrometheusCollector.__init__``.
"""

from synthorg.observability.prometheus_recording._api import (
    _ApiRecordingMixin,
)
from synthorg.observability.prometheus_recording._coordination import (
    _CoordinationRecordingMixin,
)
from synthorg.observability.prometheus_recording._infra import (
    _InfraRecordingMixin,
)
from synthorg.observability.prometheus_recording._provider import (
    _ProviderRecordingMixin,
)
from synthorg.observability.prometheus_recording._security import (
    _SecurityRecordingMixin,
)
from synthorg.observability.prometheus_recording._task_tool import (
    _TaskToolRecordingMixin,
)
from synthorg.observability.prometheus_recording._workflow import (
    _WorkflowRecordingMixin,
)


class RecordingMixin(
    _ProviderRecordingMixin,
    _ApiRecordingMixin,
    _TaskToolRecordingMixin,
    _SecurityRecordingMixin,
    _CoordinationRecordingMixin,
    _WorkflowRecordingMixin,
    _InfraRecordingMixin,
):
    """All push-time ``record_*`` methods for the Prometheus collector."""


__all__ = ["RecordingMixin"]
