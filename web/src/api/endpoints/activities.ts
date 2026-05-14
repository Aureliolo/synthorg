import { apiClient, unwrapPaginated, type PaginatedResult } from '../client'
import type { ActivityEventType } from '../types/agents'
import type { ActivityEvent, ActivityItem } from '../types/analytics'
import type { PaginatedResponse, PaginationParams } from '../types/http'

export interface ActivityFilterParams extends PaginationParams {
  type?: ActivityEventType
  agent_id?: string
  last_n_hours?: 24 | 48 | 168
}

/** Map a REST ActivityEvent to the display-oriented ActivityItem shape. */
export function mapActivityEventToItem(event: ActivityEvent): ActivityItem {
  const relatedIds = event.related_ids ?? {}
  const agentId = relatedIds.agent_id ?? 'System'
  const taskId = relatedIds.task_id ?? null
  return {
    id: taskId ?? `${event.timestamp}-${event.event_type}-${agentId}`,
    timestamp: event.timestamp,
    agent_name: agentId,
    action_type: event.event_type,
    description: event.description ?? '',
    task_id: taskId,
    department: null,
  }
}

export async function listActivities(
  params?: ActivityFilterParams,
): Promise<PaginatedResult<ActivityItem>> {
  const response = await apiClient.get<PaginatedResponse<ActivityEvent>>('/activities', { params })
  const result = unwrapPaginated<ActivityEvent>(response)
  return { ...result, data: result.data.map(mapActivityEventToItem) }
}
