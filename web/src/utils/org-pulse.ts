/**
 * What the org is doing, and what is stopping it.
 *
 * An operator looking at sixteen tasks with none completed is asking one
 * question, and a success-rate gauge cannot answer it: a rate needs completed
 * runs to average, so the state that raises the question is exactly the state
 * that empties the number.
 *
 * These derivations answer it from what is already on the wire, including
 * `GET /subsystems`, which reports why a subsystem is not up. Without that on a
 * surface, the reason an org sits idle lives only in the wiring log.
 */

import type { OverviewMetrics } from '@/api/types/analytics'
import type { BlockedReason } from '@/api/types/enums'
import type { SubsystemPhase, SubsystemReport } from '@/api/types/subsystems'
import type { Task } from '@/api/types/tasks'
import { ROUTES } from '@/router/routes'
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
const BLOCKED_REASON_TEXT: Record<BlockedReason, string> = {
  oracle_escalated: 'waiting for your decision on an escalated review',
  wave_released: 'waiting for the scheduler to pick the subtask up',
  reviewer_unstaffed: 'waiting for a Completion Reviewer to be staffed',
  red_team_unstaffed: 'waiting for a Red Team reviewer to be staffed',
  no_capable_agent: 'no agent capable enough to take it on',
}

/** The sentinel for a park whose writer named no reason. */
const UNNAMED_REASON = 'unnamed'

/**
 * A failed phase is a fault; a waiting one may simply be mid-boot.
 *
 * Typed against the generated union rather than bare strings, so a phase added
 * to the backend enum is a compile error here instead of a silent miss. Every
 * phase other than `active` is reported, because `degraded` means up with a
 * requirement it named gone, and `disabled` answers "why is this not up"
 * better than silence does.
 */
const CRITICAL_PHASES: ReadonlySet<SubsystemPhase> = new Set<SubsystemPhase>([
  'failed',
  'unreachable',
])

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
      href: ROUTES.TASKS,
    },
  ]
}

/**
 * What a park is waiting on, in operator words.
 *
 * Only the sentinel gets the "nobody said" wording. A reason the writer DID
 * state but this map has not learned yet is still a stated reason, and denying
 * that is the opposite of the fact `blocked_reason` exists to carry.
 */
function parkDetail(reason: string): string {
  if (reason === UNNAMED_REASON) {
    return 'parked without a stated reason, so nothing is watching for it'
  }
  // `Object.hasOwn` rather than an index-and-`??`: the index expression's type
  // asserts the key is present, so the fallback is unreachable to the checker
  // and a reason added to the enum but not to this map would render undefined.
  return Object.hasOwn(BLOCKED_REASON_TEXT, reason)
    ? BLOCKED_REASON_TEXT[reason as BlockedReason]
    : formatLabel(reason)
}

/** Parked work, grouped by the answer each park is waiting on. */
function blockedTaskBlockers(blockedTasks: readonly Task[]): Blocker[] {
  const byReason = new Map<string, number>()
  for (const task of blockedTasks) {
    // An unnamed reason is not a synonym for any of them: the writer did not
    // say, and saying so is more use than picking one.
    const reason = task.blocked_reason ?? UNNAMED_REASON
    byReason.set(reason, (byReason.get(reason) ?? 0) + 1)
  }
  return [...byReason.entries()]
    .sort((left, right) => right[1] - left[1])
    .map(([reason, count]) => ({
      id: `blocked:${reason}`,
      severity: 'warning' as const,
      title: `${pluralTasks(count)} blocked`,
      detail: parkDetail(reason),
      href: ROUTES.TASKS,
    }))
}

/**
 * Why this subsystem is not doing its job, in its own words.
 *
 * `detail` and `waiting_on` are joined rather than alternated: on `unreachable`
 * both are populated and each says something different, `waiting_on` naming the
 * capabilities and `detail` naming the owner to go and fix. Preferring one hid
 * the other on the single phase that carries both.
 */
function subsystemDetail(report: SubsystemReport): string | null {
  const waiting =
    report.waiting_on.length > 0
      ? `waiting on ${report.waiting_on.map(formatLabel).join(', ')}`
      : null
  return [report.detail, waiting].filter((part) => part !== null).join(' -- ') || null
}

/** Subsystems that are not up, each naming its own condition. */
function subsystemBlockers(subsystems: readonly SubsystemReport[]): Blocker[] {
  return subsystems
    .filter((report) => report.phase !== 'active')
    .map((report) => ({
      id: `subsystem:${report.name}`,
      severity: CRITICAL_PHASES.has(report.phase)
        ? ('critical' as const)
        : ('warning' as const),
      title: `${formatLabel(report.name)} is ${report.phase}`,
      // Shown verbatim: an operator can act on "memory.embedder_model is unset"
      // and cannot act on "see the wiring log".
      detail: subsystemDetail(report),
      href: ROUTES.SETTINGS,
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
 * Queued means waiting for a picker: filed, or assigned and not yet started.
 * Summed from `tasks_by_status` rather than subtracted from a total, so a
 * status added later cannot silently start counting as queued.
 *
 * Deliberately excluded: `in_review` is a dispatched review session, not
 * backlog; `blocked` is counted by the blockers list beside this number, and
 * counting it twice would read as more work waiting than there is; and the
 * human-waiting statuses (`awaiting_input`, `auth_required`, `interrupted`,
 * `suspended`) wait on a person rather than on capacity.
 */
const QUEUED_STATUSES = ['created', 'assigned'] as const

export function computeQueue(overview: OverviewMetrics | null): PulseQueue {
  if (overview === null) return { queued: 0, idleAgents: 0 }
  const queued = QUEUED_STATUSES.reduce(
    (total, status) => total + (overview.tasks_by_status[status] ?? 0),
    0,
  )
  return { queued, idleAgents: overview.idle_agents_count }
}
