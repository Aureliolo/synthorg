import type { AgentActivity } from '@/api/types/cockpit'

/**
 * Identify one activity row.
 *
 * Keyed on the EXECUTION rather than the agent or the task. An agent can hold
 * a task and a planning session at once, so the agent alone collapses them;
 * a run driving no task carries no task id at all. The execution is the one
 * value every row has and no two rows share.
 *
 * Two surfaces render these rows and both need the same answer, so it is
 * given once: a key that differs between them is a key that has already
 * started to drift.
 *
 * @param activity - The row to identify.
 * @returns A key unique within one snapshot.
 */
export function activityRowKey(activity: AgentActivity): string {
  return activity.execution_id ?? activity.task_id ?? activity.agent_id
}
