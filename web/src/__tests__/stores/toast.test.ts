import * as fc from 'fast-check'
import { useToastStore } from '@/stores/toast'

describe('toast store', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    useToastStore.getState().dismissAll()
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('initial state has empty toasts', () => {
    expect(useToastStore.getState().toasts).toHaveLength(0)
  })

  it('add() appends a toast', () => {
    useToastStore.getState().add({ variant: 'success', title: 'Saved' })
    expect(useToastStore.getState().toasts).toHaveLength(1)
  })

  it('add() returns a string ID', () => {
    const id = useToastStore.getState().add({ variant: 'info', title: 'Hello' })
    expect(typeof id).toBe('string')
    expect(id.length).toBeGreaterThan(0)
  })

  it('add() assigns unique IDs', () => {
    const id1 = useToastStore.getState().add({ variant: 'info', title: 'A' })
    const id2 = useToastStore.getState().add({ variant: 'info', title: 'B' })
    expect(id1).not.toBe(id2)
  })

  it('dismiss() removes specific toast', () => {
    const id1 = useToastStore.getState().add({ variant: 'info', title: 'A' })
    useToastStore.getState().add({ variant: 'info', title: 'B' })
    expect(useToastStore.getState().toasts).toHaveLength(2)

    useToastStore.getState().dismiss(id1)
    expect(useToastStore.getState().toasts).toHaveLength(1)
    expect(useToastStore.getState().toasts[0]?.title).toBe('B')
  })

  it('dismissAll() clears all toasts', () => {
    useToastStore.getState().add({ variant: 'info', title: 'A' })
    useToastStore.getState().add({ variant: 'info', title: 'B' })
    useToastStore.getState().add({ variant: 'info', title: 'C' })
    expect(useToastStore.getState().toasts).toHaveLength(3)

    useToastStore.getState().dismissAll()
    expect(useToastStore.getState().toasts).toHaveLength(0)
  })

  it('cancelAllPending() clears pending auto-dismiss timers but preserves toasts', () => {
    useToastStore.getState().add({ variant: 'info', title: 'Keep me' })
    useToastStore.getState().add({ variant: 'success', title: 'Keep me too' })
    expect(useToastStore.getState().toasts).toHaveLength(2)

    useToastStore.getState().cancelAllPending()

    // Toasts remain in state -- only timers are cleared.
    expect(useToastStore.getState().toasts).toHaveLength(2)

    // Advancing past the longest auto-dismiss duration must not remove anything.
    vi.advanceTimersByTime(60_000)
    expect(useToastStore.getState().toasts).toHaveLength(2)
  })

  it('auto-dismisses after default duration', () => {
    useToastStore.getState().add({ variant: 'info', title: 'Auto' })
    expect(useToastStore.getState().toasts).toHaveLength(1)

    vi.advanceTimersByTime(5000)
    expect(useToastStore.getState().toasts).toHaveLength(0)
  })

  it('keeps warning/error toasts persistent (no default auto-dismiss)', () => {
    useToastStore.getState().add({ variant: 'warning', title: 'Stays' })
    useToastStore.getState().add({ variant: 'error', title: 'Stays too' })
    expect(useToastStore.getState().toasts).toHaveLength(2)

    // Null-duration variants schedule no timer; advancing time must not remove them.
    vi.advanceTimersByTime(60_000)
    expect(useToastStore.getState().toasts).toHaveLength(2)
  })

  it('auto-dismisses after custom duration', () => {
    useToastStore.getState().add({ variant: 'info', title: 'Quick', duration: 2000 })
    expect(useToastStore.getState().toasts).toHaveLength(1)

    vi.advanceTimersByTime(1999)
    expect(useToastStore.getState().toasts).toHaveLength(1)

    vi.advanceTimersByTime(1)
    expect(useToastStore.getState().toasts).toHaveLength(0)
  })

  it('manual dismiss clears auto-dismiss timer', () => {
    const id = useToastStore.getState().add({ variant: 'info', title: 'Manual' })
    useToastStore.getState().dismiss(id)
    expect(useToastStore.getState().toasts).toHaveLength(0)

    // Advancing time should not cause errors
    vi.advanceTimersByTime(10000)
    expect(useToastStore.getState().toasts).toHaveLength(0)
  })

  it('preserves toast properties', () => {
    useToastStore.getState().add({
      variant: 'error',
      title: 'Failed',
      description: 'Something went wrong',
      duration: 10000,
      dismissible: false,
    })

    const toast = useToastStore.getState().toasts[0]
    expect(toast).toMatchObject({
      variant: 'error',
      title: 'Failed',
      description: 'Something went wrong',
      duration: 10000,
      dismissible: false,
    })
  })

  describe('property: add/dismiss invariants', () => {
    it('N adds followed by dismissAll leaves 0 toasts', () => {
      fc.assert(
        fc.property(
          fc.integer({ min: 0, max: 20 }),
          (n) => {
            useToastStore.getState().dismissAll()
            for (let i = 0; i < n; i++) {
              useToastStore.getState().add({ variant: 'info', title: `Toast ${i}` })
            }
            // Cancel auto-dismiss to test count
            useToastStore.getState().dismissAll()
            return useToastStore.getState().toasts.length === 0
          },
        ),
      )
    })
  })
})
