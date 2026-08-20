import {
  listProviders,
  getProviderHealth,
  recheckAllProviderHealth,
} from '@/api/endpoints/providers'
import { getErrorMessage } from '@/utils/errors'
import { createLogger } from '@/lib/logger'
import { normalizeProviders } from '@/utils/providers'
import { emitPlainErrorToast, emitSuccessToast } from './crud-helpers'
import { bumpHealthRevision, currentHealthRevision } from './health-revision'
import type { ProviderHealthStatus, ProviderHealthSummary } from '@/api/types/providers'
import type { ProviderSortKey } from '@/utils/providers'
import type { ProvidersGet, ProvidersSet } from './types'

const log = createLogger('providers')

let _listRequestId = 0

/**
 * The hydration read currently open, so concurrent askers join it.
 *
 * The settings page renders one `MODEL_REF` widget per bound model, and each
 * one hydrated the catalogue itself with `if (providers.length === 0 &&
 * !listLoading) void fetchProviders()`. Every widget mounts in the same commit,
 * so all of them run that check before any of their state updates land: a live
 * run fired `GET /providers` thirty times on one page load, discarded
 * twenty-nine of the responses, and did the same again on every remount.
 *
 * A check-then-act across a render commit needs the guard where the act
 * happens, not in each caller.
 */
let _openHydration: Promise<void> | null = null

/** Drop any coalesced hydration, so a reset store re-reads rather than joins. */
export function resetProviderHydration(): void {
  _openHydration = null
}

/**
 * Read each provider's recorded health, best-effort and per provider.
 *
 * @returns The map to apply, or `null` when a recheck landed while the
 * reads were in flight: these replay the stored aggregate, so they are no
 * fresher than the verdict they would undo.
 */
async function readHealthMap(
  names: readonly string[],
): Promise<Record<string, ProviderHealthSummary> | null> {
  const healthRevision = currentHealthRevision()
  const results = await Promise.allSettled(
    names.map((name) => getProviderHealth(name)),
  )
  if (currentHealthRevision() !== healthRevision) return null
  const healthMap: Record<string, ProviderHealthSummary> = {}
  for (let i = 0; i < names.length; i++) {
    const result = results[i]!
    if (result.status === 'fulfilled') {
      healthMap[names[i]!] = result.value
    } else {
      log.warn(
        'Failed to fetch health for provider:',
        names[i],
        getErrorMessage(result.reason),
      )
    }
  }
  return healthMap
}

export function createListActions(set: ProvidersSet, get: ProvidersGet) {
  return {
    /**
     * Load the catalogue once, for a consumer that only needs it present.
     *
     * Distinct from `fetchProviders`, and the distinction is load-bearing: a
     * refresh issued after a mutation must re-read, so it cannot be allowed to
     * join a read that started before the write. This one means "make sure it
     * is loaded", so it returns immediately when it is and otherwise joins
     * whatever read is already open.
     */
    ensureProvidersLoaded: async () => {
      if (get().providers.length > 0) return
      _openHydration ??= get()
        .fetchProviders()
        .finally(() => {
          _openHydration = null
        })
      await _openHydration
    },

    fetchProviders: async () => {
      const requestId = ++_listRequestId
      set({ listLoading: true, listError: null })
      const isLatest = () => requestId === _listRequestId
      try {
        const record = await listProviders()
        if (!isLatest()) return
        const providers = normalizeProviders(record)
        set({ providers })

        const healthMap = await readHealthMap(providers.map((p) => p.name))
        if (!isLatest() || healthMap === null) return
        set({ healthMap })
      } catch (err) {
        if (!isLatest()) return
        log.error('Failed to fetch providers:', getErrorMessage(err))
        set({ listError: getErrorMessage(err) })
      } finally {
        // Clear ``listLoading`` for the LATEST request even on the
        // stale-return paths so an overlapping fetch can't leave the
        // skeleton stuck on.  The newer request flips it back to true
        // at its own start, so there is no flicker.
        if (isLatest()) set({ listLoading: false })
      }
    },

    /**
     * Call every provider now and adopt the health those calls produce.
     *
     * The operator's one control when the dashboard says a provider is
     * unhealthy but they believe they have fixed it. Best-effort: the
     * server keeps each provider's recorded health when its call fails,
     * so a partial sweep still improves the picture.
     */
    recheckAllHealth: async () => {
      set({ recheckingAllHealth: true })
      try {
        const healthMap = await recheckAllProviderHealth()
        // The open detail page reads ``selectedProviderHealth``, not the
        // list's map, so a sweep that covered it has to say so there too or
        // that badge keeps the verdict the sweep just replaced.
        const selected = get().selectedProvider?.name
        const refreshed = selected === undefined ? undefined : healthMap[selected]
        // Claimed before applying, so a detail read still in flight from an
        // individual recheck drops its own health rather than resolving last
        // and restoring the verdict this sweep just replaced.
        bumpHealthRevision()
        set(
          refreshed === undefined
            ? { healthMap }
            : { healthMap, selectedProviderHealth: refreshed },
        )
        emitSuccessToast(
          `Rechecked ${Object.keys(healthMap).length} providers`,
        )
      } catch (err) {
        log.warn('Provider recheck failed:', getErrorMessage(err))
        emitPlainErrorToast('Could not recheck providers', err)
      } finally {
        set({ recheckingAllHealth: false })
      }
    },

    setSearchQuery: (q: string) => set({ searchQuery: q }),
    setHealthFilter: (h: ProviderHealthStatus | null) => set({ healthFilter: h }),
    setSortBy: (key: ProviderSortKey) => set({ sortBy: key }),
    setSortDirection: (dir: 'asc' | 'desc') => set({ sortDirection: dir }),
  }
}
