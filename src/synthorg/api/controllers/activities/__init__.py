"""Org-wide activity feed controller, split into feed + shared helpers.

One controller (``ActivityController`` on ``/activities``) in ``feed``,
backed by ``_shared`` (the concurrent data-source fetchers, currency
resolution, exception-group spine walkers, and timeline assembly).

Direct imports only:
``from synthorg.api.controllers.activities.feed import ActivityController``.
This package's ``__init__`` deliberately stays empty so the controller
and its helpers are referenced at their own import sites.
"""
