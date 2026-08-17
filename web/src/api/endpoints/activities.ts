import { SYSTEM_ACTOR_NAME, UNKNOWN_AGENT_NAME } from '@/utils/agents'
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

/**
 * Map a REST ActivityEvent to the display-oriented ActivityItem shape.
 *
 * The actor's name comes from `actor_name`, which the backend resolves at the
 * read boundary. It is never the `agent_id` beside it: assigning the reference
 * to a field the feed renders as a name is what put a UUID at the head of every
 * row. An event with no agent at all is the system acting for itself, and one
 * whose agent the roster no longer covers gets the dashboard's own words.
 */
function actorNameOf(event: ActivityEvent, agentId: string | null): string {
  if (agentId === null) return SYSTEM_ACTOR_NAME
  return event.actor_name ?? UNKNOWN_AGENT_NAME
}

function descriptionOf(event: ActivityEvent): string {
  const subject = event.subject_title
  return subject === null ? event.description : `${subject}: ${event.description}`
}

function mapActivityEventToItem(event: ActivityEvent): ActivityItem {
  const relatedIds = event.related_ids
  const agentId = relatedIds['agent_id'] ?? null
  const taskId = relatedIds['task_id'] ?? null
  return {
    // Always composite. Keying on the task id alone collapsed every event
    // about one task to a single React key, and one task metric emits both a
    // `task_started` and a `task_completed` while a cost record adds a third,
    // so three rows shared a key and swapped content on re-render.
    id: `${event.timestamp}-${event.event_type}-${taskId ?? agentId ?? 'system'}`,
    timestamp: event.timestamp,
    agent_name: actorNameOf(event, agentId),
    agent_role: null,
    action_type: event.event_type,
    description: descriptionOf(event),
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
