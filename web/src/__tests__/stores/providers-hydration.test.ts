import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { http, HttpResponse } from 'msw'
import { server } from '@/test-setup'
import { useProvidersStore, resetProvidersStore } from '@/stores/providers'
import { buildProvider } from '@/mocks/handlers/providers/crud'
import { paginatedEnvelopeFor } from '@/mocks/handlers/helpers'
import type { listProviders } from '@/api/endpoints/providers'

/**
 * One catalogue read per hydration, across a reset.
 *
 * The settings page mounts one `MODEL_REF` widget per bound model and every
 * one of them asks for the catalogue in the same commit, so the read is
 * coalesced into a single open promise. The coalescing slot is module state,
 * which means its owner has to be tracked: a read that settles after a reset
 * has already handed the slot to a newer read, and clearing it there sends the
 * next asker off to open a third one whose request id then invalidates the
 * second, discarding a response that was already on its way.
 */

/** A hydration held open until the test releases it. */
interface PendingRead {
  /** Let the `GET /providers` this read is serving return. */
  release: () => void
}

describe('provider hydration coalescing', () => {
  let requests: number
  let pending: PendingRead[]

  beforeEach(() => {
    resetProvidersStore()
    requests = 0
    pending = []
    server.use(
      http.get('*/providers', async () => {
        requests++
        await new Promise<void>((resolve) => {
          pending.push({ release: resolve })
        })
        return HttpResponse.json(
          paginatedEnvelopeFor<typeof listProviders>([
            buildProvider({ name: 'example-provider' }),
          ]),
        )
      }),
      http.get('*/providers/:name/health', () =>
        HttpResponse.json({
          data: {
            last_check_timestamp: null,
            avg_response_time_ms: 0,
            error_rate_percent_24h: 0,
            calls_last_24h: 0,
            health_status: 'unknown',
            liveness_calls: 0,
            liveness_error_rate_percent: 0,
            total_tokens_24h: 0,
            total_cost_24h: 0,
          },
          error: null,
          error_detail: null,
          success: true,
        }),
      ),
    )
  })

  afterEach(() => {
    for (const read of pending) read.release()
    resetProvidersStore()
  })

  it('joins the open read instead of opening a second one', async () => {
    const { ensureProvidersLoaded } = useProvidersStore.getState()
    const first = ensureProvidersLoaded()
    const second = ensureProvidersLoaded()
    await waitForRequests(1)

    pending[0]!.release()
    await Promise.all([first, second])

    expect(requests).toBe(1)
  })

  it('a read settling after a reset leaves the newer read joinable', async () => {
    const { ensureProvidersLoaded } = useProvidersStore.getState()
    const beforeReset = ensureProvidersLoaded()
    await waitForRequests(1)

    // The reset invalidates the open read and empties the slot, so the next
    // asker opens its own rather than joining a read that started before it.
    resetProvidersStore()
    const afterReset = ensureProvidersLoaded()
    await waitForRequests(2)

    // The pre-reset read settles LAST. Its cleanup runs against a slot that
    // now belongs to the read opened after the reset.
    pending[0]!.release()
    await beforeReset

    // A third asker must join the read still in flight. Opening its own would
    // both duplicate the request and invalidate the second read's response.
    const joiner = useProvidersStore.getState().ensureProvidersLoaded()
    pending[1]!.release()
    await Promise.all([afterReset, joiner])

    expect(requests).toBe(2)
    expect(Object.keys(useProvidersStore.getState().providers).length).toBe(1)
  })

  /**
   * Wait until the mock server has taken *count* catalogue requests.
   *
   * The handler blocks, so a request is observable only once msw has entered
   * it; awaiting the store promise would deadlock on a read this test has not
   * released yet.
   */
  async function waitForRequests(count: number): Promise<void> {
    await vi.waitFor(() => {
      expect(requests).toBe(count)
    })
  }
})
