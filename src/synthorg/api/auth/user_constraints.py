"""Typed user-constraint conflicts + the DB-token mapping.

This module owns the constraint-token mapping that keeps
persistence-internal detail out of the API layer:
:func:`raise_for_user_constraint` translates a
``ConstraintViolationError.constraint`` token into a typed
:class:`ConflictError` subclass (all 409). ``UserService`` calls it so
it raises domain errors and the ``/users`` controllers branch on typed
conflicts instead of raw persistence-layer constraint strings.
"""

from typing import NoReturn

from synthorg.core.constraint_tokens import (
    IDX_SINGLE_CEO,
    LAST_CEO_TRIGGER,
    LAST_OWNER_TRIGGER,
    USERS_USERNAME_UNIQUE,
)
from synthorg.core.domain_errors import ConflictError
from synthorg.core.persistence_errors import ConstraintViolationError


class DuplicateUsernameError(ConflictError):
    """Raised when a username is already taken (409)."""

    default_message = "Username already taken"


class SingleCeoConstraintError(ConflictError):
    """Raised when a second CEO would be created (409)."""

    default_message = "A CEO user already exists"


class LastCeoConstraintError(ConflictError):
    """Raised when an operation would remove the last CEO (409)."""

    default_message = "Operation would remove the last CEO"


class LastOwnerConstraintError(ConflictError):
    """Raised when an operation would remove the last owner (409)."""

    default_message = "Operation would remove the last owner"


def raise_for_user_constraint(exc: ConstraintViolationError) -> NoReturn:
    """Translate a user-constraint violation into a typed conflict.

    Maps the violated DB constraint token to the matching
    :class:`ConflictError` subclass. An unrecognised token re-raises the
    original :class:`ConstraintViolationError` so the persistence
    integrity handler maps it (400) rather than masking an unexpected
    constraint as a known conflict.

    Raises:
        DuplicateUsernameError: For the username-unique constraint.
        SingleCeoConstraintError: For the single-CEO index.
        LastCeoConstraintError: For the last-CEO trigger.
        LastOwnerConstraintError: For the last-owner trigger.
        ConstraintViolationError: Re-raised for any other token.
    """
    token = exc.constraint
    if token == USERS_USERNAME_UNIQUE:
        raise DuplicateUsernameError from exc
    if token == IDX_SINGLE_CEO:
        raise SingleCeoConstraintError from exc
    if token == LAST_CEO_TRIGGER:
        raise LastCeoConstraintError from exc
    if token == LAST_OWNER_TRIGGER:
        raise LastOwnerConstraintError from exc
    raise exc


__all__ = [
    "DuplicateUsernameError",
    "LastCeoConstraintError",
    "LastOwnerConstraintError",
    "SingleCeoConstraintError",
    "raise_for_user_constraint",
]
