import { describe, it, expect } from 'vitest'
import {
  TASK_STATUS_ORDER,
  VALID_TRANSITIONS,
  TERMINAL_STATUSES,
} from '@/utils/constants'

// Note: These are exhaustive structural invariant checks over constant data
// structures, not randomized property tests. No fast-check usage is needed
// because the input space is finite and fully enumerable.
describe('task status constants (structural invariants)', () => {
  it('every status in TASK_STATUS_ORDER has an entry in VALID_TRANSITIONS', () => {
    for (const status of TASK_STATUS_ORDER) {
      expect(VALID_TRANSITIONS).toHaveProperty(status)
    }
  })

  it('every key in VALID_TRANSITIONS appears in TASK_STATUS_ORDER', () => {
    for (const status of Object.keys(VALID_TRANSITIONS)) {
      expect(TASK_STATUS_ORDER).toContain(status)
    }
  })

  it('terminal statuses have empty transition arrays', () => {
    for (const status of TERMINAL_STATUSES) {
      const transitions = VALID_TRANSITIONS[status]
      expect(transitions).toBeDefined()
      expect(transitions).toHaveLength(0)
    }
  })

  it('no status has a self-transition', () => {
    for (const [status, targets] of Object.entries(VALID_TRANSITIONS)) {
      expect(targets).not.toContain(status)
    }
  })

  it('all transition targets are valid statuses in TASK_STATUS_ORDER', () => {
    for (const [_status, targets] of Object.entries(VALID_TRANSITIONS)) {
      for (const target of targets) {
        expect(TASK_STATUS_ORDER).toContain(target)
      }
    }
  })

  it('TASK_STATUS_ORDER has no duplicate entries', () => {
    const unique = new Set(TASK_STATUS_ORDER)
    expect(unique.size).toBe(TASK_STATUS_ORDER.length)
  })

  it('non-terminal statuses have at least one transition', () => {
    for (const status of TASK_STATUS_ORDER) {
      if (!TERMINAL_STATUSES.has(status)) {
        const transitions = VALID_TRANSITIONS[status]
        expect(transitions.length).toBeGreaterThan(0)
      }
    }
  })

  it('TERMINAL_STATUSES is a subset of TASK_STATUS_ORDER', () => {
    for (const status of TERMINAL_STATUSES) {
      expect(TASK_STATUS_ORDER).toContain(status)
    }
  })
})
