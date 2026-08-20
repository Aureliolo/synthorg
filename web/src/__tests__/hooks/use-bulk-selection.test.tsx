import { act, renderHook, waitFor } from '@testing-library/react'
import { useBulkSelection } from '@/hooks/use-bulk-selection'

function noDelete(): Promise<void> {
  return Promise.resolve()
}

describe('useBulkSelection', () => {
  it('counts only the rows still on screen', () => {
    // Filtering or paging takes a row away, and a count that kept it would
    // tell the operator they are about to delete something they cannot see.
    const { result, rerender } = renderHook(
      ({ ids }: { ids: string[] }) => useBulkSelection(ids, noDelete),
      { initialProps: { ids: ['a', 'b', 'c'] } },
    )

    act(() => {
      result.current.toggle('a')
      result.current.toggle('c')
    })
    expect(result.current.selectedCount).toBe(2)

    rerender({ ids: ['a', 'b'] })

    expect(result.current.selectedCount).toBe(1)
    expect([...result.current.visibleSelected]).toEqual(['a'])
  })

  it('brings a selection back when its row returns', () => {
    // The tick is remembered against the row, not against the page it was on:
    // returning to a page has to restore what was picked there.
    const { result, rerender } = renderHook(
      ({ ids }: { ids: string[] }) => useBulkSelection(ids, noDelete),
      { initialProps: { ids: ['a', 'b'] } },
    )

    act(() => {
      result.current.toggle('b')
    })
    rerender({ ids: ['a'] })
    expect(result.current.selectedCount).toBe(0)

    rerender({ ids: ['a', 'b'] })

    expect(result.current.selectedCount).toBe(1)
  })

  it('deletes what is selected, then clears and closes', async () => {
    const onDelete = vi.fn(() => Promise.resolve())
    const { result } = renderHook(() => useBulkSelection(['a', 'b'], onDelete))

    act(() => {
      result.current.toggle('a')
      result.current.openConfirm()
    })
    expect(result.current.confirmOpen).toBe(true)

    await act(async () => {
      await result.current.runDelete()
    })

    expect(onDelete).toHaveBeenCalledWith(['a'])
    await waitFor(() => {
      expect(result.current.confirmOpen).toBe(false)
    })
    expect(result.current.selectedCount).toBe(0)
    expect(result.current.deleting).toBe(false)
  })

  it('lets go of the dialog even when the delete throws', async () => {
    // The store owns error UX and is not supposed to throw, but the dialog
    // refuses to close while a delete is in flight, so a throw that left the
    // flag set would strand an operator inside a modal over a destructive
    // action with no way out of it.
    const onDelete = vi.fn(() => Promise.reject(new Error('boom')))
    const { result } = renderHook(() => useBulkSelection(['a'], onDelete))

    act(() => {
      result.current.toggle('a')
      result.current.openConfirm()
    })

    await act(async () => {
      await expect(result.current.runDelete()).rejects.toThrow('boom')
    })

    await waitFor(() => {
      expect(result.current.deleting).toBe(false)
    })
    expect(result.current.confirmOpen).toBe(false)
  })
})
