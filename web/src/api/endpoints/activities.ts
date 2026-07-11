import { apiClient, unwrapPaginated, type PaginatedResult } from '../client'
import type { ActivityEventType } from '../types/agents'
import type { ActivityEvent, ActivityItem } from '../types/analytics'
import type { RunOutcome } from '../types/enums'
import type { PaginatedResponse, PaginationParams } from '../types/http'

export interface ActivityFilterParams extends PaginationParams {
  type?: ActivityEventType
  agent_id?: string
  last_n_hours?: 24 | 48 | 168
}

/**
 * Run outcome implied by a terminal task activity type, so REST-sourced rows
 * carry the same failure-aware badge as WS-sourced ones. Only the terminal
 * task events map; every other activity type has no run outcome.
 */
const ACTIVITY_EVENT_OUTCOME: Partial<Record<ActivityEventType, RunOutcome>> = {
  task_completed: 'succeeded',
  task_failed: 'failed',
  task_empty: 'empty',
}

/** Map a REST ActivityEvent to the display-oriented ActivityItem shape. */
function mapActivityEventToItem(event: ActivityEvent): ActivityItem {
  const relatedIds = event.related_ids
  const agentId = relatedIds['agent_id'] ?? 'System'
  const taskId = relatedIds['task_id'] ?? null
  return {
    id: taskId ?? `${event.timestamp}-${event.event_type}-${agentId}`,
    timestamp: event.timestamp,
    agent_name: agentId,
    action_type: event.event_type,
    description: event.description,
    task_id: taskId,
    department: null,
    run_outcome: ACTIVITY_EVENT_OUTCOME[event.event_type] ?? null,
  }
}

export async function listActivities(
  params?: ActivityFilterParams,
): Promise<PaginatedResult<ActivityItem>> {
  const response = await apiClient.get<PaginatedResponse<ActivityEvent>>('/activities', { params })
  const result = unwrapPaginated<ActivityEvent>(response)
  return { ...result, data: result.data.map(mapActivityEventToItem) }
}
