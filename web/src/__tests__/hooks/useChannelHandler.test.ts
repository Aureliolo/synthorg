import { renderHook } from '@testing-library/react'

import { useChannelHandler } from '@/hooks/useChannelHandler'
import { useWebSocketStore } from '@/stores/websocket'

function resetStore(): void {
  useWebSocketStore.getState().teardown()
}

describe('useChannelHandler', () => {
  beforeEach(() => {
    resetStore()
    vi.clearAllMocks()
  })

  it('registers the handler on mount and detaches on unmount', () => {
    const onSpy = vi.spyOn(useWebSocketStore.getState(), 'onChannelEvent')
    const offSpy = vi.spyOn(useWebSocketStore.getState(), 'offChannelEvent')
    const handler = vi.fn()

    const { unmount } = renderHook(() => useChannelHandler('system', handler))

    expect(onSpy).toHaveBeenCalledWith('system', handler)
    expect(offSpy).not.toHaveBeenCalled()

    unmount()

    expect(offSpy).toHaveBeenCalledWith('system', handler)
  })

  it('skips registration entirely when the supplied signal is already aborted', () => {
    const onSpy = vi.spyOn(useWebSocketStore.getState(), 'onChannelEvent')
    const handler = vi.fn()
    const controller = new AbortController()
    controller.abort()

    renderHook(() => useChannelHandler('system', handler, controller.signal))

    expect(onSpy).not.toHaveBeenCalled()
  })

  it('detaches when the external abort signal fires before unmount', () => {
    const offSpy = vi.spyOn(useWebSocketStore.getState(), 'offChannelEvent')
    const handler = vi.fn()
    const controller = new AbortController()

    renderHook(() => useChannelHandler('system', handler, controller.signal))

    controller.abort()

    expect(offSpy).toHaveBeenCalledWith('system', handler)
  })

  it('does not double-detach when both abort and unmount fire', () => {
    const offSpy = vi.spyOn(useWebSocketStore.getState(), 'offChannelEvent')
    const handler = vi.fn()
    const controller = new AbortController()

    const { unmount } = renderHook(() =>
      useChannelHandler('system', handler, controller.signal),
    )

    controller.abort()
    unmount()

    expect(offSpy).toHaveBeenCalledTimes(1)
  })
})
