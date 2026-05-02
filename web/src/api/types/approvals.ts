/** Approval queue and HITL evidence types. */

import type { ApprovalRiskLevel, ApprovalStatus, UrgencyLevel } from './enums'

/** Mirrors `synthorg.core.evidence.RecommendedAction`. */
export interface RecommendedAction {
  action_type: string
  label: string
  description: string
  confirmation_required: boolean
}

export type SignatureAlgorithm = 'ml-dsa-65' | 'ed25519'

export const SIGNATURE_ALGORITHM_VALUES = [
  'ml-dsa-65', 'ed25519',
] as const satisfies readonly SignatureAlgorithm[]

/** Mirrors `synthorg.core.evidence.EvidencePackageSignature`. */
export interface EvidencePackageSignature {
  approver_id: string
  algorithm: SignatureAlgorithm
  /**
   * Signature bytes serialized as a base64 string.
   *
   * The backend model stores raw ``bytes`` and the Pydantic JSON
   * encoder emits standard RFC 4648 base64 (no URL-safe alphabet,
   * padding preserved). Callers that need the raw bytes should run
   * ``atob(signature_bytes)`` then convert the result to a
   * ``Uint8Array``. This contract is verified by the DTO parity
   * tests in ``tests/unit/api/test_dto_parity.py``.
   */
  signature_bytes: string
  signed_at: string
  chain_position: number
}

/**
 * Mirrors `synthorg.core.evidence.EvidencePackage` (extends
 * ``StructuredArtifact``). Structured payload for HITL approval
 * decisions: narrative, reasoning trace, recommended actions, and
 * audit-chain signatures.
 */
export interface EvidencePackage {
  id: string
  title: string
  narrative: string
  reasoning_trace: readonly string[]
  recommended_actions: readonly RecommendedAction[]
  source_agent_id: string
  task_id: string | null
  risk_level: ApprovalRiskLevel
  metadata: Record<string, unknown>
  signature_threshold: number
  signatures: readonly EvidencePackageSignature[]
  /** Computed field -- whether the signature threshold has been met. */
  is_fully_signed: boolean
  /** Inherited from StructuredArtifact. */
  created_at: string
}

export interface ApprovalItem {
  id: string
  action_type: string
  title: string
  description: string
  requested_by: string
  risk_level: ApprovalRiskLevel
  status: ApprovalStatus
  task_id: string | null
  metadata: Record<string, string>
  decided_by: string | null
  decision_reason: string | null
  created_at: string
  decided_at: string | null
  expires_at: string | null
  /** Structured HITL evidence for rich approval UIs. */
  evidence_package: EvidencePackage | null
}

export interface ApprovalResponse extends ApprovalItem {
  seconds_remaining: number | null
  urgency_level: UrgencyLevel
}

export interface CreateApprovalRequest {
  action_type: string
  title: string
  description: string
  risk_level: ApprovalRiskLevel
  ttl_seconds?: number
  task_id?: string
  metadata?: Record<string, string>
}

export interface ApproveRequest {
  comment?: string
}

export interface RejectRequest {
  reason: string
}

export interface ApprovalFilters {
  status?: ApprovalStatus
  risk_level?: ApprovalRiskLevel
  action_type?: string
  offset?: number
  limit?: number
}
