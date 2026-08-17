/**
 * What the org is doing, and what is stopping it.
 *
 * The dashboard used to answer neither. It showed a health gauge reading 0% and
 * a column of departments reading N/A, because health is a task-success rate and
 * a department that has run nothing has no success rate to report. An operator
 * looking at sixteen tasks and none completed learned nothing from it.
 *
 * These derivations answer the question that state actually raises. Every input
 * is already on the wire; the one thing that was missing from the dashboard is
 * `GET /subsystems`, which reports why a subsystem is not up and had no UI at
 * all, so the reason an org was idle lived only in the wiring log.
 */

import type { OverviewMetrics } from '@/api/types/analytics'
import type { SubsystemReport } from '@/api/types/subsystems'
import type { Task } from '@/api/types/tasks'
import { formatLabel } from '@/utils/format'

/** How much an operator should care, worst first. */
export type BlockerSeverity = 'critical' | 'warning'

/** One thing standing between the org and progress. */
export interface Blocker {
  /** Stable key for React, and for asserting on a specific row. */
  readonly id: string
  readonly severity: BlockerSeverity
  /** What is wrong, in one line. */
  readonly title: string
  /** Why, in the words of whoever knows: a decline reason, or a park reason. */
  readonly detail: string | null
  /** Where the operator goes to act on it, when there is somewhere. */
  readonly href: string | null
}

/**
 * A park reason in operator words.
 *
 * The wire values are the loop's own vocabulary, and `blocked_reason` exists
 * precisely because BLOCKED is reached from directions that need different
 * answers, so each is phrased as the action it waits on.
 */
const BLOCKED_REASON_TEXT: Record<string, string> = {
  oracle_escalated: 'waiting for your decision on an escalated review',
  wave_released: 'waiting for the scheduler to pick the subtask up',
  reviewer_unstaffed: 'waiting for a Completion Reviewer to be staffed',
  red_team_unstaffed: 'waiting for a Red Team reviewer to be staffed',
  no_capable_agent: 'no agent capable enough to take it on',
}

/** Phases that mean a subsystem is not doing its job. */
const NOT_UP_PHASES = new Set(['blocked', 'failed', 'unreachable', 'waiting'])

/** A failed phase is a fault; a waiting one may simply be mid-boot. */
const CRITICAL_PHASES = new Set(['failed', 'unreachable'])

function pluralTasks(count: number): string {
  return count === 1 ? '1 task' : `${count} tasks`
}

/** Runs that finished without producing anything, plus outright failures. */
function outcomeBlockers(overview: OverviewMetrics): Blocker[] {
  const { failed, empty, succeeded } = overview.task_outcomes
  const unproductive = failed + empty
  if (unproductive === 0) return []
  const total = unproductive + succeeded
  return [
    {
      id: 'runs:unproductive',
      severity: succeeded === 0 ? 'critical' : 'warning',
      title:
        `${unproductive} of ${total} runs produced nothing`
        + (failed > 0 && empty > 0 ? ` (${failed} failed, ${empty} empty)` : ''),
      detail:
        succeeded === 0
          ? 'No run has produced output yet, so nothing the org started has landed.'
          : null,
      href: '/tasks',
    },
  ]
}

/** Parked work, grouped by the answer each park is waiting on. */
function blockedTaskBlockers(blockedTasks: readonly Task[]): Blocker[] {
  const byReason = new Map<string, number>()
  for (const task of blockedTasks) {
    // An unnamed reason is not a synonym for any of them: the writer did not
    // say, and saying so is more use than picking one.
    const reason = task.blocked_reason ?? 'unnamed'
    byReason.set(reason, (byReason.get(reason) ?? 0) + 1)
  }
  return [...byReason.entries()]
    .sort((left, right) => right[1] - left[1])
    .map(([reason, count]) => ({
      id: `blocked:${reason}`,
      severity: 'warning' as const,
      title: `${pluralTasks(count)} blocked`,
      detail:
        BLOCKED_REASON_TEXT[reason]
        ?? 'parked without a stated reason, so nothing is watching for it',
      href: '/tasks',
    }))
}

/** Subsystems that are not up, each naming its own condition. */
function subsystemBlockers(subsystems: readonly SubsystemReport[]): Blocker[] {
  return subsystems
    .filter((report) => NOT_UP_PHASES.has(report.phase))
    .map((report) => ({
      id: `subsystem:${report.name}`,
      severity: CRITICAL_PHASES.has(report.phase)
        ? ('critical' as const)
        : ('warning' as const),
      title: `${formatLabel(report.name)} is ${report.phase}`,
      // `detail` is the subsystem's own answer to "why is this not up", and
      // `waiting_on` names the capabilities it lacks. Shown verbatim: an
      // operator can act on "memory.embedder_model is unset" and cannot act on
      // "see the wiring log".
      detail:
        report.detail
        ?? (report.waiting_on.length > 0
          ? `waiting on ${report.waiting_on.map(formatLabel).join(', ')}`
          : null),
      href: '/settings',
    }))
}

export interface OrgPulseInputs {
  readonly overview: OverviewMetrics | null
  readonly blockedTasks: readonly Task[]
  readonly subsystems: readonly SubsystemReport[]
}

/**
 * Everything standing between the org and progress, worst first.
 *
 * An empty list is a real all-clear, which is why nothing here invents a row for
 * a healthy org: the panel says so in its own words instead of showing a zero.
 */
export function computeBlockers(inputs: OrgPulseInputs): Blocker[] {
  const blockers = [
    ...(inputs.overview === null ? [] : outcomeBlockers(inputs.overview)),
    ...subsystemBlockers(inputs.subsystems),
    ...blockedTaskBlockers(inputs.blockedTasks),
  ]
  const rank = (severity: BlockerSeverity): number => (severity === 'critical' ? 0 : 1)
  return blockers.sort((left, right) => rank(left.severity) - rank(right.severity))
}

/** The queue behind the work currently running. */
export interface PulseQueue {
  readonly queued: number
  readonly idleAgents: number
}

/**
 * How much work is waiting, and how many agents are not on any.
 *
 * Queued counts every task that has not reached a terminal or in-flight status,
 * summed from `tasks_by_status` rather than subtracted from a total, so a status
 * added later cannot silently start counting as queued.
 */
const QUEUED_STATUSES = ['created', 'assigned', 'in_review'] as const

export function computeQueue(overview: OverviewMetrics | null): PulseQueue {
  if (overview === null) return { queued: 0, idleAgents: 0 }
  const queued = QUEUED_STATUSES.reduce(
    (total, status) => total + (overview.tasks_by_status[status] ?? 0),
    0,
  )
  return { queued, idleAgents: overview.idle_agents_count }
}
