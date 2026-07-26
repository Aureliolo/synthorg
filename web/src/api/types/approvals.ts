/** Approval queue and HITL evidence types. */

import type { ApprovalRiskLevel, ApprovalSource, ApprovalStatus } from './enums'
import type { SafeEvidencePackageSignature } from './dtos.gen'

export type {
  ApprovalAgentRef,
  ApprovalArtifactRef,
  ApprovalProjectRef,
  ApprovalResponse,
  ApprovalRunSummary,
  ApprovalTaskRef,
  ApproveRequest,
  CreateApprovalRequest,
  RejectRequest,
  // The API redacts the raw audit-chain signature bytes, so the wire
  // evidence package is the ``Safe*`` variant (no ``signature_bytes``).
  SafeEvidencePackage,
  SafeEvidencePackageSignature,
} from './dtos.gen'

/** Signature algorithm, derived from the generated DTO so it cannot drift
 *  from the backend ``Literal``. The runtime VALUES tuple stays here because
 *  the dashboard's signature pickers and the WS enum sanitiser iterate it; the
 *  `satisfies` keeps it in lockstep with the derived union. */
export type SignatureAlgorithm = SafeEvidencePackageSignature['algorithm']

export const SIGNATURE_ALGORITHM_VALUES = [
  'ml-dsa-65',
  'ed25519',
] as const satisfies readonly SignatureAlgorithm[]

/** Frontend-only query filter (not a Pydantic DTO). */
export interface ApprovalFilters {
  status?: ApprovalStatus
  /** Narrow to one origin (e.g. plan reviews only); server-side `source` param. */
  source?: ApprovalSource
  risk_level?: ApprovalRiskLevel
  action_type?: string
  cursor?: string | null
  limit?: number
}
