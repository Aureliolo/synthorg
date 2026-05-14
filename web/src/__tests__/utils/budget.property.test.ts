import { describe, expect, it } from 'vitest'
import fc from 'fast-check'
import type { BudgetAlertConfig, CostRecord } from '@/api/types/budget'
import {
  computeAgentSpending,
  computeCategoryBreakdown,
  computeCostBreakdown,
  getThresholdZone,
} from '@/utils/budget'
import { DEFAULT_CURRENCY } from '@/utils/currencies'

// ── Arbitraries ────────────────────────────────────────────

const callCategoryArb = fc.oneof(
  fc.constant('productive' as const),
  fc.constant('coordination' as const),
  fc.constant('system' as const),
  fc.constant('embedding' as const),
  fc.constant(null),
)

const finishReasonArb = fc.oneof(
  fc.constant('stop' as const),
  fc.constant('max_tokens' as const),
  fc.constant('tool_use' as const),
  fc.constant('content_filter' as const),
  fc.constant('error' as const),
  fc.constant(null),
)

const costRecordArb: fc.Arbitrary<CostRecord> = fc.record({
  agent_id: fc.stringMatching(/^agent-[0-9]{1,3}$/),
  task_id: fc.stringMatching(/^task-[0-9]{1,3}$/),
  project_id: fc.oneof(fc.stringMatching(/^proj-[0-9]{1,3}$/), fc.constant(null)),
  provider: fc.stringMatching(/^prov-[a-z]{1,3}$/),
  model: fc.constant('test-model-001'),
  input_tokens: fc.nat({ max: 10000 }),
  output_tokens: fc.nat({ max: 10000 }),
  cost: fc.double({ min: 0, max: 100, noNaN: true }),
  currency: fc.constant(DEFAULT_CURRENCY),
  timestamp: fc.constant('2026-03-20T10:00:00Z'),
  call_category: callCategoryArb,
  accuracy_effort_ratio: fc.oneof(fc.double({ min: 0, max: 1, noNaN: true }), fc.constant(null)),
  latency_ms: fc.oneof(fc.double({ min: 0, max: 10000, noNaN: true }), fc.constant(null)),
  cache_hit: fc.oneof(fc.boolean(), fc.constant(null)),
  retry_count: fc.oneof(fc.nat({ max: 5 }), fc.constant(null)),
  retry_reason: fc.oneof(fc.stringMatching(/^[a-z_]{3,20}$/), fc.constant(null)),
  finish_reason: finishReasonArb,
  success: fc.oneof(fc.boolean(), fc.constant(null)),
})

const alertsArb: fc.Arbitrary<BudgetAlertConfig> = fc
  .tuple(
    fc.double({ min: 1, max: 49, noNaN: true }),
    fc.double({ min: 1, max: 49, noNaN: true }),
    fc.double({ min: 1, max: 49, noNaN: true }),
  )
  .map(([a, b, c]) => {
    const sorted = [a, b, c].sort((x, y) => x - y)
    return {
      warn_at: sorted[0]!,
      critical_at: sorted[0]! + sorted[1]!,
      hard_stop_at: sorted[0]! + sorted[1]! + sorted[2]!,
    }
  })

// ── Properties ─────────────────────────────────────────────

describe('computeAgentSpending properties', () => {
  it('all budgetPercent values are non-negative', () => {
    fc.assert(
      fc.property(
        fc.array(costRecordArb, { minLength: 1, maxLength: 20 }),
        fc.double({ min: 0.01, max: 10000, noNaN: true }),
        (records, budget) => {
          const rows = computeAgentSpending(records, budget, new Map())
          for (const row of rows) {
            expect(row.budgetPercent).toBeGreaterThanOrEqual(0)
            expect(row.totalCost).toBeGreaterThanOrEqual(0)
            expect(row.costPerTask).toBeGreaterThanOrEqual(0)
          }
        },
      ),
    )
  })
})

describe('computeCostBreakdown properties', () => {
  it('all percent values are non-negative and sum to approximately 100', () => {
    fc.assert(
      fc.property(
        fc.array(costRecordArb, { minLength: 1, maxLength: 20 }),
        (records) => {
          const slices = computeCostBreakdown(records, 'agent', new Map(), new Map())
          let sum = 0
          for (const slice of slices) {
            expect(slice.percent).toBeGreaterThanOrEqual(0)
            sum += slice.percent
          }
          const totalCost = records.reduce((acc, r) => acc + r.cost, 0)
          if (slices.length > 0 && totalCost > 0) {
            expect(sum).toBeCloseTo(100, 0)
          }
        },
      ),
    )
  })
})

describe('computeCategoryBreakdown properties', () => {
  it('percentages sum to 100 for non-empty records or all zero for empty', () => {
    fc.assert(
      fc.property(
        fc.array(costRecordArb, { maxLength: 30 }),
        (records) => {
          const ratio = computeCategoryBreakdown(records)
          const sum =
            ratio.productive.percent +
            ratio.coordination.percent +
            ratio.system.percent +
            ratio.embedding.percent +
            ratio.uncategorized.percent
          const totalCost = records.reduce((acc, r) => acc + r.cost, 0)
          if (records.length === 0 || totalCost === 0) {
            expect(sum).toBe(0)
          } else {
            expect(sum).toBeCloseTo(100, 0)
          }
        },
      ),
    )
  })
})

describe('getThresholdZone properties', () => {
  it('always returns a valid zone for any usedPercent', () => {
    const validZones = new Set(['normal', 'amber', 'red', 'critical'])
    fc.assert(
      fc.property(
        fc.double({ min: 0, max: 200, noNaN: true }),
        alertsArb,
        (usedPercent, alerts) => {
          const zone = getThresholdZone(usedPercent, alerts)
          expect(validZones.has(zone)).toBe(true)
        },
      ),
    )
  })
})
