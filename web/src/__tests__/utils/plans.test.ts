import { describe, expect, it } from 'vitest'

import { makePlanItem } from '@/__tests__/helpers/factories'
import {
  answeredQuestions,
  computeCriticalPath,
  computeWaves,
  criticalPathFor,
  dependencyTitles,
  derivePlanStats,
  isHighComplexity,
  isHighStakes,
  itemFlags,
  derivePlanCoverage,
  derivePlanStaffing,
  itemNeedsAttention,
  planDetailPath,
  planItemToPayload,
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
    expect(itemFlags(item, { onCriticalPath: false, roster: undefined })).toEqual([])
    expect(itemNeedsAttention(item, { onCriticalPath: false, roster: undefined })).toBe(
      false,
    )
  })

  it('collects every risk and gap on a bad item', () => {
    const item = makePlanItem('a', {
      owner: null,
      stakes: 'critical',
      estimated_complexity: 'epic',
      acceptance_criteria: [],
    })
    const keys = itemFlags(item, { onCriticalPath: true, roster: undefined }).map(
      (f) => f.key,
    )
    expect(keys).toEqual(['stakes', 'complexity', 'unowned', 'no-criteria', 'critical-path'])
  })

  it('flags an owner no agent holds, rather than reading it as assigned', () => {
    // The dogfood shape: "Backend Engineer" for an org staffing "Backend
    // Developer". The item has nobody behind it, but it is not unowned, so
    // the unassigned check alone reported the plan as fully assigned.
    const item = makePlanItem('a', {
      owner: 'Backend Engineer',
      stakes: 'normal',
      estimated_complexity: 'medium',
      acceptance_criteria: ['builds green'],
    })
    const roster = new Set(['Backend Developer', 'QA Engineer'])

    const flags = itemFlags(item, { onCriticalPath: false, roster })

    expect(flags.map((f) => f.key)).toEqual(['unroutable-owner'])
    expect(flags[0]?.detail).toContain('Backend Engineer')
  })

  it('accepts an owner the roster holds', () => {
    const item = makePlanItem('a', {
      owner: 'Backend Developer',
      stakes: 'normal',
      estimated_complexity: 'medium',
      acceptance_criteria: ['builds green'],
    })
    const roster = new Set(['Backend Developer'])

    expect(itemFlags(item, { onCriticalPath: false, roster })).toEqual([])
  })

  it('judges no owner while the roster is unknown', () => {
    // The agents list has not arrived yet; flagging every item on that would
    // be noise the reviewer cannot act on. `undefined` is the sentinel for
    // that, not an empty set.
    const item = makePlanItem('a', {
      owner: 'Backend Engineer',
      stakes: 'normal',
      estimated_complexity: 'medium',
      acceptance_criteria: ['builds green'],
    })

    expect(itemFlags(item, { onCriticalPath: false, roster: undefined })).toEqual([])
  })

  it('flags every named owner when the org staffs nobody', () => {
    // A loaded empty roster is an answer: nothing can be dispatched, which
    // is exactly what a reviewer needs told before approving the plan.
    const item = makePlanItem('a', {
      owner: 'Backend Engineer',
      stakes: 'normal',
      estimated_complexity: 'medium',
      acceptance_criteria: ['builds green'],
    })

    expect(
      itemFlags(item, { onCriticalPath: false, roster: new Set() }).map((f) => f.key),
    ).toEqual(['unroutable-owner'])
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

  it('counts an owner no agent holds as unassigned', () => {
    // "8 of 9 items owned by a role with no agent behind it" read as fully
    // assigned, because only a null owner counted.
    const items = [
      makePlanItem('a', { owner: 'Backend Engineer', acceptance_criteria: ['done'] }),
      makePlanItem('b', { owner: 'Backend Developer', acceptance_criteria: ['done'] }),
    ]
    const roster = new Set(['Backend Developer'])

    const stats = derivePlanStats(items, new Set(), roster)

    expect(stats.unowned).toBe(1)
    expect(stats.flaggedItems).toBe(1)
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

describe('derivePlanCoverage', () => {
  it('maps each criterion to the items that advance it and flags gaps', () => {
    const items = [
      makePlanItem('a', { title: 'Board', satisfies: ['Playable board'] }),
      makePlanItem('b', { title: 'Score', satisfies: ['playable board'] }),
    ]
    const coverage = derivePlanCoverage(['Playable board', 'Score tracking'], items)
    expect(coverage.total).toBe(2)
    expect(coverage.covered).toBe(1)
    expect(coverage.uncovered).toEqual(['Score tracking'])
    // Case-insensitive match unions both items under the first criterion.
    expect(coverage.entries[0]?.coveredBy).toEqual(['Board', 'Score'])
    expect(coverage.entries[1]?.coveredBy).toEqual([])
  })

  it('returns empty coverage when the objective declared no criteria', () => {
    const coverage = derivePlanCoverage([], [makePlanItem('a')])
    expect(coverage.total).toBe(0)
    expect(coverage.uncovered).toEqual([])
  })
})

describe('answeredQuestions', () => {
  it('settles a question whose distinctive words the criteria all carry', () => {
    const items = [
      makePlanItem('a', {
        title: 'Storage layer',
        acceptance_criteria: ['The persistence backend is SQLite'],
      }),
    ]
    expect(answeredQuestions(['Which persistence backend?'], items)).toEqual([
      { question: 'Which persistence backend?', settledBy: 'Storage layer' },
    ])
  })

  it('leaves a question the plan does not address open', () => {
    const items = [
      makePlanItem('a', { title: 'Board', acceptance_criteria: ['Grid renders'] }),
    ]
    expect(answeredQuestions(['Is offline play in scope?'], items)).toEqual([
      { question: 'Is offline play in scope?', settledBy: null },
    ])
  })

  it('does not settle a question on filler words alone', () => {
    // Matching on "which"/"the"/"is" would settle every question against any
    // criterion at all, which is a worse failure than asking twice.
    const items = [
      makePlanItem('a', { title: 'Board', acceptance_criteria: ['Which is the one'] }),
    ]
    expect(answeredQuestions(['Which persistence backend?'], items)[0]).toEqual({
      question: 'Which persistence backend?',
      settledBy: null,
    })
  })

  it('leaves a question made only of filler words open', () => {
    // With no distinctive word to match on, "every word is carried" is
    // vacuously true, so the question would be settled by the first item on
    // the plan regardless of what it says.
    const items = [
      makePlanItem('a', { title: 'Board', acceptance_criteria: ['Grid renders'] }),
    ]
    expect(answeredQuestions(['Is it?'], items)).toEqual([
      { question: 'Is it?', settledBy: null },
    ])
  })

  it('ignores case and punctuation', () => {
    const items = [
      makePlanItem('a', {
        title: 'Storage',
        acceptance_criteria: ['persistence: BACKEND chosen'],
      }),
    ]
    expect(answeredQuestions(['Which persistence backend?'], items)[0]?.settledBy).toBe(
      'Storage',
    )
  })

  it('leaves everything open when the plan has no items yet', () => {
    expect(answeredQuestions(['Which backend?'], [])).toEqual([
      { question: 'Which backend?', settledBy: null },
    ])
  })
})

describe('derivePlanStaffing', () => {
  it('summarises owner load, high-stakes, unassigned, and bottlenecks', () => {
    const items = [
      makePlanItem('a', { owner: 'Backend', stakes: 'critical' }),
      makePlanItem('b', { owner: 'Backend' }),
      makePlanItem('c', { owner: 'Backend' }),
      makePlanItem('d', { owner: 'Design' }),
      makePlanItem('e', { owner: null }),
    ]
    const staffing = derivePlanStaffing(items)
    expect(staffing.totalOwners).toBe(2)
    expect(staffing.unassigned).toBe(1)
    // Busiest owner first; Backend owns 3 of 5 (>= ceil(5/2)) so it is a bottleneck.
    expect(staffing.roles[0]?.owner).toBe('Backend')
    expect(staffing.roles[0]?.itemCount).toBe(3)
    expect(staffing.roles[0]?.highStakesCount).toBe(1)
    expect(staffing.roles[0]?.overloaded).toBe(true)
    expect(staffing.roles[1]?.overloaded).toBe(false)
  })

  it('never flags a bottleneck when a single owner holds everything', () => {
    const staffing = derivePlanStaffing([
      makePlanItem('a', { owner: 'Solo' }),
      makePlanItem('b', { owner: 'Solo' }),
      makePlanItem('c', { owner: 'Solo' }),
    ])
    expect(staffing.roles[0]?.overloaded).toBe(false)
  })
})

describe('planItemToPayload', () => {
  it('round-trips a decision item, preserving its options and chosen pick', () => {
    const item = makePlanItem('decide-1', {
      kind: 'decision',
      chosen_option_id: 'opt-a',
      options: [
        { id: 'opt-a', title: 'A', summary: 'Tradeoffs A.', recommended: true },
        { id: 'opt-b', title: 'B', summary: 'Tradeoffs B.', recommended: false },
      ],
    })
    const payload = planItemToPayload(item)
    expect(payload.id).toBe('decide-1')
    expect(payload.kind).toBe('decision')
    expect(payload.chosen_option_id).toBe('opt-a')
    expect(payload.options).toHaveLength(2)
  })
})
