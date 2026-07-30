import { describe, expect, it } from 'vitest'
import { deriveHealthSubsystemStates } from '@/components/ui/health-popover/derive-subsystem-states'
import type { LoadState } from '@/stores/health'
import type { MemoryHealth } from '@/api/types/system'

const FETCHED_AT = new Date('2099-01-01T10:00:00.000Z')

function okLoadState(
  memory: MemoryHealth,
  status: 'ok' | 'unavailable' = 'ok',
  providers: boolean | null = true,
): LoadState {
  return {
    state: 'ok',
    data: {
      status,
      persistence: true,
      message_bus: true,
      providers,
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
    // Degraded memory abstains from readiness, so it cannot be why the
    // backend refused: the refusal is unexplained, which is down.
    expect(states.overallState).toBe('down')
  })

  it('reports the timed-out probe fan-out as down rather than unknown', () => {
    // A readiness probe that exceeds its budget answers 503 with every
    // component null, so all three boolean cards read `unknown`. Ranked
    // above the refusal, that verdict hid the outage behind the mildest
    // word the panel has.
    const states = deriveHealthSubsystemStates(
      {
        state: 'ok',
        data: {
          status: 'unavailable',
          persistence: null,
          message_bus: null,
          providers: null,
          telemetry: 'disabled',
          memory: { state: 'durable', backend: 'sqlvector', detail: null },
          version: '0.6.4',
          uptime_seconds: 1,
        },
        fetchedAt: FETCHED_AT,
      },
      true,
      false,
      false,
    )
    expect(states.persistenceState).toBe('unknown')
    expect(states.busState).toBe('unknown')
    expect(states.overallState).toBe('down')
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

  it('surfaces unreachable providers on their own card, not as a softened hero', () => {
    // Providers gate readiness for real, so an unreachable provider used to
    // land nowhere: every card stayed green and the hero said "degraded,
    // check the cards below" with nothing below to check.
    const states = deriveHealthSubsystemStates(
      okLoadState(
        { state: 'durable', backend: 'sqlvector', detail: null },
        'unavailable',
        false,
      ),
      true,
      false,
      false,
    )
    expect(states.providersState).toBe('down')
    expect(states.apiState).toBe('ok')
    expect(states.overallState).toBe('down')
  })

  it('reports a readiness failure no card covers as down, not degraded', () => {
    // Every input the backend weighs has a card, so reaching this means it
    // refused traffic for an unexplained reason. It already decided it is
    // not serving; the panel must not soften that to "degraded".
    const states = deriveHealthSubsystemStates(
      okLoadState({ state: 'durable', backend: 'sqlvector', detail: null }, 'unavailable'),
      true,
      false,
      false,
    )
    expect(states.overallState).toBe('down')
  })

  it('holds providers unknown when the deployment configures none', () => {
    const states = deriveHealthSubsystemStates(
      okLoadState({ state: 'durable', backend: 'sqlvector', detail: null }, 'ok', null),
      true,
      false,
      false,
    )
    expect(states.providersState).toBe('unknown')
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

  it('reports memory off as degraded, distinctly from unreachable', () => {
    // Off means never wired; unreachable means wired and not answering. Reading
    // both as down made the hero announce an outage over an embedding model
    // nobody had chosen, and claimed the whole system was down while every
    // other component served normally. Degraded still surfaces it, and the
    // card's own detail says what to do.
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
    expect(states.memoryState).toBe('degraded')
    expect(states.overallState).toBe('degraded')
    expect(states.memoryDetail).toBe('No embedding model resolved.')
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
