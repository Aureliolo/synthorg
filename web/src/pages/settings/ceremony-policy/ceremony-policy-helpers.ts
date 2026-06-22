import type {
  CeremonyPolicyConfig,
  CeremonyStrategyType,
  VelocityCalcType,
} from '@/api/types/ceremony-policy'
import type { Department } from '@/api/types/org'
import type { SettingEntry } from '@/api/types/settings'
import {
  CEREMONY_STRATEGY_TYPES,
  STRATEGY_DEFAULT_VELOCITY_CALC,
  VELOCITY_CALC_TYPES,
} from '@/stores/ceremony-policy-constants'

const THRESHOLD_MIN = 0.01
const THRESHOLD_MAX = 1.0
const DEPT_PAGE_LIMIT = 200

function isPlainObject(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function parseStrategyConfig(raw: string | undefined): { config: Record<string, unknown>; error: boolean } {
  // Only a missing value is "no config"; an empty string is malformed
  // JSON and must surface as a parse error rather than be silently ignored.
  if (raw === undefined) return { config: {}, error: false }
  try {
    const parsed: unknown = JSON.parse(raw)
    if (isPlainObject(parsed)) return { config: parsed, error: false }
  } catch {
    /* fall through to error */
  }
  return { config: {}, error: true }
}

function resolveStrategy(raw: string | undefined): CeremonyStrategyType {
  return raw && CEREMONY_STRATEGY_TYPES.includes(raw as CeremonyStrategyType)
    ? (raw as CeremonyStrategyType)
    : 'task_driven'
}

function resolveVelocityCalculator(raw: string | undefined, strategy: CeremonyStrategyType): VelocityCalcType {
  if (raw && VELOCITY_CALC_TYPES.includes(raw as VelocityCalcType)) return raw as VelocityCalcType
  return STRATEGY_DEFAULT_VELOCITY_CALC[strategy]
}

function resolveAutoTransition(raw: string | undefined): boolean {
  if (raw === undefined) return true
  return raw.toLowerCase() === 'true'
}

function resolveThreshold(raw: string | undefined): number {
  const n = Number(raw ?? String(THRESHOLD_MAX))
  return Number.isFinite(n) ? Math.min(Math.max(n, THRESHOLD_MIN), THRESHOLD_MAX) : THRESHOLD_MAX
}

export interface CeremonySnapshot {
  strategy: CeremonyStrategyType
  strategyConfig: Record<string, unknown>
  velocityCalculator: VelocityCalcType
  autoTransition: boolean
  transitionThreshold: number
  configParseError: boolean
}

function coordinationValue(entries: SettingEntry[], key: string): string | undefined {
  return entries.find((e) => e.definition.namespace === 'coordination' && e.definition.key === key)?.value
}

export function buildCeremonySnapshot(entries: SettingEntry[]): CeremonySnapshot {
  const { config, error: configParseError } = parseStrategyConfig(
    coordinationValue(entries, 'ceremony_strategy_config'),
  )
  const strategy = resolveStrategy(coordinationValue(entries, 'ceremony_strategy'))
  return {
    strategy,
    strategyConfig: config,
    velocityCalculator: resolveVelocityCalculator(
      coordinationValue(entries, 'ceremony_velocity_calculator'),
      strategy,
    ),
    autoTransition: resolveAutoTransition(coordinationValue(entries, 'ceremony_auto_transition')),
    transitionThreshold: resolveThreshold(coordinationValue(entries, 'ceremony_transition_threshold')),
    configParseError,
  }
}

export interface OverridesSnapshot {
  overrides: Record<string, CeremonyPolicyConfig | null>
  overridesParseError: boolean
}

export function buildOverridesSnapshot(entries: SettingEntry[]): OverridesSnapshot {
  const raw = coordinationValue(entries, 'ceremony_policy_overrides')
  // Only a missing value is "no overrides"; an empty string is malformed
  // JSON and must surface as a parse error rather than be silently ignored.
  if (raw === undefined) return { overrides: {}, overridesParseError: false }
  try {
    const parsed: unknown = JSON.parse(raw)
    if (isPlainObject(parsed)) {
      const overrides: Record<string, CeremonyPolicyConfig | null> = {}
      for (const [name, value] of Object.entries(parsed)) {
        // Each override is null (inherit) or a policy object; the per-field
        // shape is validated downstream where the override is applied, so we
        // only confirm the coarse null-or-object shape here. A value that is
        // neither must surface as a parse error rather than be silently
        // dropped, which would make corrupted settings look valid and get
        // saved back with the bad entries removed.
        if (value !== null && !isPlainObject(value)) {
          return { overrides: {}, overridesParseError: true }
        }
        overrides[name] = value
      }
      return { overrides, overridesParseError: false }
    }
  } catch {
    /* fall through to error */
  }
  return { overrides: {}, overridesParseError: true }
}

const COMMON_CEREMONIES = ['sprint_planning', 'standup', 'sprint_review', 'retrospective']

export function deriveCeremonyNames(overrides: Record<string, CeremonyPolicyConfig | null>): string[] {
  const names = new Set(Object.keys(overrides))
  for (const name of COMMON_CEREMONIES) names.add(name)
  return [...names].sort()
}

export interface DepartmentLoadResult {
  departments: Department[]
  cycleDetected: boolean
  failed: boolean
}

/**
 * Fetch all departments across paginated pages. Preserves partial
 * results on error, and bails (cycleDetected) if a malformed backend
 * repeats a cursor -- silently dropping items is the worst failure mode
 * for a settings page backed by server-side state.
 */
export async function fetchAllDepartments(): Promise<DepartmentLoadResult> {
  const departments: Department[] = []
  const seenCursors = new Set<string>()
  let cycleDetected = false
  let failed = false
  try {
    const { listDepartments } = await import('@/api/endpoints/company')
    let cursor: string | null = null
    for (;;) {
      const result = await listDepartments({ cursor, limit: DEPT_PAGE_LIMIT })
      departments.push(...result.data)
      if (!result.hasMore || !result.nextCursor) break
      if (seenCursors.has(result.nextCursor)) {
        cycleDetected = true
        break
      }
      seenCursors.add(result.nextCursor)
      cursor = result.nextCursor
    }
  } catch {
    failed = true
  }
  return { departments, cycleDetected, failed }
}
