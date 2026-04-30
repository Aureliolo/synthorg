import { renderHook } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { useDetailNavigation } from '@/hooks/use-detail-navigation'

interface Item {
  id: string
}

// Module-level wrapper (per @eslint-react/component-hook-factories).
// Tests pass the initial route via ``renderHook``'s ``initialProps``
// option below.
function RouterWrapper({ children }: { children: React.ReactNode }) {
  return <MemoryRouter initialEntries={[currentInitialPath]}>{children}</MemoryRouter>
}

// Mutable module-level path that ``RouterWrapper`` reads on mount.
// Each test sets it before calling ``renderHook``; tests in this
// file run sequentially so the shared variable is safe.
let currentInitialPath = '/'

const items: readonly Item[] = [
  { id: 'a' },
  { id: 'b' },
  { id: 'c' },
]

describe('useDetailNavigation', () => {
  it('exposes prev+next when the cursor is in the middle of the list', () => {
    currentInitialPath = '/items/b'
    const { result } = renderHook(
      () =>
        useDetailNavigation({
          items,
          currentId: 'b',
          routeFor: (item) => `/items/${item.id}`,
        }),
      { wrapper: RouterWrapper },
    )
    expect(result.current.canPrev).toBe(true)
    expect(result.current.canNext).toBe(true)
    expect(result.current.position).toEqual({ current: 2, total: 3 })
    expect(result.current.goPrev).not.toBeNull()
    expect(result.current.goNext).not.toBeNull()
  })

  it('disables prev at the head of the list', () => {
    currentInitialPath = '/items/a'
    const { result } = renderHook(
      () =>
        useDetailNavigation({
          items,
          currentId: 'a',
          routeFor: (item) => `/items/${item.id}`,
        }),
      { wrapper: RouterWrapper },
    )
    expect(result.current.canPrev).toBe(false)
    expect(result.current.canNext).toBe(true)
    expect(result.current.goPrev).toBeNull()
    expect(result.current.position).toEqual({ current: 1, total: 3 })
  })

  it('disables next at the tail of the list', () => {
    currentInitialPath = '/items/c'
    const { result } = renderHook(
      () =>
        useDetailNavigation({
          items,
          currentId: 'c',
          routeFor: (item) => `/items/${item.id}`,
        }),
      { wrapper: RouterWrapper },
    )
    expect(result.current.canPrev).toBe(true)
    expect(result.current.canNext).toBe(false)
    expect(result.current.goNext).toBeNull()
    expect(result.current.position).toEqual({ current: 3, total: 3 })
  })

  it('returns null position when the current id is not in items (deep link)', () => {
    currentInitialPath = '/items/missing'
    const { result } = renderHook(
      () =>
        useDetailNavigation({
          items,
          currentId: 'missing',
          routeFor: (item) => `/items/${item.id}`,
        }),
      { wrapper: RouterWrapper },
    )
    expect(result.current.canPrev).toBe(false)
    expect(result.current.canNext).toBe(false)
    expect(result.current.position).toBeNull()
  })

  it('returns null position when currentId is missing entirely', () => {
    currentInitialPath = '/items'
    const { result } = renderHook(
      () =>
        useDetailNavigation({
          items,
          currentId: undefined,
          routeFor: (item) => `/items/${item.id}`,
        }),
      { wrapper: RouterWrapper },
    )
    expect(result.current.position).toBeNull()
  })

  it('uses a custom getId when items have a non-id key field', () => {
    currentInitialPath = '/items/alice'
    // ``T extends { id: string }`` on the hook means we keep ``id`` for
    // the constraint; ``getId`` then projects a different field as the
    // logical key. ``id`` here is the URL slug; ``name`` is the lookup
    // value tests assert against. Mirrors a real surface where the
    // route uses a slug but the backend list keys by ``name``.
    type Custom = { id: string; name: string }
    const customItems: readonly Custom[] = [
      { id: 'alice-slug', name: 'alice' },
      { id: 'bob-slug', name: 'bob' },
    ]
    const { result } = renderHook(
      () =>
        useDetailNavigation<Custom>({
          items: customItems,
          currentId: 'alice',
          getId: (item) => item.name,
          routeFor: (item) => `/items/${item.id}`,
        }),
      { wrapper: RouterWrapper },
    )
    expect(result.current.position).toEqual({ current: 1, total: 2 })
  })
})
