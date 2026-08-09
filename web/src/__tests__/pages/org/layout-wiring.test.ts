import { afterEach, describe, expect, it, vi } from 'vitest'
import * as dagre from '@dagrejs/dagre'
import { type DeptSpec, layoutOf, orgConfig } from '../../helpers/org-layout'

const ORG: readonly DeptSpec[] = [
  { name: 'executive', members: ['zoe'] },
  {
    name: 'engineering',
    members: ['alice', 'bob', 'carol'],
    teams: [{ name: 'core', members: ['bob', 'carol'] }],
  },
]

afterEach(() => {
  vi.restoreAllMocks()
})

describe('dagre invocation contract', () => {
  it('clears dagre\'s cross-call state on every unit it lays out', () => {
    // dagre keeps the previous graph in module-level state and only clears it
    // on an explicit `false`. Nothing observable in a position assertion would
    // catch the flag being dropped, because the ordering constraints already
    // own real-node order; only long-edge routing would drift. Pin the wiring
    // directly so the defence cannot be removed silently.
    const layout = vi.spyOn(dagre, 'layout')
    layoutOf(orgConfig(ORG))

    expect(layout).toHaveBeenCalled()
    for (const call of layout.mock.calls) {
      expect(call[1]).toMatchObject({ useDynamic: false })
    }
  })

  it('lays each unit out in its own graph rather than as a dagre cluster', () => {
    // One pass per team, one per populated department, one for the top-level
    // frame. Compound clusters are deliberately unused: dagre 3.1.0 reads only
    // `rankdir` off a cluster node and drops a nested cluster's members.
    const layout = vi.spyOn(dagre, 'layout')
    layoutOf(orgConfig(ORG))

    // 1 team + 2 departments + 1 top-level frame.
    expect(layout).toHaveBeenCalledTimes(4)
    for (const call of layout.mock.calls) {
      const graph = call[0] as { isCompound: () => boolean }
      expect(graph.isCompound()).toBe(false)
    }
  })
})
