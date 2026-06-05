import { act, fireEvent, render, renderHook, waitFor, screen } from '@testing-library/react'
import fc from 'fast-check'
import type { ReactNode } from 'react'
import { createMemoryRouter, RouterProvider, useNavigate, useLocation } from 'react-router'
import { useUnsavedChangesGuard } from '@/hooks/use-unsaved-changes-guard'

const DRAFT_KEY = 'test-draft-guard'

function hookWrapper({ children }: { children: ReactNode }) {
  const router = createMemoryRouter(
    [
      { path: '/a', element: <>{children}</> },
      { path: '/b', element: <div>Page B</div> },
    ],
    { initialEntries: ['/a'] },
  )
  return <RouterProvider router={router} />
}

function ConfirmHarnessPage() {
  const guard = useUnsavedChangesGuard({ when: true })
  const navigate = useNavigate()
  return (
    <div>
      <button type="button" onClick={() => navigate('/b')} aria-label="go-b">go</button>
      <span data-testid="confirm">{String(guard.confirmOpen)}</span>
      <button type="button" aria-label="cancel" onClick={guard.cancel}>cancel</button>
      <button type="button" aria-label="proceed" onClick={guard.proceed}>proceed</button>
    </div>
  )
}

function ProceedDraftHarnessPage() {
  const guard = useUnsavedChangesGuard({ when: true, draftKey: DRAFT_KEY })
  const navigate = useNavigate()
  return (
    <div>
      <button type="button" onClick={() => navigate('/b')} aria-label="go-b">go</button>
      <button type="button" aria-label="proceed" onClick={guard.proceed}>proceed</button>
      {/* Expose blocker state so the test can wait for the blocker to reach
          'blocked' before clicking proceed, avoiding a race where proceed()
          fires before useBlocker finishes its state transition. */}
      <span data-testid="confirm">{String(guard.confirmOpen)}</span>
      <span data-testid="has-draft">{String(guard.hasDraft)}</span>
    </div>
  )
}

function CurrentPath() {
  const location = useLocation()
  return <span data-testid="path">{location.pathname}</span>
}

afterEach(() => {
  window.localStorage.removeItem(DRAFT_KEY)
})

describe('useUnsavedChangesGuard', () => {
  it('confirmOpen is false when not dirty', () => {
    const { result } = renderHook(
      () => useUnsavedChangesGuard({ when: false }),
      { wrapper: hookWrapper },
    )
    expect(result.current.confirmOpen).toBe(false)
  })

  it('hasDraft reads localStorage on mount', () => {
    window.localStorage.setItem(DRAFT_KEY, JSON.stringify({ foo: 'bar' }))
    const { result } = renderHook(
      () => useUnsavedChangesGuard({ when: false, draftKey: DRAFT_KEY }),
      { wrapper: hookWrapper },
    )
    expect(result.current.hasDraft).toBe(true)
    expect(result.current.restoreDraft()).toEqual({ foo: 'bar' })
  })

  it('hasDraft false when localStorage is empty', () => {
    const { result } = renderHook(
      () => useUnsavedChangesGuard({ when: false, draftKey: DRAFT_KEY }),
      { wrapper: hookWrapper },
    )
    expect(result.current.hasDraft).toBe(false)
    expect(result.current.restoreDraft()).toBeNull()
  })

  it('discardDraft removes localStorage entry and clears hasDraft', () => {
    window.localStorage.setItem(DRAFT_KEY, JSON.stringify({ a: 1 }))
    const { result } = renderHook(
      () => useUnsavedChangesGuard({ when: false, draftKey: DRAFT_KEY }),
      { wrapper: hookWrapper },
    )
    act(() => {
      result.current.discardDraft()
    })
    expect(window.localStorage.getItem(DRAFT_KEY)).toBeNull()
    expect(result.current.hasDraft).toBe(false)
  })

  it('debounced draft write persists after draftDebounceMs', () => {
    vi.useFakeTimers()
    try {
      const payload = { v: 2 }
      renderHook(
        () =>
          useUnsavedChangesGuard({
            when: true,
            draftKey: DRAFT_KEY,
            draftData: () => payload,
            draftDebounceMs: 100,
          }),
        { wrapper: hookWrapper },
      )
      act(() => {
        vi.advanceTimersByTime(150)
      })
      expect(JSON.parse(window.localStorage.getItem(DRAFT_KEY) ?? '{}')).toEqual({ v: 2 })
    } finally {
      vi.useRealTimers()
    }
  })

  it('beforeunload event is prevented when dirty', () => {
    renderHook(() => useUnsavedChangesGuard({ when: true }), { wrapper: hookWrapper })
    const event = new Event('beforeunload', { cancelable: true })
    act(() => {
      fireEvent(window, event)
    })
    expect(event.defaultPrevented).toBe(true)
  })

  it('blocks navigation and exposes confirmOpen', async () => {
    const router = createMemoryRouter(
      [
        { path: '/a', element: <ConfirmHarnessPage /> },
        { path: '/b', element: <div>Page B</div> },
      ],
      { initialEntries: ['/a'] },
    )

    const { getByLabelText, getByTestId } = render(<RouterProvider router={router} />)

    expect(getByTestId('confirm').textContent).toBe('false')

    act(() => {
      fireEvent.click(getByLabelText('go-b'))
    })

    await waitFor(() => {
      expect(getByTestId('confirm').textContent).toBe('true')
    })

    act(() => {
      fireEvent.click(getByLabelText('cancel'))
    })

    await waitFor(() => {
      expect(getByTestId('confirm').textContent).toBe('false')
    })
  })

  it('proceed() clears draft and completes the navigation to the target route', async () => {
    window.localStorage.setItem(DRAFT_KEY, JSON.stringify({ existing: true }))

    const router = createMemoryRouter(
      [
        { path: '/a', element: <><ProceedDraftHarnessPage /><CurrentPath /></> },
        { path: '/b', element: <><div>Page B</div><CurrentPath /></> },
      ],
      { initialEntries: ['/a'] },
    )

    const { getByLabelText, getByTestId } = render(<RouterProvider router={router} />)
    expect(getByTestId('path').textContent).toBe('/a')

    act(() => {
      fireEvent.click(getByLabelText('go-b'))
    })
    // Wait for the blocker to transition to 'blocked' before firing proceed();
    // otherwise proceed()'s `blocker.state === 'blocked'` guard races with
    // React's state commit and the click becomes a no-op on fast machines.
    await waitFor(() => {
      expect(getByTestId('confirm').textContent).toBe('true')
    })
    act(() => {
      fireEvent.click(getByLabelText('proceed'))
    })
    await waitFor(() => {
      expect(window.localStorage.getItem(DRAFT_KEY)).toBeNull()
      expect(screen.getByTestId('path').textContent).toBe('/b')
    })
  })

  it('draftTrigger change reschedules the debounced write', () => {
    vi.useFakeTimers()
    try {
      let payload = { v: 1 }
      const { rerender } = renderHook(
        ({ trigger }: { trigger: number }) =>
          useUnsavedChangesGuard({
            when: true,
            draftKey: DRAFT_KEY,
            draftData: () => payload,
            draftTrigger: trigger,
            draftDebounceMs: 100,
          }),
        { wrapper: hookWrapper, initialProps: { trigger: 0 } },
      )
      // First flush captures the initial payload.
      act(() => { vi.advanceTimersByTime(150) })
      expect(JSON.parse(window.localStorage.getItem(DRAFT_KEY) ?? '{}')).toEqual({ v: 1 })
      // Payload changes; caller bumps the trigger.
      payload = { v: 2 }
      rerender({ trigger: 1 })
      act(() => { vi.advanceTimersByTime(150) })
      expect(JSON.parse(window.localStorage.getItem(DRAFT_KEY) ?? '{}')).toEqual({ v: 2 })
    } finally {
      vi.useRealTimers()
    }
  })

  it('hasDraft updates when draftKey changes', () => {
    const KEY_A = 'test-draft-key-a'
    const KEY_B = 'test-draft-key-b'
    window.localStorage.setItem(KEY_A, JSON.stringify({ x: 1 }))
    try {
      const { result, rerender } = renderHook(
        ({ key }: { key: string }) =>
          useUnsavedChangesGuard({ when: false, draftKey: key }),
        { wrapper: hookWrapper, initialProps: { key: KEY_A } },
      )
      expect(result.current.hasDraft).toBe(true)
      rerender({ key: KEY_B })
      expect(result.current.hasDraft).toBe(false)
    } finally {
      window.localStorage.removeItem(KEY_A)
      window.localStorage.removeItem(KEY_B)
    }
  })

  it('property: restoreDraft round-trips any JSON-safe payload written via draftTrigger', () => {
    vi.useFakeTimers()
    try {
      fc.assert(
        fc.property(
          fc.record({
            id: fc.integer(),
            name: fc.string({ minLength: 0, maxLength: 40 }),
            active: fc.boolean(),
          }),
          (payload) => {
            window.localStorage.removeItem(DRAFT_KEY)
            const { result, unmount } = renderHook(
              () =>
                useUnsavedChangesGuard<typeof payload>({
                  when: true,
                  draftKey: DRAFT_KEY,
                  draftData: () => payload,
                  draftTrigger: JSON.stringify(payload),
                  draftDebounceMs: 50,
                }),
              { wrapper: hookWrapper },
            )
            act(() => { vi.advanceTimersByTime(100) })
            expect(result.current.restoreDraft()).toEqual(payload)
            unmount()
          },
        ),
        { numRuns: 25 },
      )
    } finally {
      vi.useRealTimers()
      window.localStorage.removeItem(DRAFT_KEY)
    }
  })
})
