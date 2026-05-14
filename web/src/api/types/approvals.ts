/** Approval queue and HITL evidence types. */

import type { ApprovalRiskLevel, ApprovalStatus } from './enums'
import type { ApprovalResponse as WireApprovalResponse, EvidencePackage } from './dtos.gen'

export type {
  ApproveRequest,
  CreateApprovalRequest,
  EvidencePackage,
  EvidencePackageSignature,
  RecommendedAction,
  RejectRequest,
} from './dtos.gen'

/**
 * ApprovalResponse with the defaulted Pydantic fields re-typed as
 * required because the wire serializer ALWAYS emits them (defaults
 * are serialised; the JSON schema simply omits them from
 * ``required[]`` because Pydantic treats "has default" as "not
 * required from the client side"). Frontend consumers can rely on
 * the value being present at runtime.
 */
export type ApprovalResponse = Omit<
  WireApprovalResponse,
  'metadata' | 'status' | 'task_id' | 'decided_by' | 'decision_reason' | 'decided_at' | 'expires_at' | 'evidence_package' | 'seconds_remaining'
> & {
  readonly metadata: Record<string, string>
  readonly status: ApprovalStatus
  readonly task_id: string | null
  readonly decided_by: string | null
  readonly decision_reason: string | null
  readonly decided_at: string | null
  readonly expires_at: string | null
  readonly evidence_package: EvidencePackage | null
  readonly seconds_remaining: number | null
}

/** Pre-decoration approval row (the queue endpoint augments
 *  ``ApprovalItem`` with ``seconds_remaining`` and ``urgency_level``
 *  before returning ``ApprovalResponse``). The base type is not a
 *  separate schema in OpenAPI; deriving it here from
 *  ``ApprovalResponse`` keeps a single source of truth.
 */
export type ApprovalItem = Omit<ApprovalResponse, 'seconds_remaining' | 'urgency_level'>

/** Inline enum on ``EvidencePackageSignature.algorithm``. OpenAPI
 *  emits it as a string union, not a named schema. The runtime VALUES
 *  tuple stays hand-maintained because the dashboard's signature
 *  pickers iterate it. */
export type SignatureAlgorithm = 'ml-dsa-65' | 'ed25519'

export const SIGNATURE_ALGORITHM_VALUES = [
  'ml-dsa-65', 'ed25519',
] as const satisfies readonly SignatureAlgorithm[]

/** Frontend-only query filter (not a Pydantic DTO). */
export interface ApprovalFilters {
  status?: ApprovalStatus
  risk_level?: ApprovalRiskLevel
  action_type?: string
  offset?: number
  limit?: number
}
