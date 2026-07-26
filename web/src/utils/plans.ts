/**
 * Plan-review derivations: the signals a reviewer needs to read a plan like a
 * proposal rather than a task list. Colour vocabularies for effort (complexity)
 * and consequence (stakes), per-item guard-risk flags, whole-plan summary
 * stats, and the critical path through the dependency graph. Pure functions so
 * the review surfaces stay declarative and the logic is unit-testable.
 */

import type { Complexity, Stakes, TaskStructure } from '@/api/types/enums'
import type { PlanItem, PlanItemPayload } from '@/api/types/plans'
import type { StatusPillTone } from '@/components/ui/status-pill'
import { ROUTES } from '@/router/routes'

/** Deep-link to a plan's review workspace. */
export function planDetailPath(planId: string): string {
  return ROUTES.PLAN_DETAIL.replace(':planId', encodeURIComponent(planId))
}

/**
 * Project a durable plan item back onto its edit payload, so a targeted change
 * (e.g. recording a decision's chosen option) can round-trip the untouched
 * items through the wholesale edit endpoint without the editor's form state.
 */
export function planItemToPayload(item: PlanItem): PlanItemPayload {
  return {
    id: item.id,
    title: item.title,
    description: item.description,
    owner: item.owner,
    dependencies: item.dependencies,
    acceptance_criteria: item.acceptance_criteria,
    expected_artifacts: item.expected_artifacts,
    required_skills: item.required_skills,
    required_tags: item.required_tags,
    estimated_complexity: item.estimated_complexity,
    stakes: item.stakes,
    kind: item.kind,
    options: item.options,
    chosen_option_id: item.chosen_option_id,
    satisfies: item.satisfies,
  }
}

/** Stable DOM id for a plan item's card, so the attention panel can jump to it. */
export function planItemAnchorId(itemId: string): string {
  return `plan-item-${itemId}`
}

// ── Effort (complexity) and consequence (stakes) vocabularies ──────────────
// Complexity grades effort/uncertainty; stakes grades the cost of being wrong.
// Both climb the shared danger palette so a scan reads severity by colour.

export const COMPLEXITY_TONE: Record<Complexity, StatusPillTone> = {
  simple: 'text-secondary',
  medium: 'accent',
  complex: 'warning',
  epic: 'danger',
}

export const COMPLEXITY_LABEL: Record<Complexity, string> = {
  simple: 'Simple',
  medium: 'Medium',
  complex: 'Complex',
  epic: 'Epic',
}

export const STAKES_TONE: Record<Stakes, StatusPillTone> = {
  low: 'text-secondary',
  normal: 'accent',
  high: 'warning',
  critical: 'danger',
}

export const STAKES_LABEL: Record<Stakes, string> = {
  low: 'Low',
  normal: 'Normal',
  high: 'High',
  critical: 'Critical',
}

const HIGH_COMPLEXITY: ReadonlySet<Complexity> = new Set<Complexity>(['complex', 'epic'])
const HIGH_STAKES: ReadonlySet<Stakes> = new Set<Stakes>(['high', 'critical'])

export function isHighComplexity(item: PlanItem): boolean {
  return HIGH_COMPLEXITY.has(item.estimated_complexity)
}

export function isHighStakes(item: PlanItem): boolean {
  return HIGH_STAKES.has(item.stakes)
}

// ── Guard-risk flags: the things a reviewer must actually check ────────────

export interface PlanItemFlag {
  /** Stable key for React lists. */
  readonly key: string
  /** Short pill label. */
  readonly label: string
  /** Shared-palette tone. */
  readonly tone: StatusPillTone
  /** One line on why this item wants a second look. */
  readonly detail: string
}

export interface ItemFlagContext {
  readonly onCriticalPath: boolean
}

/**
 * Reasons this item warrants review attention, most severe first. Stakes and
 * complexity carry their own graded tone; the guard-risk gaps (no owner, no
 * acceptance criteria) and the critical-path membership are fixed tones.
 */
export function itemFlags(item: PlanItem, ctx: ItemFlagContext): readonly PlanItemFlag[] {
  const flags: PlanItemFlag[] = []
  if (isHighStakes(item)) {
    flags.push({
      key: 'stakes',
      label: `${STAKES_LABEL[item.stakes]} stakes`,
      tone: STAKES_TONE[item.stakes],
      detail: 'High cost if this item is done wrong.',
    })
  }
  if (isHighComplexity(item)) {
    flags.push({
      key: 'complexity',
      label: `${COMPLEXITY_LABEL[item.estimated_complexity]} effort`,
      tone: COMPLEXITY_TONE[item.estimated_complexity],
      detail: 'Large or uncertain scope; expect it to dominate the timeline.',
    })
  }
  if (item.owner === null) {
    flags.push({
      key: 'unowned',
      label: 'Unassigned',
      tone: 'warning',
      detail: 'No role or agent owns this item yet.',
    })
  }
  if (item.acceptance_criteria.length === 0) {
    flags.push({
      key: 'no-criteria',
      label: 'No acceptance criteria',
      tone: 'warning',
      detail: 'Nothing defines when this item is done.',
    })
  }
  if (ctx.onCriticalPath) {
    flags.push({
      key: 'critical-path',
      label: 'Critical path',
      tone: 'accent',
      detail: 'A slip here slips the whole plan.',
    })
  }
  return flags
}

/** An item needs review attention when it carries any flag. */
export function itemNeedsAttention(item: PlanItem, ctx: ItemFlagContext): boolean {
  return itemFlags(item, ctx).length > 0
}

// ── Critical path through the dependency graph ─────────────────────────────

/** Map of item id to its display title, for resolving dependency references. */
export function planItemTitleMap(items: readonly PlanItem[]): ReadonlyMap<string, string> {
  return new Map(items.map((item) => [item.id, item.title]))
}

/** Resolve an item's dependency ids to the titles of the items it depends on. */
export function dependencyTitles(
  item: PlanItem,
  titleById: ReadonlyMap<string, string>,
): readonly string[] {
  return item.dependencies.map((dep) => titleById.get(dep) ?? dep)
}

/**
 * The longest dependency chain through the plan, as the set of item ids on it.
 * Dependencies point at predecessors (items that must finish first), so the
 * critical path is the longest predecessor chain; delays on it delay delivery.
 * Returns an empty set when no chain spans two or more items (nothing to flag).
 */
export function computeCriticalPath(items: readonly PlanItem[]): ReadonlySet<string> {
  const byId = new Map(items.map((item) => [item.id, item]))
  const chainLength = new Map<string, number>()
  const predecessor = new Map<string, string | null>()
  const visiting = new Set<string>()

  function longestChainEndingAt(id: string): number {
    const cached = chainLength.get(id)
    if (cached !== undefined) return cached
    if (visiting.has(id)) return 1 // defensive cycle guard; the backend rejects cycles
    visiting.add(id)
    const item = byId.get(id)
    let best = 0
    let bestPred: string | null = null
    for (const dep of item?.dependencies ?? []) {
      if (!byId.has(dep)) continue // ignore dangling dependency ids
      const depLength = longestChainEndingAt(dep)
      if (depLength > best) {
        best = depLength
        bestPred = dep
      }
    }
    visiting.delete(id)
    const total = best + 1
    chainLength.set(id, total)
    predecessor.set(id, bestPred)
    return total
  }

  let endId: string | null = null
  let longest = 0
  for (const item of items) {
    const length = longestChainEndingAt(item.id)
    if (length > longest) {
      longest = length
      endId = item.id
    }
  }

  const path = new Set<string>()
  if (longest < 2) return path // a single item is not a path
  let cursor = endId
  // Stop on a repeat: the recursion guard can still leave a cyclic predecessor
  // chain (a -> b -> a) on malformed input, which would loop forever here.
  while (cursor !== null && !path.has(cursor)) {
    path.add(cursor)
    cursor = predecessor.get(cursor) ?? null
  }
  return path
}

/**
 * The critical path only carries information when the plan actually branches: on
 * a sequential plan the "path" is every item (zero signal), and even on a
 * classified-parallel plan a chain that spans the whole set says nothing. Return
 * the path only when it is a strict subset of a non-sequential plan; otherwise
 * an empty set, so the surface suppresses the (degenerate) critical-path signal.
 */
export function criticalPathFor(
  items: readonly PlanItem[],
  taskStructure: TaskStructure,
): ReadonlySet<string> {
  if (taskStructure === 'sequential') return new Set<string>()
  const path = computeCriticalPath(items)
  return path.size < items.length ? path : new Set<string>()
}

// ── Execution waves (the timeline) ─────────────────────────────────────────

export interface PlanWave {
  /** Zero-based execution order: everything in wave N can run once wave N-1 is done. */
  readonly index: number
  /** Items that run together in this wave (no dependency between them). */
  readonly items: readonly PlanItem[]
}

/**
 * Group items into execution waves by dependency depth, so the plan reads as a
 * timeline: wave 0 is everything with no prerequisites, and each later wave is
 * the work that unlocks once the previous one lands. Items within a wave have no
 * dependency between them, so they run in parallel. This is the legible form of
 * the plan's parallelism, derived from the DAG (no persisted structure).
 */
export function computeWaves(items: readonly PlanItem[]): readonly PlanWave[] {
  const byId = new Map(items.map((item) => [item.id, item]))
  const depthCache = new Map<string, number>()
  const visiting = new Set<string>()

  function depthOf(id: string): number {
    const cached = depthCache.get(id)
    if (cached !== undefined) return cached
    if (visiting.has(id)) return 0 // defensive cycle guard; backend rejects cycles
    visiting.add(id)
    const item = byId.get(id)
    let depth = 0
    for (const dep of item?.dependencies ?? []) {
      if (byId.has(dep)) depth = Math.max(depth, depthOf(dep) + 1)
    }
    visiting.delete(id)
    depthCache.set(id, depth)
    return depth
  }

  const waves = new Map<number, PlanItem[]>()
  for (const item of items) {
    const depth = depthOf(item.id)
    const bucket = waves.get(depth) ?? []
    bucket.push(item)
    waves.set(depth, bucket)
  }
  return [...waves.entries()]
    .sort(([a], [b]) => a - b)
    .map(([index, waveItems]) => ({ index, items: waveItems }))
}

// ── Success-criteria coverage ──────────────────────────────────────────────

export interface CoverageEntry {
  /** An objective acceptance criterion. */
  readonly criterion: string
  /** Titles of the plan items that advance it (empty when uncovered). */
  readonly coveredBy: readonly string[]
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
  const coveringTitles = new Map<string, string[]>()
  for (const item of items) {
    for (const tag of item.satisfies) {
      const key = coverageKey(tag)
      const bucket = coveringTitles.get(key) ?? []
      if (!bucket.includes(item.title)) bucket.push(item.title)
      coveringTitles.set(key, bucket)
    }
  }
  const entries: CoverageEntry[] = objectiveCriteria.map((criterion) => ({
    criterion,
    coveredBy: coveringTitles.get(coverageKey(criterion)) ?? [],
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

// ── Staffing / team summary ────────────────────────────────────────────────

export interface StaffingEntry {
  /** The owning role or agent name. */
  readonly owner: string
  /** Items this owner is accountable for. */
  readonly itemCount: number
  /** How many of those items are high or critical stakes. */
  readonly highStakesCount: number
  /** Whether this owner carries a bottleneck share of the plan. */
  readonly overloaded: boolean
}

export interface PlanStaffing {
  /** One entry per distinct owner, busiest first. */
  readonly roles: readonly StaffingEntry[]
  /** Items with no owner (a staffing gap). */
  readonly unassigned: number
  /** Distinct owners on the plan. */
  readonly totalOwners: number
}

/** An owner below this item count is never flagged a bottleneck on a small plan. */
const OVERLOAD_MIN_ITEMS = 3

/**
 * Summarise who is accountable for the plan: each owner's item load, how much of
 * it is high-stakes, and whether they carry a bottleneck share (at least half a
 * non-trivial plan while others also own work), plus the count of unassigned
 * items. Pure derivation from item owners; no persisted team structure.
 */
export function derivePlanStaffing(items: readonly PlanItem[]): PlanStaffing {
  const byOwner = new Map<string, { itemCount: number; highStakesCount: number }>()
  let unassigned = 0
  for (const item of items) {
    if (item.owner === null) {
      unassigned += 1
      continue
    }
    const entry = byOwner.get(item.owner) ?? { itemCount: 0, highStakesCount: 0 }
    entry.itemCount += 1
    if (isHighStakes(item)) entry.highStakesCount += 1
    byOwner.set(item.owner, entry)
  }
  const totalOwners = byOwner.size
  const bottleneckAt = Math.max(OVERLOAD_MIN_ITEMS, Math.ceil(items.length / 2))
  const roles: StaffingEntry[] = [...byOwner.entries()]
    .map(([owner, entry]) => ({
      owner,
      itemCount: entry.itemCount,
      highStakesCount: entry.highStakesCount,
      overloaded: totalOwners > 1 && entry.itemCount >= bottleneckAt,
    }))
    .sort((a, b) => b.itemCount - a.itemCount || a.owner.localeCompare(b.owner))
  return { roles, unassigned, totalOwners }
}

// ── Whole-plan summary stats ───────────────────────────────────────────────

export interface PlanStats {
  readonly totalItems: number
  readonly highStakes: number
  readonly highComplexity: number
  readonly unowned: number
  readonly missingCriteria: number
  readonly dependencyEdges: number
  readonly criticalPathLength: number
  readonly flaggedItems: number
}

/** Aggregate the review signals across every item in the plan. */
export function derivePlanStats(
  items: readonly PlanItem[],
  criticalPath: ReadonlySet<string>,
): PlanStats {
  let highStakes = 0
  let highComplexity = 0
  let unowned = 0
  let missingCriteria = 0
  let dependencyEdges = 0
  let flaggedItems = 0
  for (const item of items) {
    if (isHighStakes(item)) highStakes += 1
    if (isHighComplexity(item)) highComplexity += 1
    if (item.owner === null) unowned += 1
    if (item.acceptance_criteria.length === 0) missingCriteria += 1
    dependencyEdges += item.dependencies.length
    if (itemNeedsAttention(item, { onCriticalPath: criticalPath.has(item.id) })) {
      flaggedItems += 1
    }
  }
  return {
    totalItems: items.length,
    highStakes,
    highComplexity,
    unowned,
    missingCriteria,
    dependencyEdges,
    criticalPathLength: criticalPath.size,
    flaggedItems,
  }
}
