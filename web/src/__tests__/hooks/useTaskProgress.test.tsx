import { act, renderHook } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { AguiEventType, type AguiStreamEvent } from '@/api/sse/agui-types'
import type { TaskProgressCallbacks } from '@/api/sse/task-progress-client'
import { useTaskProgress } from '@/hooks/useTaskProgress'

const close = vi.fn()
let captured: TaskProgressCallbacks | null = null

vi.mock('@/api/sse/task-progress-client', () => ({
  openTaskProgressStream: (_taskId: string, callbacks: TaskProgressCallbacks) => {
    captured = callbacks
    return { close }
  },
}))

function emit(type: AguiEventType, payload: Record<string, unknown> = {}): void {
  const event: AguiStreamEvent = {
    id: `evt-${Math.round(payload['turn'] as number) || 0}-${type}`,
    type,
    sessionId: 'task-1',
    agentId: 'agent-x',
    payload,
  }
  act(() => {
    captured?.onEvent(event)
  })
}

function triggerExhausted(): void {
  // Assert the hook actually wired onExhausted so the tests below cannot pass
  // vacuously if the wiring regresses.
  expect(captured?.onExhausted).toBeDefined()
  act(() => {
    captured?.onExhausted?.()
  })
}

describe('useTaskProgress', () => {
  beforeEach(() => {
    captured = null
    close.mockClear()
  })
  afterEach(() => {
    vi.clearAllMocks()
  })

  it('returns null when there is no task to watch', () => {
    const { result } = renderHook(() => useTaskProgress(null))
    expect(result.current).toBeNull()
  })

  it('accumulates tool-call stages and finishes', () => {
    const { result } = renderHook(() => useTaskProgress('task-1'))
    expect(result.current?.status).toBe('running')

    emit(AguiEventType.RunStarted)
    emit(AguiEventType.ToolCallStart, { turn: 1, tools: ['search'] })
    emit(AguiEventType.ToolCallStart, { turn: 2, tools: ['read_file'] })

    expect(result.current?.stages).toHaveLength(2)
    // The prior running stage is marked done when the next one appends.
    expect(result.current?.stages[0]?.status).toBe('done')
    expect(result.current?.stages[1]?.status).toBe('running')

    emit(AguiEventType.RunFinished)
    expect(result.current?.status).toBe('finished')
    expect(result.current?.stages.every((s) => s.status === 'done')).toBe(true)
  })

  it('marks the run errored and the last stage failed on run_error', () => {
    const { result } = renderHook(() => useTaskProgress('task-1'))
    emit(AguiEventType.ToolCallStart, { turn: 1, tools: ['search'] })
    emit(AguiEventType.RunError)
    expect(result.current?.status).toBe('error')
    expect(result.current?.stages[0]?.status).toBe('failed')
  })

  it('surfaces disconnected and stops the spinner when the stream is exhausted mid-run', () => {
    const { result } = renderHook(() => useTaskProgress('task-1'))
    emit(AguiEventType.ToolCallStart, { turn: 1, tools: ['search'] })
    expect(result.current?.status).toBe('running')

    triggerExhausted()
    expect(result.current?.status).toBe('disconnected')
    // A running stage is settled so the panel does not keep a spinner going.
    expect(result.current?.stages.every((s) => s.status !== 'running')).toBe(true)
  })

  it('does not overwrite a terminal finished status when the stream later exhausts', () => {
    const { result } = renderHook(() => useTaskProgress('task-1'))
    emit(AguiEventType.ToolCallStart, { turn: 1, tools: ['search'] })
    emit(AguiEventType.RunFinished)
    expect(result.current?.status).toBe('finished')

    // The backend closing its side after a terminal frame must not be reported
    // as a lost connection.
    triggerExhausted()
    expect(result.current?.status).toBe('finished')
  })

  it('does not overwrite a terminal error status when the stream later exhausts', () => {
    const { result } = renderHook(() => useTaskProgress('task-1'))
    emit(AguiEventType.ToolCallStart, { turn: 1, tools: ['search'] })
    emit(AguiEventType.RunError)
    expect(result.current?.status).toBe('error')

    triggerExhausted()
    expect(result.current?.status).toBe('error')
  })

  it('tears the stream down on unmount', () => {
    const { unmount } = renderHook(() => useTaskProgress('task-1'))
    unmount()
    expect(close).toHaveBeenCalledTimes(1)
  })
})
