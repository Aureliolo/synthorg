"""Authentication controllers, split by auth lifecycle phase.

Five controllers sharing the ``/auth`` path: ``bootstrap`` (first-run
admin setup), ``session`` (login / refresh / logout), ``credentials``
(change-password), ``identity`` (``/me`` + WebSocket ticket), and
``sessions_mgmt`` (list / revoke active sessions). The ``_shared``
helper module holds the single auth rate-limit config, the
constant-time dummy hash, and the login-attempt lockout helpers.

Direct imports only:
``from synthorg.api.auth.controllers.<module> import ...``.
This package's ``__init__`` deliberately stays empty so each
sub-controller and the helper module is referenced at its own import
site.
"""
