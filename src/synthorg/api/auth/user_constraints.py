"""Typed user-constraint conflicts + the DB-token mapping.

The ``/users`` controllers used to import the persistence-layer
constraint tokens and branch on ``ConstraintViolationError.constraint``
to build their conflict responses. That leaked persistence-internal
constraint strings into the API layer. This module owns the mapping:
:func:`raise_for_user_constraint` translates a constraint-token violation
into a typed :class:`ConflictError` subclass (all 409), so ``UserService``
raises domain errors and the controllers never see the raw tokens.
"""

from typing import NoReturn

from synthorg.core.domain_errors import ConflictError
from synthorg.core.persistence_errors import ConstraintViolationError
from synthorg.persistence.constraint_tokens import (
    IDX_SINGLE_CEO,
    LAST_CEO_TRIGGER,
    LAST_OWNER_TRIGGER,
    USERS_USERNAME_UNIQUE,
)


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
