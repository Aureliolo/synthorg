"""Client request controller, split into lifecycle + intake pipeline.

``lifecycle`` (``RequestController`` on ``/requests``) handles the
synchronous CRUD + status transitions (submit, scope, approve, reject);
``pipeline`` holds the detached background reconciliation
(``process_intake_pipeline`` and its reconcile helpers) that drives an
APPROVED request through the work-entry adapter and writes the terminal
state. ``pipeline`` is plain helper code, not a controller, and is not
registered in the feature manifest.

Direct imports only:
``from synthorg.api.controllers.requests.lifecycle import RequestController``.
This package's ``__init__`` deliberately stays empty so the controller
and pipeline are referenced at their own import sites.
"""
