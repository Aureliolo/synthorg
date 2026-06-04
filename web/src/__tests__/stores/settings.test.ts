import { http, HttpResponse } from 'msw'
import { beforeEach, describe, expect, it } from 'vitest'
import type { SettingEntry } from '@/api/types/settings'
import { buildSettingEntry } from '@/mocks/handlers/settings'
import { apiError, apiSuccess, paginatedFor, voidSuccess } from '@/mocks/handlers'
import type { getAllSettings } from '@/api/endpoints/settings'
import type { PaginatedResult } from '@/api/client'
import { useSettingsStore } from '@/stores/settings'
import { useToastStore } from '@/stores/toast'
import { DEFAULT_CURRENCY } from '@/utils/currencies'
import { server } from '@/test-setup'

/**
 * Characterisation tests for the settings store. Pinned to the
 * pre-split (single-file) `stores/settings.ts` behaviour so the
 * upcoming package-split commit can run them green without
 * touching the assertion surface. Every test exercises an
 * observable invariant (mutation-token ordering, generation drift,
 * savingKeys refcount, error rollback) rather than internal
 * helpers; the slice boundary is free to change underneath.
 */

const SETTINGS_LIST_LIMIT = 200

function singleEntryPage(entries: SettingEntry[]): PaginatedResult<SettingEntry> {
  return {
    data: entries,
    limit: SETTINGS_LIST_LIMIT,
    nextCursor: null,
    hasMore: false,
    pagination: {
      limit: SETTINGS_LIST_LIMIT,
      next_cursor: null,
      has_more: false,
    },
  }
}

function resetSettingsStore(): void {
  useSettingsStore.setState({
    currency: DEFAULT_CURRENCY,
    schema: [],
    entries: [],
    loading: false,
    error: null,
    savingKeys: new Map(),
    appliedMutationTokens: new Map(),
    entriesGeneration: 0,
    saveError: null,
  })
}

interface Deferred<T> {
  promise: Promise<T>
  resolve: (value: T) => void
  reject: (reason?: unknown) => void
}

function deferred<T = void>(): Deferred<T> {
  let resolve!: (value: T) => void
  let reject!: (reason?: unknown) => void
  const promise = new Promise<T>((res, rej) => {
    resolve = res
    reject = rej
  })
  return { promise, resolve, reject }
}

describe('useSettingsStore', () => {
  // Toast cleanup is handled by the global ``afterEach`` in
  // ``test-setup.tsx`` (``useToastStore.getState().dismissAll()``); no
  // file-local beforeEach/afterEach needed beyond store state reset.
  beforeEach(() => {
    resetSettingsStore()
  })

  describe('fetchSettingsData', () => {
    it('populates schema + entries and derives currency from budget namespace', async () => {
      const currencyEntry = buildSettingEntry({
        value: 'EUR',
        source: 'db',
        definition: { namespace: 'budget', key: 'currency' },
      })
      server.use(
        http.get('/api/v1/settings/_schema', () =>
          HttpResponse.json(apiSuccess([])),
        ),
        http.get('/api/v1/settings', () =>
          HttpResponse.json(
            paginatedFor<typeof getAllSettings>(singleEntryPage([currencyEntry])),
          ),
        ),
      )

      await useSettingsStore.getState().fetchSettingsData()

      const state = useSettingsStore.getState()
      expect(state.loading).toBe(false)
      expect(state.error).toBeNull()
      expect(state.entries).toHaveLength(1)
      expect(state.currency).toBe('EUR')
    })

    it('falls back to default currency when budget/currency value is invalid', async () => {
      const badEntry = buildSettingEntry({
        value: 'not-a-code',
        source: 'db',
        definition: { namespace: 'budget', key: 'currency' },
      })
      server.use(
        http.get('/api/v1/settings/_schema', () =>
          HttpResponse.json(apiSuccess([])),
        ),
        http.get('/api/v1/settings', () =>
          HttpResponse.json(
            paginatedFor<typeof getAllSettings>(singleEntryPage([badEntry])),
          ),
        ),
      )

      await useSettingsStore.getState().fetchSettingsData()

      expect(useSettingsStore.getState().currency).toBe(DEFAULT_CURRENCY)
    })

    it('records a partial error when only the schema call fails', async () => {
      server.use(
        http.get('/api/v1/settings/_schema', () =>
          HttpResponse.json(apiError('Schema offline'), { status: 500 }),
        ),
        http.get('/api/v1/settings', () =>
          HttpResponse.json(
            paginatedFor<typeof getAllSettings>(singleEntryPage([])),
          ),
        ),
      )

      await useSettingsStore.getState().fetchSettingsData()

      const state = useSettingsStore.getState()
      expect(state.loading).toBe(false)
      expect(state.error).toMatch(/Schema/)
    })
  })

  describe('refreshEntries', () => {
    it('skips when a save is in flight (savingKeys non-empty)', async () => {
      const seededEntry = buildSettingEntry({
        value: 'seeded',
        definition: { namespace: 'api', key: 'host' },
      })
      const freshEntry = buildSettingEntry({
        value: 'should-not-appear',
        definition: { namespace: 'api', key: 'host' },
      })
      useSettingsStore.setState({
        entries: [seededEntry],
        savingKeys: new Map([['api/host', 1]]),
      })
      server.use(
        http.get('/api/v1/settings', () =>
          HttpResponse.json(
            paginatedFor<typeof getAllSettings>(singleEntryPage([freshEntry])),
          ),
        ),
      )

      await useSettingsStore.getState().refreshEntries()

      expect(useSettingsStore.getState().entries[0]?.value).toBe('seeded')
    })

    it('discards the snapshot when entriesGeneration drifts during the fetch', async () => {
      const seededEntry = buildSettingEntry({
        value: 'seeded',
        definition: { namespace: 'api', key: 'host' },
      })
      const staleEntry = buildSettingEntry({
        value: 'stale-snapshot',
        definition: { namespace: 'api', key: 'host' },
      })
      useSettingsStore.setState({
        entries: [seededEntry],
        entriesGeneration: 5,
      })
      const gate = deferred()
      server.use(
        http.get('/api/v1/settings', async () => {
          await gate.promise
          return HttpResponse.json(
            paginatedFor<typeof getAllSettings>(singleEntryPage([staleEntry])),
          )
        }),
      )

      const refresh = useSettingsStore.getState().refreshEntries()
      // Simulate a concurrent successful save by bumping the generation
      // counter mid-fetch (same effect the updateSetting success branch
      // produces). The refresh must discard its now-stale snapshot.
      useSettingsStore.setState((s) => ({ entriesGeneration: s.entriesGeneration + 1 }))
      gate.resolve()
      await refresh

      expect(useSettingsStore.getState().entries[0]?.value).toBe('seeded')
    })
  })

  describe('updateSetting', () => {
    it('returns the updated entry, appends it to entries, and emits a success toast', async () => {
      server.use(
        http.put('/api/v1/settings/:namespace/:key', async ({ params, request }) => {
          const body = (await request.json()) as { value: string }
          return HttpResponse.json(
            apiSuccess(
              buildSettingEntry({
                value: body.value,
                source: 'db',
                definition: {
                  namespace: String(params.namespace) as SettingEntry['definition']['namespace'],
                  key: String(params.key),
                },
              }),
            ),
          )
        }),
      )

      const result = await useSettingsStore.getState().updateSetting(
        'api',
        'host',
        'http://example.test',
      )

      expect(result).not.toBeNull()
      expect(result?.value).toBe('http://example.test')
      const state = useSettingsStore.getState()
      expect(state.entries.find((e) => e.definition.key === 'host')?.value).toBe(
        'http://example.test',
      )
      expect(state.savingKeys.size).toBe(0)
      expect(state.entriesGeneration).toBeGreaterThan(0)
      const successToasts = useToastStore
        .getState()
        .toasts.filter((t) => t.variant === 'success')
      expect(successToasts.length).toBeGreaterThan(0)
    })

    it('updates currency when budget/currency is the target', async () => {
      server.use(
        http.put('/api/v1/settings/budget/currency', async ({ request }) => {
          const body = (await request.json()) as { value: string }
          return HttpResponse.json(
            apiSuccess(
              buildSettingEntry({
                value: body.value,
                source: 'db',
                definition: { namespace: 'budget', key: 'currency' },
              }),
            ),
          )
        }),
      )

      await useSettingsStore.getState().updateSetting('budget', 'currency', 'GBP')

      expect(useSettingsStore.getState().currency).toBe('GBP')
    })

    it('returns null and sets saveError on failure, emits an error toast', async () => {
      server.use(
        http.put('/api/v1/settings/api/host', () =>
          HttpResponse.json(apiError('Backend unreachable'), { status: 500 }),
        ),
      )

      const result = await useSettingsStore.getState().updateSetting(
        'api',
        'host',
        'http://example.test',
      )

      expect(result).toBeNull()
      const state = useSettingsStore.getState()
      // The exact wording comes from `getErrorMessage`, which may apply
      // status-based fallbacks; the characterised contract is "saveError
      // is set to a non-empty string when the mutation fails".
      expect(state.saveError).toBeTruthy()
      expect(typeof state.saveError).toBe('string')
      expect(state.savingKeys.size).toBe(0)
      const errorToasts = useToastStore
        .getState()
        .toasts.filter((t) => t.variant === 'error')
      expect(errorToasts.length).toBeGreaterThan(0)
    })

    it('drops out-of-order responses: older mutation finishing last does not overwrite newer entries', async () => {
      // Orchestrate two concurrent saves on the same composite key.
      // The OLDER call holds at the server until the NEWER call has
      // already landed; when the older finally returns, the store
      // must drop it (token < lastApplied) and leave entries pinned
      // to the newer value.
      const olderGate = deferred()
      let callCount = 0
      server.use(
        http.put('/api/v1/settings/api/host', async ({ request }) => {
          const body = (await request.json()) as { value: string }
          callCount += 1
          if (callCount === 1) {
            // Older call: park until the newer call has resolved.
            await olderGate.promise
          }
          return HttpResponse.json(
            apiSuccess(
              buildSettingEntry({
                value: body.value,
                source: 'db',
                definition: { namespace: 'api', key: 'host' },
              }),
            ),
          )
        }),
      )

      const olderPromise = useSettingsStore.getState().updateSetting(
        'api',
        'host',
        'older-value',
      )
      const newerResult = await useSettingsStore.getState().updateSetting(
        'api',
        'host',
        'newer-value',
      )
      expect(newerResult?.value).toBe('newer-value')
      expect(
        useSettingsStore
          .getState()
          .entries.find((e) => e.definition.key === 'host')?.value,
      ).toBe('newer-value')

      // Release the older call; its response will arrive AFTER the
      // newer one applied. The store must drop it.
      olderGate.resolve()
      const olderResult = await olderPromise
      expect(olderResult).toBeNull()
      expect(
        useSettingsStore
          .getState()
          .entries.find((e) => e.definition.key === 'host')?.value,
      ).toBe('newer-value')
    })

    it('tracks savingKeys as a refcount: parallel saves on same key drain independently', async () => {
      // Two concurrent saves on api/host. While both are in flight,
      // savingKeys.get('api/host') === 2. After the first resolves,
      // it must still equal 1 (not 0). After both resolve, 0.
      const firstGate = deferred()
      const secondGate = deferred()
      let call = 0
      server.use(
        http.put('/api/v1/settings/api/host', async ({ request }) => {
          const body = (await request.json()) as { value: string }
          call += 1
          if (call === 1) await firstGate.promise
          else await secondGate.promise
          return HttpResponse.json(
            apiSuccess(
              buildSettingEntry({
                value: body.value,
                source: 'db',
                definition: { namespace: 'api', key: 'host' },
              }),
            ),
          )
        }),
      )

      const p1 = useSettingsStore.getState().updateSetting('api', 'host', 'v1')
      const p2 = useSettingsStore.getState().updateSetting('api', 'host', 'v2')

      // Both in flight: refcount is 2.
      expect(useSettingsStore.getState().savingKeys.get('api/host')).toBe(2)

      firstGate.resolve()
      await p1
      // First drained, second still in flight: refcount is 1.
      expect(useSettingsStore.getState().savingKeys.get('api/host')).toBe(1)

      secondGate.resolve()
      await p2
      // Both drained: key is removed entirely.
      expect(useSettingsStore.getState().savingKeys.has('api/host')).toBe(false)
    })
  })

  describe('resetSetting', () => {
    it('returns true and refetches entries on success', async () => {
      const refreshed = buildSettingEntry({
        value: 'reset-default',
        source: 'default',
        definition: { namespace: 'api', key: 'host' },
      })
      server.use(
        http.delete('/api/v1/settings/api/host', () =>
          HttpResponse.json(voidSuccess()),
        ),
        http.get('/api/v1/settings', () =>
          HttpResponse.json(
            paginatedFor<typeof getAllSettings>(singleEntryPage([refreshed])),
          ),
        ),
      )

      const ok = await useSettingsStore.getState().resetSetting('api', 'host')

      expect(ok).toBe(true)
      expect(
        useSettingsStore
          .getState()
          .entries.find((e) => e.definition.key === 'host')?.value,
      ).toBe('reset-default')
      const successToasts = useToastStore
        .getState()
        .toasts.filter((t) => t.variant === 'success')
      expect(successToasts.length).toBeGreaterThan(0)
    })

    it('returns false and emits a warning when generation drifts during the refetch', async () => {
      const seededEntry = buildSettingEntry({
        value: 'seeded',
        definition: { namespace: 'api', key: 'host' },
      })
      const staleSnapshot = buildSettingEntry({
        value: 'stale-snapshot',
        definition: { namespace: 'api', key: 'host' },
      })
      useSettingsStore.setState({ entries: [seededEntry], entriesGeneration: 0 })
      const refetchEnteredGate = deferred()
      const refetchReleaseGate = deferred()
      server.use(
        http.delete('/api/v1/settings/api/host', () =>
          HttpResponse.json(voidSuccess()),
        ),
        http.get('/api/v1/settings', async () => {
          // Signal that the refetch handler has been entered. By this
          // point, `generationAtFetchStart` inside `resetSetting` has
          // already been captured, so the next external bump of
          // `entriesGeneration` deterministically constitutes drift.
          refetchEnteredGate.resolve()
          await refetchReleaseGate.promise
          return HttpResponse.json(
            paginatedFor<typeof getAllSettings>(singleEntryPage([staleSnapshot])),
          )
        }),
      )

      const resetPromise = useSettingsStore.getState().resetSetting('api', 'host')
      await refetchEnteredGate.promise
      // Simulate a concurrent updateSetting completion by bumping
      // entriesGeneration; the reset's apply branch must detect drift
      // and discard the (now-stale) refetched snapshot.
      useSettingsStore.setState((s) => ({ entriesGeneration: s.entriesGeneration + 1 }))
      refetchReleaseGate.resolve()
      const ok = await resetPromise

      expect(ok).toBe(false)
      // Entries left untouched (the stale snapshot was discarded).
      expect(
        useSettingsStore
          .getState()
          .entries.find((e) => e.definition.key === 'host')?.value,
      ).toBe('seeded')
      const warningToasts = useToastStore
        .getState()
        .toasts.filter((t) => t.variant === 'warning')
      expect(warningToasts.length).toBeGreaterThan(0)
    })

    it('returns false on a failed server-side reset and emits an error toast', async () => {
      server.use(
        http.delete('/api/v1/settings/api/host', () =>
          HttpResponse.json(apiError('Permission denied'), { status: 403 }),
        ),
      )

      const ok = await useSettingsStore.getState().resetSetting('api', 'host')

      expect(ok).toBe(false)
      expect(useSettingsStore.getState().saveError).toBe('Permission denied')
      const errorToasts = useToastStore
        .getState()
        .toasts.filter((t) => t.variant === 'error')
      expect(errorToasts.length).toBeGreaterThan(0)
    })
  })

  describe('updateFromWsEvent', () => {
    it('triggers refreshEntries on a system-channel event', async () => {
      const refreshed = buildSettingEntry({
        value: 'from-ws',
        definition: { namespace: 'api', key: 'host' },
      })
      let calls = 0
      server.use(
        http.get('/api/v1/settings', () => {
          calls += 1
          return HttpResponse.json(
            paginatedFor<typeof getAllSettings>(singleEntryPage([refreshed])),
          )
        }),
      )

      useSettingsStore.getState().updateFromWsEvent({
        channel: 'system',
        event_type: 'system.startup',
        timestamp: '2026-05-24T00:00:00Z',
        version: 1,
        payload: {},
      })
      // Allow the void-Promise chain inside updateFromWsEvent to settle.
      await new Promise<void>((resolve) => {
        queueMicrotask(resolve)
      })
      await new Promise<void>((resolve) => {
        setTimeout(resolve, 0)
      })

      expect(calls).toBeGreaterThanOrEqual(1)
    })

    it('ignores events on other channels', () => {
      const seededEntry = buildSettingEntry({
        value: 'seeded',
        definition: { namespace: 'api', key: 'host' },
      })
      useSettingsStore.setState({ entries: [seededEntry] })

      useSettingsStore.getState().updateFromWsEvent({
        channel: 'tasks',
        event_type: 'task.updated',
        timestamp: '2026-05-24T00:00:00Z',
        version: 1,
        payload: {},
      })

      expect(useSettingsStore.getState().entries[0]?.value).toBe('seeded')
    })
  })
})
