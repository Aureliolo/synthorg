import { act, renderHook } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { useBulkSelection } from '@/hooks/useBulkSelection'

describe('useBulkSelection', () => {
  it('starts with an empty selection', () => {
    const { result } = renderHook(() => useBulkSelection())
    expect(result.current.count).toBe(0)
    expect(result.current.selectedIds.size).toBe(0)
  })

  it('toggle adds an id to the selection', () => {
    const { result } = renderHook(() => useBulkSelection())
    act(() => result.current.toggle('a'))
    expect(result.current.selectedIds.has('a')).toBe(true)
    expect(result.current.count).toBe(1)
  })

  it('toggle removes an id when already selected', () => {
    const { result } = renderHook(() => useBulkSelection())
    act(() => result.current.toggle('a'))
    act(() => result.current.toggle('a'))
    expect(result.current.selectedIds.has('a')).toBe(false)
    expect(result.current.count).toBe(0)
  })

  it('toggleAll selects every visible id when none are selected', () => {
    const { result } = renderHook(() => useBulkSelection())
    act(() => result.current.toggleAll(['a', 'b', 'c']))
    expect(result.current.selectedIds.size).toBe(3)
    expect(result.current.selectedIds.has('a')).toBe(true)
    expect(result.current.selectedIds.has('b')).toBe(true)
    expect(result.current.selectedIds.has('c')).toBe(true)
  })

  it('toggleAll clears every visible id when all are selected', () => {
    const { result } = renderHook(() => useBulkSelection())
    act(() => result.current.toggleAll(['a', 'b', 'c']))
    act(() => result.current.toggleAll(['a', 'b', 'c']))
    expect(result.current.selectedIds.size).toBe(0)
  })

  it('toggleAll preserves hidden selections when toggling visible-only', () => {
    const { result } = renderHook(() => useBulkSelection())
    act(() => result.current.toggle('hidden'))
    act(() => result.current.toggleAll(['visible-1', 'visible-2']))
    expect(result.current.selectedIds.size).toBe(3)
    expect(result.current.selectedIds.has('hidden')).toBe(true)
  })

  it('toggleAll on a partial selection completes it', () => {
    const { result } = renderHook(() => useBulkSelection())
    act(() => result.current.toggle('a'))
    act(() => result.current.toggleAll(['a', 'b', 'c']))
    expect(result.current.selectedIds.size).toBe(3)
  })

  it('clear resets the selection regardless of filter', () => {
    const { result } = renderHook(() => useBulkSelection())
    act(() => result.current.toggleAll(['a', 'b', 'c']))
    act(() => result.current.toggle('hidden'))
    act(() => result.current.clear())
    expect(result.current.selectedIds.size).toBe(0)
  })

  it('isAllSelected returns false for an empty visible set', () => {
    const { result } = renderHook(() => useBulkSelection())
    act(() => result.current.toggle('a'))
    expect(result.current.isAllSelected([])).toBe(false)
  })

  it('isAllSelected returns true only when every visible id is selected', () => {
    const { result } = renderHook(() => useBulkSelection())
    act(() => result.current.toggleAll(['a', 'b']))
    expect(result.current.isAllSelected(['a', 'b'])).toBe(true)
    expect(result.current.isAllSelected(['a', 'b', 'c'])).toBe(false)
  })

  it('isPartiallySelected is true when some-but-not-all visible ids are selected', () => {
    const { result } = renderHook(() => useBulkSelection())
    act(() => result.current.toggle('a'))
    expect(result.current.isPartiallySelected(['a', 'b', 'c'])).toBe(true)
    expect(result.current.isPartiallySelected(['a'])).toBe(false)
    expect(result.current.isPartiallySelected(['b', 'c'])).toBe(false)
  })

  it('toggle is involution (calling it twice returns to the original state)', () => {
    const { result } = renderHook(() => useBulkSelection())
    const before = result.current.selectedIds.size
    act(() => result.current.toggle('x'))
    act(() => result.current.toggle('x'))
    expect(result.current.selectedIds.size).toBe(before)
  })
})
