/** Approval queue and HITL evidence types. */

import type { ApprovalResponse } from './dtos.gen'
import type { ApprovalRiskLevel, ApprovalStatus } from './enums'

export type {
  ApprovalResponse,
  ApproveRequest,
  CreateApprovalRequest,
  EvidencePackage,
  EvidencePackageSignature,
  RecommendedAction,
  RejectRequest,
} from './dtos.gen'

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
