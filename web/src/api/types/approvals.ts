/** Approval queue and HITL evidence types. */

import type { ApprovalRiskLevel, ApprovalStatus } from './enums'
import type { EvidencePackageSignature } from './dtos.gen'

export type {
  ApprovalResponse,
  ApproveRequest,
  CreateApprovalRequest,
  EvidencePackage,
  EvidencePackageSignature,
  RecommendedAction,
  RejectRequest,
} from './dtos.gen'

/** Signature algorithm, derived from the generated DTO so it cannot drift
 *  from the backend ``Literal``. The runtime VALUES tuple stays here because
 *  the dashboard's signature pickers and the WS enum sanitiser iterate it; the
 *  `satisfies` keeps it in lockstep with the derived union. */
export type SignatureAlgorithm = EvidencePackageSignature['algorithm']

export const SIGNATURE_ALGORITHM_VALUES = [
  'ml-dsa-65',
  'ed25519',
] as const satisfies readonly SignatureAlgorithm[]

/** Frontend-only query filter (not a Pydantic DTO). */
export interface ApprovalFilters {
  status?: ApprovalStatus
  risk_level?: ApprovalRiskLevel
  action_type?: string
  offset?: number
  limit?: number
}
