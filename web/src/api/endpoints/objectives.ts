import { apiClient, unwrap } from '../client'
import type { ApiResponse } from '../types/http'
import type {
  SubmitObjectiveAck,
  SubmitObjectivePayload as SubmitObjectivePayloadWire,
} from '@/api/types/plans'

export type { SubmitObjectiveAck }

// ``acceptance_criteria`` is required in the generated wire type (default
// ``[]``); the dialog omits it so the backend applies that default. Derive
// from the generated shape (no field/type drift) and keep it optional here.
export type SubmitObjectivePayload =
  Omit<SubmitObjectivePayloadWire, 'acceptance_criteria'>
  & { acceptance_criteria?: readonly string[] }

/**
 * Submit a goal/objective for decomposition. Returns the ``202 Accepted``
 * acknowledgement with a server-minted ``submission_id`` that correlates
 * to the spawned root task once the pipeline run materialises it.
 */
export async function submitObjective(
  payload: SubmitObjectivePayload,
): Promise<SubmitObjectiveAck> {
  const response = await apiClient.post<ApiResponse<SubmitObjectiveAck>>(
    '/objectives',
    payload,
  )
  return unwrap(response)
}
