"""Webhook controllers, split by ingest / activity / retry concerns.

Three controllers sharing the ``/webhooks`` path: ``ingest`` (receive,
verify, dedup, publish), ``activity`` (list a connection's receipts),
and ``retry`` (re-publish a failed receipt). Two helper modules back
them: ``_shared`` (the receive-path connection lookup, payload-size
guard, signature verification, replay/freshness check, and the
bus-publish + durable-idempotency primitives) and ``_retry_helpers``
(receipt-status transitions, payload decode, retryable guard, and the
publish-and-transition body). The retry path reaches
``_shared._publish_webhook_event_and_log`` module-qualified so there is
one canonical patch target.

Direct imports only:
``from synthorg.api.controllers.webhooks.<module> import ...``.
This package's ``__init__`` deliberately stays empty so each
sub-controller and helper module is referenced at its own import site.
"""
