import { renderHook } from '@testing-library/react'
import type { AgentRuntimeStatus } from '@/utils/agent-status'
import { useStatusTransition } from '@/hooks/useStatusTransition'

describe('useStatusTransition', () => {
  const statuses: AgentRuntimeStatus[] = ['active', 'idle', 'error', 'offline']

  it.each(statuses)('returns a display color for status "%s"', (status) => {
    const { result } = renderHook(() => useStatusTransition(status))
    expect(typeof result.current.displayColor).toBe('string')
    expect(result.current.displayColor.length).toBeGreaterThan(0)
  })

  it.each(statuses)('returns motionProps with animate target for status "%s"', (status) => {
    const { result } = renderHook(() => useStatusTransition(status))
    expect(result.current.motionProps).toHaveProperty('animate')
    expect(result.current.motionProps.animate).toHaveProperty('backgroundColor')
  })

  it.each(statuses)('returns motionProps with transition for status "%s"', (status) => {
    const { result } = renderHook(() => useStatusTransition(status))
    expect(result.current.motionProps).toHaveProperty('transition')
  })

  it('maps active to success color', () => {
    const { result } = renderHook(() => useStatusTransition('active'))
    expect(result.current.displayColor).toBe('success')
  })

  it('maps error to danger color', () => {
    const { result } = renderHook(() => useStatusTransition('error'))
    expect(result.current.displayColor).toBe('danger')
  })

  it('maps idle to accent color', () => {
    const { result } = renderHook(() => useStatusTransition('idle'))
    expect(result.current.displayColor).toBe('accent')
  })

  it('maps offline to secondary color', () => {
    const { result } = renderHook(() => useStatusTransition('offline'))
    expect(result.current.displayColor).toBe('text-secondary')
  })
})
