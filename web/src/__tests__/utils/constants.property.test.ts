import fc from 'fast-check'
import { TASK_STATUS_ORDER, VALID_TRANSITIONS, TERMINAL_STATUSES } from '@/utils/tasks'

describe('constants property tests', () => {
  const allStatuses = TASK_STATUS_ORDER
  const statusArb = fc.constantFrom(...allStatuses)

  it('every status in TASK_STATUS_ORDER exists in VALID_TRANSITIONS', () => {
    fc.assert(
      fc.property(statusArb, (status) => {
        expect(status in VALID_TRANSITIONS).toBe(true)
      }),
    )
  })

  it('transition targets are always valid statuses', () => {
    fc.assert(
      fc.property(statusArb, (status) => {
        const targets = VALID_TRANSITIONS[status]
        for (const target of targets) {
          expect(allStatuses).toContain(target)
        }
      }),
    )
  })

  it('terminal statuses have no outgoing transitions', () => {
    fc.assert(
      fc.property(
        fc.constantFrom(...TERMINAL_STATUSES),
        (status) => {
          expect(VALID_TRANSITIONS[status]).toHaveLength(0)
        },
      ),
    )
  })
})
