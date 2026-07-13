import { describe, expect, it } from 'vitest'

import { makePlanItem } from '@/__tests__/helpers/factories'
import { derivePlanDiff } from '@/utils/plan-diff'

describe('derivePlanDiff', () => {
  it('classifies added, removed, modified, and unchanged items', () => {
    const previous = {
      version: 1,
      task_structure: 'sequential' as const,
      captured_at: '2026-07-01T10:00:00Z',
      items: [
        makePlanItem('keep', { title: 'Keep', owner: 'A' }),
        makePlanItem('change', { title: 'Old title', owner: 'A' }),
        makePlanItem('drop', { title: 'Dropped' }),
      ],
    }
    const current = {
      version: 2,
      items: [
        makePlanItem('keep', { title: 'Keep', owner: 'A' }),
        makePlanItem('change', { title: 'New title', owner: 'B' }),
        makePlanItem('new', { title: 'Fresh' }),
      ],
    }
    const diff = derivePlanDiff(previous, current)
    expect(diff.fromVersion).toBe(1)
    expect(diff.toVersion).toBe(2)
    expect(diff.added.map((d) => d.id)).toEqual(['new'])
    expect(diff.removed.map((d) => d.id)).toEqual(['drop'])
    expect(diff.modified[0]?.id).toBe('change')
    expect(diff.modified[0]?.changedFields).toEqual(['title', 'owner'])
    expect(diff.unchanged).toBe(1)
  })
})
