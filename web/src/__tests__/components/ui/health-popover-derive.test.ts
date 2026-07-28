import { describe, expect, it } from 'vitest'
import { deriveHealthSubsystemStates } from '@/components/ui/health-popover/derive-subsystem-states'
import type { LoadState } from '@/components/ui/health-popover/health-popover.utils'
import type { MemoryHealth } from '@/api/types/system'

const FETCHED_AT = new Date('2099-01-01T10:00:00.000Z')

function okLoadState(
  memory: MemoryHealth,
  status: 'ok' | 'unavailable' = 'ok',
): LoadState {
  return {
    state: 'ok',
    data: {
      status,
      persistence: true,
      message_bus: true,
      providers: true,
      telemetry: 'disabled',
      memory,
      version: '0.6.4',
      uptime_seconds: 1,
    },
    fetchedAt: FETCHED_AT,
  }
}

describe('deriveHealthSubsystemStates api mapping', () => {
  it('reports the API operational whenever the fetch succeeded', () => {
    // A parsed response is proof the HTTP layer answered. Folding the
    // aggregate readiness verdict into this card reported a fully-serving
    // backend as unreachable whenever any one subsystem was degraded.
    const states = deriveHealthSubsystemStates(
      okLoadState(
        { state: 'degraded', backend: 'sqlvector', detail: 'unindexed' },
        'unavailable',
      ),
      true,
      false,
      false,
    )
    expect(states.apiState).toBe('ok')
    expect(states.overallState).toBe('degraded')
  })

  it('reports the API down only when the fetch itself failed', () => {
    const states = deriveHealthSubsystemStates(
      { state: 'error', message: 'network', fetchedAt: FETCHED_AT },
      true,
      false,
      false,
    )
    expect(states.apiState).toBe('down')
    expect(states.overallState).toBe('down')
  })

  it('degrades overall for a readiness failure no card covers', () => {
    // Providers have no card of their own, so an unavailable verdict with
    // every card healthy must still reach the hero.
    const states = deriveHealthSubsystemStates(
      okLoadState({ state: 'durable', backend: 'sqlvector', detail: null }, 'unavailable'),
      true,
      false,
      false,
    )
    expect(states.overallState).toBe('degraded')
  })
})

describe('deriveHealthSubsystemStates memory mapping', () => {
  it('maps a durable backend to an operational card showing the backend name', () => {
    const states = deriveHealthSubsystemStates(
      okLoadState({ state: 'durable', backend: 'sqlvector', detail: null }),
      true,
      false,
      false,
    )
    expect(states.memoryState).toBe('ok')
    expect(states.memoryDetail).toBe('sqlvector')
    expect(states.overallState).toBe('ok')
  })

  it('drags the overall state down when memory runs on the ephemeral backend', () => {
    const states = deriveHealthSubsystemStates(
      okLoadState({
        state: 'degraded',
        backend: 'inmemory',
        detail: 'Recall is lost on restart.',
      }),
      true,
      false,
      false,
    )
    expect(states.memoryState).toBe('degraded')
    expect(states.memoryDetail).toBe('Recall is lost on restart.')
    expect(states.overallState).toBe('degraded')
  })

  it('reports memory off as down so a missing embedder cannot look healthy', () => {
    const states = deriveHealthSubsystemStates(
      okLoadState({
        state: 'off',
        backend: 'none',
        detail: 'No embedding model resolved.',
      }),
      true,
      false,
      false,
    )
    expect(states.memoryState).toBe('down')
    expect(states.overallState).toBe('down')
  })

  it('holds memory unknown while no snapshot has been fetched', () => {
    const states = deriveHealthSubsystemStates({ state: 'idle' }, true, false, false)
    expect(states.memoryState).toBe('unknown')
    expect(states.memoryDetail).toBeUndefined()
  })

  it('holds memory unknown (not down) when the probe itself errored', () => {
    // A failed /health probe says nothing about memory specifically, so
    // it must read as unknown rather than falsely accusing the memory
    // subsystem of being down.
    const states = deriveHealthSubsystemStates(
      { state: 'error', message: 'network', fetchedAt: new Date(0) },
      true,
      false,
      false,
    )
    expect(states.memoryState).toBe('unknown')
    expect(states.memoryDetail).toBeUndefined()
  })

  it('reports an unreachable backend as down', () => {
    const states = deriveHealthSubsystemStates(
      okLoadState({
        state: 'unreachable',
        backend: 'sqlvector',
        detail: 'Reads and writes are failing.',
      }),
      true,
      false,
      false,
    )
    expect(states.memoryState).toBe('down')
    expect(states.overallState).toBe('down')
  })

  it('falls back to no detail when the backend name is blank', () => {
    const states = deriveHealthSubsystemStates(
      okLoadState({ state: 'durable', backend: '  ', detail: null }),
      true,
      false,
      false,
    )
    expect(states.memoryDetail).toBeUndefined()
  })
})
