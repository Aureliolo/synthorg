import { describe, expect, it } from 'vitest'
import { deriveHealthSubsystemStates } from '@/components/ui/health-popover/derive-subsystem-states'
import type { LoadState } from '@/stores/health'
import type {
  BackupHealth,
  MemoryHealth,
  ProviderReachability,
} from '@/api/types/system'
import type { SubsystemPhase, SubsystemReport } from '@/api/types/subsystems'

const FETCHED_AT = new Date('2099-01-01T10:00:00.000Z')

function okLoadState(
  memory: MemoryHealth,
  status: 'ok' | 'unavailable' = 'ok',
  providers: ProviderReachability | null = 'ok',
  backup: BackupHealth = { state: 'wired', detail: null },
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
      backup,
      cost_recording: { state: 'ok', dropped_records: 0, detail: null },
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
    expect(states.withWebSocketState).toBe('down')
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
          backup: { state: 'unattempted', detail: null },
          cost_recording: { state: 'ok', dropped_records: 0, detail: null },
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
    expect(states.withWebSocketState).toBe('down')
  })

  it('reports the API down only when the fetch itself failed', () => {
    const states = deriveHealthSubsystemStates(
      { state: 'error', message: 'network', fetchedAt: FETCHED_AT },
      true,
      false,
      false,
    )
    expect(states.apiState).toBe('down')
    expect(states.withWebSocketState).toBe('down')
  })

  it('surfaces unreachable providers on their own card, not as a softened hero', () => {
    // Without a card of its own an unreachable provider lands nowhere: every
    // card stays green and the hero says "degraded, check the cards below"
    // with nothing below to check.
    const states = deriveHealthSubsystemStates(
      okLoadState(
        { state: 'durable', backend: 'sqlvector', detail: null },
        'unavailable',
        'down',
      ),
      true,
      false,
      false,
    )
    expect(states.providersState).toBe('down')
    expect(states.apiState).toBe('ok')
    expect(states.withWebSocketState).toBe('down')
  })

  it('shows a degraded provider as degraded rather than folding it into ok', () => {
    // A boolean has to pick a side for "degraded"; folded into reachable, the
    // row an operator checks during a partial outage is the row that hides it.
    const states = deriveHealthSubsystemStates(
      okLoadState(
        { state: 'durable', backend: 'sqlvector', detail: null },
        'ok',
        'degraded',
      ),
      true,
      false,
      false,
    )
    expect(states.providersState).toBe('degraded')
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
    expect(states.withWebSocketState).toBe('down')
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

  it('shows an unreadable verdict as unknown, never as an outage', () => {
    // The backend reports `unknown` when its own read of the verdict failed.
    // Rendering that as `down` sends the operator to check endpoints and
    // credentials that may be serving perfectly.
    const states = deriveHealthSubsystemStates(
      okLoadState(
        { state: 'durable', backend: 'sqlvector', detail: null },
        'ok',
        'unknown',
      ),
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
    expect(states.withWebSocketState).toBe('ok')
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
    expect(states.withWebSocketState).toBe('degraded')
  })

  it('reports memory off as degraded, distinctly from unreachable', () => {
    // Off means never wired; unreachable means wired and not answering. Folding
    // off into down would conflate an unconfigured optional capability with an
    // operational failure, and roll the whole deployment up as down for want of
    // an embedding model nobody has chosen, even though every other component
    // is serving normally. Degraded still surfaces it, and the card's own
    // detail says what to do.
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
    expect(states.withWebSocketState).toBe('degraded')
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
    expect(states.withWebSocketState).toBe('down')
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

const HEALTHY_MEMORY: MemoryHealth = {
  state: 'durable',
  backend: 'sqlvector',
  detail: null,
}

function report(name: string, phase: SubsystemPhase): SubsystemReport {
  return { name, phase, detail: null, waiting_on: [] }
}

/** Every backend component healthy, so the subsystem list decides the verdict. */
function statesWith(subsystems: readonly SubsystemReport[] | null) {
  return deriveHealthSubsystemStates(
    okLoadState(HEALTHY_MEMORY),
    true,
    false,
    false,
    subsystems,
  )
}

describe('deriveHealthSubsystemStates declared subsystems', () => {
  it('does not report all systems normal over a blocked subsystem', () => {
    // The pill read green and the dialog headlined "Every tracked component is
    // reporting healthy" while the dashboard's own blockers panel listed five
    // subsystems as not up. `/health` reports infrastructure; `/subsystems`
    // reports capability, and only the first reached this verdict.
    const states = statesWith([report('conversational_actor', 'blocked')])

    expect(states.backendOnlyState).toBe('degraded')
    expect(states.withWebSocketState).toBe('degraded')
  })

  it.each(['waiting', 'rebuilding', 'degraded'] as const)(
    'counts a %s subsystem as degraded',
    (phase) => {
      expect(statesWith([report('memory_backend', phase)]).backendOnlyState).toBe(
        'degraded',
      )
    },
  )

  it.each(['failed', 'unreachable'] as const)(
    'counts a %s subsystem as down',
    (phase) => {
      expect(statesWith([report('memory_backend', phase)]).backendOnlyState).toBe('down')
    },
  )

  it('leaves the verdict alone for a subsystem an operator switched off', () => {
    // Configured, not faulty. The blockers panel still lists it, because that
    // panel answers what stands between the org and progress rather than
    // whether anything is wrong.
    expect(statesWith([report('telemetry', 'disabled')]).backendOnlyState).toBe('ok')
  })

  it('reads an active subsystem as no signal at all', () => {
    expect(statesWith([report('charter_engine', 'active')]).backendOnlyState).toBe('ok')
  })

  it('leaves the verdict to the health probe when nothing has been read', () => {
    // Not knowing is not evidence of a fault, and every existing caller that
    // supplies no list must keep behaving exactly as it did.
    expect(statesWith(null).backendOnlyState).toBe('ok')
  })

  it('takes the worst of several', () => {
    const states = statesWith([
      report('a', 'blocked'),
      report('b', 'failed'),
      report('c', 'active'),
    ])

    expect(states.backendOnlyState).toBe('down')
  })
})
