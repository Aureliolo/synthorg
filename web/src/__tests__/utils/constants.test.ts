import { describe, it, expect } from 'vitest'
import { TASK_STATUS_ORDER, TERMINAL_STATUSES, VALID_TRANSITIONS, NAV_ITEMS } from '@/utils/constants'

describe('TASK_STATUS_ORDER', () => {
  it('contains all 9 statuses', () => {
    expect(TASK_STATUS_ORDER).toHaveLength(9)
  })

  it('starts with created', () => {
    expect(TASK_STATUS_ORDER[0]).toBe('created')
  })
})

describe('TERMINAL_STATUSES', () => {
  it('contains exactly 2 terminal statuses', () => {
    expect(TERMINAL_STATUSES.size).toBe(2)
  })

  it('contains completed and cancelled', () => {
    expect(TERMINAL_STATUSES.has('completed')).toBe(true)
    expect(TERMINAL_STATUSES.has('cancelled')).toBe(true)
  })

  it('does not contain in_progress', () => {
    expect(TERMINAL_STATUSES.has('in_progress')).toBe(false)
  })
})

describe('VALID_TRANSITIONS', () => {
  it('created can transition to assigned', () => {
    expect(VALID_TRANSITIONS['created']).toContain('assigned')
  })

  it('completed has no transitions', () => {
    expect(VALID_TRANSITIONS['completed']).toEqual([])
  })

  it('in_progress can go to in_review', () => {
    expect(VALID_TRANSITIONS['in_progress']).toContain('in_review')
  })
})

describe('NAV_ITEMS', () => {
  it('has dashboard as first item', () => {
    expect(NAV_ITEMS[0].label).toBe('Dashboard')
    expect(NAV_ITEMS[0].to).toBe('/')
  })

  it('has settings as last item', () => {
    expect(NAV_ITEMS[NAV_ITEMS.length - 1].label).toBe('Settings')
  })
})
