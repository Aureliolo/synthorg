import type {
  ActiveSteeringDirective,
  InterventionKind,
  SteeringIssueResult,
  SteeringSupersessionResult,
  SupersedeMode,
} from '@/api/types'

import { apiClient, unwrap, withSignal } from '../client'
import type { ApiResponse } from '../types/http'

/** Body for issuing a project-scoped steering directive. */
export interface IssueSteeringPayload {
  project_id: string
  kind: InterventionKind
  text: string
  narrow_task_ids?: readonly string[]
  narrow_agent_ids?: readonly string[]
  supersede_task_ids?: readonly string[]
  supersede_mode?: SupersedeMode
}

/** Issue a steering directive; in-flight agents adopt it at the next safe boundary. */
export async function issueSteering(
  payload: IssueSteeringPayload,
): Promise<SteeringIssueResult> {
  const response = await apiClient.post<ApiResponse<SteeringIssueResult>>(
    '/cockpit/steering',
    payload,
  )
  return unwrap(response)
}

/** List the active steering directives for a project (operator board). */
export async function listActiveSteering(
  projectId: string,
  signal?: AbortSignal,
): Promise<readonly ActiveSteeringDirective[]> {
  const response = await apiClient.get<ApiResponse<readonly ActiveSteeringDirective[]>>(
    '/cockpit/steering',
    withSignal(signal, { params: { project_id: projectId } }),
  )
  return unwrap(response)
}

/** Confirm the operator-edited obsolete-task set for a directive (cancels them). */
export async function confirmSupersession(
  directiveId: string,
  projectId: string,
  taskIds: readonly string[],
): Promise<SteeringSupersessionResult> {
  const response = await apiClient.post<ApiResponse<SteeringSupersessionResult>>(
    `/cockpit/steering/${encodeURIComponent(directiveId)}/supersede`,
    { project_id: projectId, task_ids: taskIds },
  )
  return unwrap(response)
}
