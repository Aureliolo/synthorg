import { useNotificationsStore, cancelPendingPersist } from '@/stores/notifications'
import type { WsEvent } from '@/api/types/websocket'

/**
 * Focused unit tests for the module-level `cancelPendingPersist` helper.
 *
 * `notifications.ts` debounces localStorage persistence with a 300ms
 * `setTimeout`. Tests that enqueue notifications but finish before the
 * debounce elapses would otherwise leave the pending timer alive past
 * the test boundary, which the active-handle gate would surface as a
 * forgotten Timeout. `cancelPendingPersist` drops the pending handle
 * without flushing; the global `afterEach` in `test-setup.tsx` calls
 * it unconditionally.
 */
describe('cancelPendingPersist', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    useNotificationsStore.getState().clearAll()
    localStorage.clear()
    // Invoking clearAll() can itself schedule a persist timer; cancel it
    // so every test starts from a deterministic no-timer baseline.
    cancelPendingPersist()
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('suppresses the pending persist after an enqueue', () => {
    useNotificationsStore.getState().enqueue({
      category: 'approvals.pending',
      title: 'Pending work item',
    })

    // Baseline: without cancellation the debounce would fire after 300ms
    // and write to localStorage.
    cancelPendingPersist()

    vi.advanceTimersByTime(5_000)

    expect(localStorage.getItem('so_notifications')).toBeNull()
  })

  it('is a no-op when no timer is pending', () => {
    // Called at a clean slate -- must not throw.
    expect(() => cancelPendingPersist()).not.toThrow()
    cancelPendingPersist()
    cancelPendingPersist()
  })

  it('does not prevent a follow-up persist from being scheduled', () => {
    useNotificationsStore.getState().enqueue({
      category: 'approvals.pending',
      title: 'First',
    })
    cancelPendingPersist()

    // A subsequent enqueue must still schedule its own persist.
    useNotificationsStore.getState().enqueue({
      category: 'approvals.pending',
      title: 'Second',
    })

    vi.advanceTimersByTime(400)

    const persisted = localStorage.getItem('so_notifications')
    expect(persisted).not.toBeNull()
    const parsed = JSON.parse(persisted!) as Array<{ title: string }>
    const titles = parsed.map((i) => i.title)
    expect(titles).toContain('Second')
  })
})

describe('handleWsEvent payload sanitization', () => {
  beforeEach(() => {
    vi.useRealTimers()
    useNotificationsStore.getState().clearAll()
    localStorage.clear()
    cancelPendingPersist()
  })

  function makeEvent(eventType: string, payload: Record<string, unknown>): WsEvent {
    return {
      event_type: eventType,
      payload,
      timestamp: new Date().toISOString(),
    } as WsEvent
  }

  it('strips C0 control characters from description fields', () => {
    useNotificationsStore.getState().handleWsEvent(
      makeEvent('approval.submitted', {
        approval_id: 'a-1',
        title: 'Normal\u0000title\u001Bwith\u0007controls',
      }),
    )

    const items = useNotificationsStore.getState().items
    expect(items).toHaveLength(1)
    expect(items[0]!.description).toBe('Normaltitlewithcontrols')
  })

  it('strips bidi-override characters from description fields', () => {
    useNotificationsStore.getState().handleWsEvent(
      makeEvent('system.error', {
        message: '\u202EHidden reversal payload\u202C hidden',
      }),
    )

    const items = useNotificationsStore.getState().items
    expect(items).toHaveLength(1)
    expect(items[0]!.description).toBe('Hidden reversal payload hidden')
  })

  it('clamps description length to 128 characters', () => {
    const huge = 'x'.repeat(500)
    useNotificationsStore.getState().handleWsEvent(
      makeEvent('system.error', { message: huge }),
    )

    const items = useNotificationsStore.getState().items
    expect(items).toHaveLength(1)
    expect(items[0]!.description).toHaveLength(128)
  })

  it('returns undefined for non-string payload fields (type guard)', () => {
    useNotificationsStore.getState().handleWsEvent(
      makeEvent('system.error', {
        message: { nested: 'object' },
      }),
    )

    const items = useNotificationsStore.getState().items
    expect(items).toHaveLength(1)
    expect(items[0]!.description).toBeUndefined()
  })

  it('treats whitespace-only strings as absent to avoid blank descriptions', () => {
    useNotificationsStore.getState().handleWsEvent(
      makeEvent('system.error', { message: '   \n\t  ' }),
    )

    const items = useNotificationsStore.getState().items
    expect(items).toHaveLength(1)
    expect(items[0]!.description).toBeUndefined()
  })

  it('preserves common whitespace (TAB, LF, CR) in multi-line messages', () => {
    useNotificationsStore.getState().handleWsEvent(
      makeEvent('system.error', {
        message: 'line1\nline2\r\nindented:\tvalue',
      }),
    )

    const items = useNotificationsStore.getState().items
    expect(items).toHaveLength(1)
    // Whitespace in the interior survives; leading/trailing trim still applies
    // (none in this input).
    expect(items[0]!.description).toBe('line1\nline2\r\nindented:\tvalue')
  })

  it('truncates at code-point boundaries so surrogate pairs are not split', () => {
    // Each "🌟" is a 2-code-unit surrogate pair. 200 stars = 400 UTF-16 units
    // (200 code points), well over the 128-code-point cap. Naive
    // `.slice(0, 128)` would return 64 whole stars followed by a LONE HIGH
    // SURROGATE from star #65 -- that lone surrogate serializes to U+FFFD
    // and leaks a bad character into storage. The code-point-aware slice
    // returns exactly 128 whole stars with no orphans.
    const stars = '\u{1F31F}'.repeat(200)
    useNotificationsStore.getState().handleWsEvent(
      makeEvent('system.error', { message: stars }),
    )

    const items = useNotificationsStore.getState().items
    expect(items).toHaveLength(1)
    const description = items[0]!.description
    expect(description).toBeDefined()
    // Must contain exactly 128 whole emojis -- no more, no fewer, no lone surrogates.
    expect([...description!]).toHaveLength(128)
    expect([...description!].every((c) => c === '\u{1F31F}')).toBe(true)
  })
})
