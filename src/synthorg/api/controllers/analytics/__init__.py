"""Analytics controllers, split by overview / trends / forecast concern.

Three controllers share the ``/analytics`` path: ``overview``
(``AnalyticsOverviewController``: composite snapshot), ``trends``
(``AnalyticsTrendsController``: bucketed time-series), and ``forecast``
(``AnalyticsForecastController``: budget projection). ``_shared`` holds
the three response DTOs plus the budget-context and agent-count helpers
used by more than one endpoint.

Direct imports only:
``from synthorg.api.controllers.analytics.overview import ...``.
This package's ``__init__`` deliberately stays empty so each controller
and helper is referenced at its own import site.
"""
