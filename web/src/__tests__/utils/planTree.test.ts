import { describe, expect, it } from 'vitest'

import { makePlanItem } from '@/__tests__/helpers/factories'
import {
  buildPlanTree,
  childrenOf,
  dispatchDependencies,
  isContainer,
  placedByTree,
  workstreamOf,
} from '@/utils/planTree'

/**
 * Two workstreams; the first is split into two, and one of those into one
 * more, so every question below has a distinct right answer.
 */
function threeLevels() {
  return [
    makePlanItem('engine', { title: 'Engine' }),
    makePlanItem('ui', { title: 'UI' }),
    makePlanItem('board', { title: 'Board', parent_id: 'engine' }),
    makePlanItem('rotation', { title: 'Rotation', parent_id: 'engine' }),
    makePlanItem('grid', { title: 'Grid', parent_id: 'board' }),
  ]
}

describe('buildPlanTree', () => {
  it('reads the items nothing contains as the plan workstreams', () => {
    const tree = buildPlanTree(threeLevels())
    expect(tree.workstreams.map((item) => item.id)).toEqual(['engine', 'ui'])
  })

  it('keeps children in plan order under their container', () => {
    const tree = buildPlanTree(threeLevels())
    expect(childrenOf(tree, 'engine').map((item) => item.id)).toEqual([
      'board',
      'rotation',
    ])
  })

  it('derives containment from having children rather than a declared flag', () => {
    const tree = buildPlanTree(threeLevels())
    expect(isContainer(tree, 'engine')).toBe(true)
    expect(isContainer(tree, 'grid')).toBe(false)
  })

  it('reads an item whose parent the plan does not hold as a workstream', () => {
    // A hand-edited plan can name a parent that was deleted. Reading it as a
    // workstream keeps the whole item visible; dropping it hides real work.
    const tree = buildPlanTree([makePlanItem('orphan', { parent_id: 'gone' })])
    expect(tree.workstreams.map((item) => item.id)).toEqual(['orphan'])
  })

  it('treats a flat plan as workstreams all the way down', () => {
    const tree = buildPlanTree([makePlanItem('a'), makePlanItem('b')])
    expect(tree.workstreams).toHaveLength(2)
    expect(tree.byParent.size).toBe(0)
  })
})

describe('placedByTree', () => {
  it('puts each workstream immediately before the subtree it assembles', () => {
    const placed = placedByTree(buildPlanTree(threeLevels()))
    expect(placed.map((entry) => entry.item.id)).toEqual([
      'engine',
      'board',
      'grid',
      'rotation',
      'ui',
    ])
  })

  it('numbers an item by its position in the tree', () => {
    const placed = placedByTree(buildPlanTree(threeLevels()))
    expect(placed.map((entry) => entry.label)).toEqual(['1', '1.1', '1.1.1', '1.2', '2'])
  })

  it('reports depth and child count so a card renders without re-walking', () => {
    const placed = placedByTree(buildPlanTree(threeLevels()))
    const grid = placed.find((entry) => entry.item.id === 'grid')
    expect(grid?.depth).toBe(2)
    expect(grid?.childCount).toBe(0)
    expect(placed[0]?.childCount).toBe(2)
  })
})

describe('workstreamOf', () => {
  it('walks a nested item up to the track it belongs to', () => {
    const tree = buildPlanTree(threeLevels())
    expect(workstreamOf(tree, 'grid')?.id).toBe('engine')
  })

  it('answers with the item itself when it is a workstream', () => {
    const tree = buildPlanTree(threeLevels())
    expect(workstreamOf(tree, 'ui')?.id).toBe('ui')
  })
})

describe('dispatchDependencies', () => {
  it('has a container wait on the subtree it assembles', () => {
    const waits = dispatchDependencies(threeLevels())
    expect(waits.get('engine')).toEqual(['board', 'rotation'])
  })

  it('keeps whatever the plan declared as well', () => {
    const items = [
      makePlanItem('engine'),
      makePlanItem('board', { parent_id: 'engine', dependencies: ['spec'] }),
      makePlanItem('spec'),
    ]
    expect(dispatchDependencies(items).get('board')).toEqual(['spec'])
  })

  it('leaves the declared edges alone: containment is derived, never written', () => {
    // The single-owner claim, asserted rather than documented. `dependencies`
    // stays the only record of the order the plan DECLARED.
    const items = threeLevels()
    dispatchDependencies(items)
    expect(items.map((item) => item.dependencies)).toEqual([[], [], [], [], []])
  })
})
