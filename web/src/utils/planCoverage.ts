/**
 * What the plan says it delivers, read against what the objective asked for.
 *
 * Two derivations, both answering "is anything left unanswered": which
 * objective criteria no item advances, and which of the planner's own open
 * questions the plan already settles. Separate from `plans.ts`, which derives
 * the per-item review signals.
 */

import type { PlanItem } from '@/api/types/plans'
import { buildPlanTree, workstreamOf } from '@/utils/planTree'

// ── Success-criteria coverage ──────────────────────────────────────────────

export interface CoverageEntry {
  /** An objective acceptance criterion. */
  readonly criterion: string
  /** Titles of the plan items that advance it (empty when uncovered). */
  readonly coveredBy: readonly string[]
  /**
   * Titles of the workstreams those items sit under, de-duplicated.
   *
   * What a reviewer reads on a recursive plan: a criterion advanced by nine
   * leaves across two tracks is two names, and the nine titles it expands to
   * are a wall that says less. Equal to `coveredBy` on a flat plan, where
   * every item IS its own workstream.
   */
  readonly coveredByWorkstream: readonly string[]
}

export interface PlanCoverage {
  /** One entry per objective criterion, in objective order. */
  readonly entries: readonly CoverageEntry[]
  /** Criteria at least one item advances. */
  readonly covered: number
  /** Total objective criteria. */
  readonly total: number
  /** Criteria no item advances (the gaps a reviewer must close). */
  readonly uncovered: readonly string[]
}

/** Normalise a criterion for matching (trim + case-fold) so near-copies align. */
function coverageKey(text: string): string {
  return text.trim().toLowerCase()
}

/** Append `value` under `key` unless it is already there. */
function addUnique(index: Map<string, string[]>, key: string, value: string): void {
  const bucket = index.get(key) ?? []
  if (!bucket.includes(value)) bucket.push(value)
  index.set(key, bucket)
}

/**
 * Map each objective acceptance criterion to the plan items that advance it
 * (via their ``satisfies`` tags), so the review surface can flag any criterion
 * nothing covers. Matching is trim + case-insensitive so a verbatim-ish copy
 * still aligns. Returns an empty coverage when the objective declared no
 * criteria (nothing to check).
 */
export function derivePlanCoverage(
  objectiveCriteria: readonly string[],
  items: readonly PlanItem[],
): PlanCoverage {
  const tree = buildPlanTree(items)
  const byItem = new Map<string, string[]>()
  const byTrack = new Map<string, string[]>()
  for (const item of items) {
    const track = workstreamOf(tree, item.id)?.title ?? item.title
    for (const tag of item.satisfies) {
      addUnique(byItem, coverageKey(tag), item.title)
      addUnique(byTrack, coverageKey(tag), track)
    }
  }
  const entries: CoverageEntry[] = objectiveCriteria.map((criterion) => ({
    criterion,
    coveredBy: byItem.get(coverageKey(criterion)) ?? [],
    coveredByWorkstream: byTrack.get(coverageKey(criterion)) ?? [],
  }))
  const uncovered = entries
    .filter((entry) => entry.coveredBy.length === 0)
    .map((entry) => entry.criterion)
  return {
    entries,
    covered: entries.length - uncovered.length,
    total: entries.length,
    uncovered,
  }
}

// ── Open questions the plan already answers ────────────────────────────────

export interface QuestionAnswer {
  /** The open question as the planner wrote it. */
  readonly question: string
  /** Title of the item whose acceptance criteria settle it, if any. */
  readonly settledBy: string | null
}

/**
 * Words carried by nearly every question, so matching on them would settle a
 * question against any criterion at all.
 */
const QUESTION_NOISE: ReadonlySet<string> = new Set([
  'a', 'an', 'and', 'are', 'as', 'at', 'be', 'by', 'can', 'do', 'does', 'for',
  'from', 'has', 'have', 'how', 'in', 'is', 'it', 'of', 'on', 'or', 'should',
  'so', 'that', 'the', 'this', 'to', 'we', 'what', 'when', 'where', 'which',
  'who', 'why', 'will', 'with',
])

/** The distinctive words of a phrase: lower-cased, punctuation-free, no noise. */
function contentWords(text: string): readonly string[] {
  return text
    .toLowerCase()
    .split(/[^a-z0-9]+/)
    .filter((word) => word.length > 1 && !QUESTION_NOISE.has(word))
}

/**
 * Pair every open question with the plan item whose acceptance criteria already
 * settle it, matching when the criteria carry every distinctive word of the
 * question.
 *
 * The result separates rather than hides: a question the plan answers stops
 * demanding input, but the operator can still see it and the item it was
 * matched against, because a wrong match must cost attention rather than a
 * question they never got to answer.
 */
export function answeredQuestions(
  questions: readonly string[],
  items: readonly PlanItem[],
): readonly QuestionAnswer[] {
  const criteria = items.map((item) => ({
    title: item.title,
    words: new Set(contentWords(item.acceptance_criteria.join(' '))),
  }))
  return questions.map((question) => {
    const words = contentWords(question)
    const match =
      words.length === 0
        ? undefined
        : criteria.find((item) => words.every((word) => item.words.has(word)))
    return { question, settledBy: match?.title ?? null }
  })
}
