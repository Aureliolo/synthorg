import type { TemplateInfoResponse } from '@/api/types/setup'
import { makeEnumParser } from '@/utils/type-guards'

/** Template size tags used for the approachability heuristic. */
const TAG_SOLO = 'solo'
const TAG_SMALL_TEAM = 'small-team'

/** "What are you building?" -- drives the personalised recommendation. */
export type BuildGoal =
  | 'any'
  | 'prototype'
  | 'product'
  | 'services'
  | 'data'
  | 'research'
  | 'security'
  | 'enterprise'

export const GOAL_OPTIONS: readonly { value: BuildGoal; label: string }[] = [
  { value: 'prototype', label: 'Prototype / solo project' },
  { value: 'product', label: 'Product & engineering' },
  { value: 'services', label: 'Client & agency work' },
  { value: 'data', label: 'Data & analytics' },
  { value: 'research', label: 'Research' },
  { value: 'security', label: 'Security & compliance' },
  { value: 'enterprise', label: 'Full company' },
]

/** "How much oversight?" -- maps to a preferred autonomy level. */
export type OversightPref = 'any' | 'supervised' | 'balanced' | 'autonomous'

export const OVERSIGHT_OPTIONS: readonly { value: OversightPref; label: string }[] = [
  { value: 'supervised', label: 'Supervised (I approve actions)' },
  { value: 'balanced', label: 'Balanced (semi-autonomous)' },
  { value: 'autonomous', label: 'Hands-off (full autonomy)' },
]

export const parseBuildGoal = makeEnumParser<BuildGoal>(GOAL_OPTIONS.map((o) => o.value))
export const parseOversight = makeEnumParser<OversightPref>(OVERSIGHT_OPTIONS.map((o) => o.value))

export interface RecommendationIntent {
  goal: BuildGoal
  oversight: OversightPref
}

/** Tags that signal a template fits a given build goal. */
const GOAL_TAGS: Record<Exclude<BuildGoal, 'any'>, readonly string[]> = {
  prototype: ['solo', 'minimal', 'rapid-prototyping', 'mvp', 'startup', 'small-team', 'lean'],
  product: ['product', 'design-system', 'agile', 'iterative', 'cross-functional'],
  services: ['agency', 'consulting', 'client-facing', 'client-management', 'advisory'],
  data: ['data', 'analytics', 'ml', 'pipelines', 'data-pipeline'],
  research: ['research', 'analysis', 'peer-review', 'knowledge-heavy'],
  security: ['security', 'red-team', 'threat-modelling', 'compliance', 'hardened'],
  enterprise: ['enterprise', 'full-hierarchy', 'all-departments', 'c-suite', 'comprehensive'],
}

/** Autonomy level each oversight preference favours. */
const OVERSIGHT_AUTONOMY: Record<Exclude<OversightPref, 'any'>, string> = {
  supervised: 'supervised',
  balanced: 'semi',
  autonomous: 'full',
}

/** One matched goal tag is worth this; an exact autonomy match this. */
const GOAL_TAG_WEIGHT = 3
const AUTONOMY_EXACT_WEIGHT = 4

export function hasIntent({ goal, oversight }: RecommendationIntent): boolean {
  return goal !== 'any' || oversight !== 'any'
}

/** Tag-overlap contribution to a template's match score for the goal. */
function goalScore(template: TemplateInfoResponse, goal: BuildGoal): number {
  if (goal === 'any') return 0
  const wanted = GOAL_TAGS[goal]
  const hits = template.tags.filter((tag) => wanted.includes(tag)).length
  return hits * GOAL_TAG_WEIGHT
}

/** Autonomy-match contribution to a template's match score. */
function oversightScore(template: TemplateInfoResponse, pref: OversightPref): number {
  if (pref === 'any') return 0
  return template.autonomy_level === OVERSIGHT_AUTONOMY[pref] ? AUTONOMY_EXACT_WEIGHT : 0
}

export interface RankedTemplate {
  template: TemplateInfoResponse
  /** Raw match score (with intent) or approachability score (no intent). */
  score: number
  /** 40-100 when ranked by intent; null when no intent was stated. */
  matchPercent: number | null
  /** Short human-readable reasons this template was surfaced. */
  reasons: readonly string[]
}

/** Short "why" label per goal, shown on recommended cards. */
const GOAL_REASON: Record<Exclude<BuildGoal, 'any'>, string> = {
  prototype: 'Built for fast prototyping',
  product: 'Product & engineering focus',
  services: 'Client-facing setup',
  data: 'Data & analytics focus',
  research: 'Research-oriented',
  security: 'Security & compliance focus',
  enterprise: 'Full-company structure',
}

/** Short "why" label per oversight preference. */
const OVERSIGHT_REASON: Record<Exclude<OversightPref, 'any'>, string> = {
  supervised: 'You approve actions',
  balanced: 'Semi-autonomous',
  autonomous: 'Runs hands-off',
}

/** Approachable tags that nudge a template up the no-intent default ranking. */
const APPROACHABLE_TAGS = new Set([TAG_SOLO, TAG_SMALL_TEAM, 'startup', 'mvp', 'minimal', 'lean'])

/** Score that maps to a 100% match (2 goal-tag hits + an autonomy match). */
const IDEAL_SCORE = GOAL_TAG_WEIGHT * 2 + AUTONOMY_EXACT_WEIGHT
/** Lowest match% shown, so a weak-but-valid match never looks broken. */
const MATCH_FLOOR_PERCENT = 40
const FULL_PERCENT = 100
/** Hero pick + this many alternatives. */
export const ALTERNATIVE_COUNT = 2

function clampPercent(score: number): number {
  const pct = Math.round((score / IDEAL_SCORE) * FULL_PERCENT)
  return Math.min(FULL_PERCENT, Math.max(MATCH_FLOOR_PERCENT, pct))
}

/**
 * No-intent default ranking. Supervision dominates so a fresh user's headline
 * pick is never the least-supervised template: a supervised org outranks an
 * autonomous one even when the autonomous one is smaller. Size and approachable
 * tags only break ties within the same oversight tier.
 */
function approachabilityScore(template: TemplateInfoResponse): number {
  const supervisedish =
    template.autonomy_level === 'semi' || template.autonomy_level === 'locked'
  const safetyScore = template.autonomy_level === 'supervised' ? 6 : supervisedish ? 3 : 0
  const sizeScore = template.agent_count <= 3 ? 2 : template.agent_count <= 8 ? 1 : 0
  const tagBonus = template.tags.some((t) => APPROACHABLE_TAGS.has(t)) ? 1 : 0
  return safetyScore + sizeScore + tagBonus
}

function buildReasons(template: TemplateInfoResponse, intent: RecommendationIntent): string[] {
  const reasons: string[] = []
  if (intent.goal !== 'any' && goalScore(template, intent.goal) > 0) {
    reasons.push(GOAL_REASON[intent.goal])
  }
  if (intent.oversight !== 'any' && oversightScore(template, intent.oversight) > 0) {
    reasons.push(OVERSIGHT_REASON[intent.oversight])
  }
  if (reasons.length === 0) reasons.push('Approachable starting point')
  return reasons
}

function rankByApproachability(templates: readonly TemplateInfoResponse[]): RankedTemplate[] {
  return templates
    .map((template) => ({ template, score: approachabilityScore(template) }))
    .sort((a, b) => b.score - a.score)
    .map(({ template, score }) => ({
      template,
      score,
      matchPercent: null,
      reasons: ['Approachable starting point'],
    }))
}

/**
 * Rank templates best-first. With a stated intent, only matching templates
 * are ranked by match score; with no intent (or no matches), templates are
 * ranked by an approachability heuristic so there is always a hero pick.
 */
export function rankTemplates(
  templates: readonly TemplateInfoResponse[],
  intent: RecommendationIntent,
): RankedTemplate[] {
  if (hasIntent(intent)) {
    const matched = templates
      .map((template) => ({
        template,
        score: goalScore(template, intent.goal) + oversightScore(template, intent.oversight),
      }))
      .filter((m) => m.score > 0)
      .sort((a, b) => b.score - a.score)
    if (matched.length > 0) {
      return matched.map(({ template, score }) => ({
        template,
        score,
        matchPercent: clampPercent(score),
        reasons: buildReasons(template, intent),
      }))
    }
  }
  return rankByApproachability(templates)
}
