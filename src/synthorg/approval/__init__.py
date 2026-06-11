"""Shared approval types and protocols.

A neutral subsystem module so ``engine`` and ``tools`` can both depend on
approval event models (``EscalationInfo``, ``ResumePayload``) without
either module importing the other.

The package init stays light: it re-exports only the leaf event models and
does NOT eagerly import ``approval.protocol``. ``ApprovalStoreProtocol``
names ``core.approval.ApprovalItem`` at runtime so typeguard can resolve its
signatures, and ``core.approval`` imports ``approval.enums`` -- pulling
``approval.protocol`` through this init would close a cold-import cycle
(``core.approval`` -> ``approval.enums`` -> this init -> ``approval.protocol``
-> ``core.approval``). Import the protocol from its defining submodule:
``from synthorg.approval.protocol import ApprovalStoreProtocol``.

The concrete ``ApprovalStore`` implementation lives in
``synthorg.api.approval_store``; concrete ``ApprovalRepository``
implementations live under ``synthorg.persistence.{sqlite,postgres}``.
"""

from synthorg.approval.models import EscalationInfo, ResumePayload

__all__ = [
    "EscalationInfo",
    "ResumePayload",
]
