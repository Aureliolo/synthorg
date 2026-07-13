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

  it('flags a changed decision option set (by content, not reference)', () => {
    const options = [
      { id: 'a', title: 'A', summary: 'first', recommended: true },
      { id: 'b', title: 'B', summary: 'second', recommended: false },
    ]
    const previous = {
      version: 1,
      task_structure: 'sequential' as const,
      captured_at: '2026-07-01T10:00:00Z',
      items: [makePlanItem('d', { kind: 'decision', options })],
    }
    const current = {
      version: 2,
      // Same ids but a reworded summary on option A: a content change.
      items: [
        makePlanItem('d', {
          kind: 'decision',
          options: [{ ...options[0]!, summary: 'reworded' }, options[1]!],
        }),
      ],
    }
    const diff = derivePlanDiff(previous, current)
    expect(diff.modified[0]?.changedFields).toEqual(['options'])
  })

  it('treats an unchanged decision option set as unchanged', () => {
    const options = [
      { id: 'a', title: 'A', summary: 'first', recommended: true },
      { id: 'b', title: 'B', summary: 'second', recommended: false },
    ]
    const snapshot = {
      version: 1,
      task_structure: 'sequential' as const,
      captured_at: '2026-07-01T10:00:00Z',
      items: [makePlanItem('d', { kind: 'decision', options })],
    }
    // Fresh option objects (new references) with identical content.
    const current = {
      version: 2,
      items: [makePlanItem('d', { kind: 'decision', options: options.map((o) => ({ ...o })) })],
    }
    const diff = derivePlanDiff(snapshot, current)
    expect(diff.unchanged).toBe(1)
    expect(diff.modified).toHaveLength(0)
  })
})
