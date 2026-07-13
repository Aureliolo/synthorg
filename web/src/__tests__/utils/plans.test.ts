import { describe, expect, it } from 'vitest'

import { makePlanItem } from '@/__tests__/helpers/factories'
import {
  computeCriticalPath,
  computeWaves,
  criticalPathFor,
  dependencyTitles,
  derivePlanStats,
  isHighComplexity,
  isHighStakes,
  itemFlags,
  itemNeedsAttention,
  planDetailPath,
  planItemTitleMap,
} from '@/utils/plans'

describe('plan severity predicates', () => {
  it('treats complex and epic as high complexity', () => {
    expect(isHighComplexity(makePlanItem('a', { estimated_complexity: 'complex' }))).toBe(true)
    expect(isHighComplexity(makePlanItem('a', { estimated_complexity: 'epic' }))).toBe(true)
    expect(isHighComplexity(makePlanItem('a', { estimated_complexity: 'medium' }))).toBe(false)
  })

  it('treats high and critical as high stakes', () => {
    expect(isHighStakes(makePlanItem('a', { stakes: 'high' }))).toBe(true)
    expect(isHighStakes(makePlanItem('a', { stakes: 'critical' }))).toBe(true)
    expect(isHighStakes(makePlanItem('a', { stakes: 'normal' }))).toBe(false)
  })
})

describe('itemFlags', () => {
  it('flags a clean, owned, scoped item with nothing', () => {
    const item = makePlanItem('a', {
      owner: 'agent-eng',
      stakes: 'normal',
      estimated_complexity: 'medium',
      acceptance_criteria: ['builds green'],
    })
    expect(itemFlags(item, { onCriticalPath: false })).toEqual([])
    expect(itemNeedsAttention(item, { onCriticalPath: false })).toBe(false)
  })

  it('collects every risk and gap on a bad item', () => {
    const item = makePlanItem('a', {
      owner: null,
      stakes: 'critical',
      estimated_complexity: 'epic',
      acceptance_criteria: [],
    })
    const keys = itemFlags(item, { onCriticalPath: true }).map((f) => f.key)
    expect(keys).toEqual(['stakes', 'complexity', 'unowned', 'no-criteria', 'critical-path'])
  })
})

describe('computeCriticalPath', () => {
  it('returns the longest predecessor chain', () => {
    // a -> b -> c is the chain; d hangs off a shorter branch.
    const items = [
      makePlanItem('a', { dependencies: [] }),
      makePlanItem('b', { dependencies: ['a'] }),
      makePlanItem('c', { dependencies: ['b'] }),
      makePlanItem('d', { dependencies: ['a'] }),
    ]
    const path = computeCriticalPath(items)
    expect([...path].sort()).toEqual(['a', 'b', 'c'])
    expect(path.has('d')).toBe(false)
  })

  it('returns an empty set when no chain spans two items', () => {
    const items = [makePlanItem('a'), makePlanItem('b')]
    expect(computeCriticalPath(items).size).toBe(0)
  })

  it('does not loop on a defensive cycle', () => {
    const items = [
      makePlanItem('a', { dependencies: ['b'] }),
      makePlanItem('b', { dependencies: ['a'] }),
    ]
    expect(() => computeCriticalPath(items)).not.toThrow()
  })
})

describe('criticalPathFor', () => {
  const branching = [
    makePlanItem('a', { dependencies: [] }),
    makePlanItem('b', { dependencies: ['a'] }),
    makePlanItem('c', { dependencies: ['b'] }),
    makePlanItem('d', { dependencies: ['a'] }),
  ]

  it('suppresses the critical path on a sequential plan (no signal)', () => {
    expect(criticalPathFor(branching, 'sequential').size).toBe(0)
  })

  it('surfaces the path on a branching non-sequential plan', () => {
    expect([...criticalPathFor(branching, 'mixed')].sort()).toEqual(['a', 'b', 'c'])
  })

  it('suppresses the path when the chain spans every item', () => {
    // A fully linear graph classified parallel: the "path" is everything, so
    // there is nothing to single out.
    const linear = [
      makePlanItem('a', { dependencies: [] }),
      makePlanItem('b', { dependencies: ['a'] }),
      makePlanItem('c', { dependencies: ['b'] }),
    ]
    expect(criticalPathFor(linear, 'parallel').size).toBe(0)
  })
})

describe('computeWaves', () => {
  it('groups items into dependency-depth waves, parallel within a wave', () => {
    // a gates b and d (which run in parallel); c follows b.
    const items = [
      makePlanItem('a', { dependencies: [] }),
      makePlanItem('b', { dependencies: ['a'] }),
      makePlanItem('c', { dependencies: ['b'] }),
      makePlanItem('d', { dependencies: ['a'] }),
    ]
    const waves = computeWaves(items)
    expect(waves.map((w) => w.index)).toEqual([0, 1, 2])
    expect(waves[0]?.items.map((i) => i.id)).toEqual(['a'])
    expect(
      waves[1]?.items
        .map((i) => i.id)
        .slice()
        .sort(),
    ).toEqual(['b', 'd'])
    expect(waves[2]?.items.map((i) => i.id)).toEqual(['c'])
  })

  it('puts independent items in a single wave', () => {
    const items = [makePlanItem('a'), makePlanItem('b'), makePlanItem('c')]
    const waves = computeWaves(items)
    expect(waves).toHaveLength(1)
    expect(waves[0]?.items).toHaveLength(3)
  })
})

describe('derivePlanStats', () => {
  it('aggregates every review signal', () => {
    const items = [
      makePlanItem('a', {
        owner: null,
        stakes: 'critical',
        estimated_complexity: 'epic',
        acceptance_criteria: [],
        dependencies: [],
      }),
      makePlanItem('b', {
        owner: 'agent-eng',
        stakes: 'normal',
        estimated_complexity: 'medium',
        acceptance_criteria: ['done'],
        dependencies: ['a'],
      }),
    ]
    const path = computeCriticalPath(items)
    const stats = derivePlanStats(items, path)
    expect(stats.totalItems).toBe(2)
    expect(stats.highStakes).toBe(1)
    expect(stats.highComplexity).toBe(1)
    expect(stats.unowned).toBe(1)
    expect(stats.missingCriteria).toBe(1)
    expect(stats.dependencyEdges).toBe(1)
    expect(stats.criticalPathLength).toBe(2)
    // Both items are flagged: 'a' for its risks/gaps, 'b' for sitting on the
    // critical path (a -> b).
    expect(stats.flaggedItems).toBe(2)
  })
})

describe('dependency resolution', () => {
  it('resolves dependency ids to titles, keeping unknown ids verbatim', () => {
    const items = [
      makePlanItem('a', { title: 'Scaffold' }),
      makePlanItem('b', { title: 'Movement', dependencies: ['a', 'ghost'] }),
    ]
    const titleById = planItemTitleMap(items)
    const b = items[1]
    if (b === undefined) throw new Error('unreachable')
    expect(dependencyTitles(b, titleById)).toEqual(['Scaffold', 'ghost'])
  })
})

describe('planDetailPath', () => {
  it('encodes the plan id into the detail route', () => {
    expect(planDetailPath('plan 1')).toBe('/plans/plan%201')
  })
})
