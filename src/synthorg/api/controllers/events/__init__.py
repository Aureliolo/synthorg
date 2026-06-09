"""Event-stream and interrupt controllers, split by transport.

``stream`` (``EventStreamController`` on ``/events``) carries the AG-UI
SSE stream and the SSE-side resume endpoint; ``interrupts``
(``InterruptController`` on ``/interrupts``) is the polling fallback for
listing and resuming interrupts. ``_shared`` holds the resume DTOs, the
store/auth accessors, payload validation, and the common resume body,
plus the session-id alphabet guard.

Direct imports only:
``from synthorg.api.controllers.events.stream import EventStreamController``.
This package's ``__init__`` deliberately stays empty so each controller
and helper is referenced at its own import site.
"""
