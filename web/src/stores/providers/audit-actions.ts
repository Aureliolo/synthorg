import {
  listProviderAudit,
  getProviderRateLimits,
  getPresetOverride,
} from '@/api/endpoints/providers'
import { createLogger } from '@/lib/logger'
import { getErrorMessage } from '@/utils/errors'
import type { ProvidersGet, ProvidersSet } from './types'

const log = createLogger('providers-audit')

const DEFAULT_AUDIT_LIMIT = 50

/**
 * Read-only slice for the audit drawer + rate-limit + preset-override
 * detail panes.  Mutations live in ``crud-actions.ts``; this slice
 * owns the cursor-paginated fetches and the per-detail-pane error
 * state per the canonical store contract in ``web/CLAUDE.md`` (list
 * fetches set ``error`` on the store; the page-level surface
 * consumes the error state and renders an ``ErrorBanner``).
 */
export function createAuditActions(set: ProvidersSet, get: ProvidersGet) {
  return {
    fetchAudit: async (
      providerName: string,
      opts: { limit?: number } = {},
    ): Promise<void> => {
      const limit = opts.limit ?? DEFAULT_AUDIT_LIMIT
      // Reset state on every fetch: a new provider invalidates any
      // previous audit pagination cursor.
      set({
        auditEvents: [],
        auditNextCursor: null,
        auditHasMore: false,
        auditLoading: true,
        auditLoadingMore: false,
        auditError: null,
        auditProviderName: providerName,
      })
      try {
        const page = await listProviderAudit(providerName, { limit })
        set({
          auditEvents: page.data,
          auditNextCursor: page.nextCursor,
          auditHasMore: page.hasMore,
          auditLoading: false,
        })
      } catch (err) {
        log.warn('Failed to fetch provider audit:', getErrorMessage(err))
        set({
          auditLoading: false,
          auditError: getErrorMessage(err),
        })
      }
    },

    fetchMoreAudit: async (): Promise<void> => {
      const state = get()
      if (
        !state.auditHasMore ||
        !state.auditNextCursor ||
        state.auditProviderName === null ||
        state.auditLoading ||
        state.auditLoadingMore
      ) {
        return
      }
      const providerName = state.auditProviderName
      const cursor = state.auditNextCursor
      set({ auditLoadingMore: true })
      try {
        const page = await listProviderAudit(providerName, { cursor })
        // Guard against the user navigating away mid-fetch: discard
        // results that no longer match the active provider.
        const after = get()
        if (after.auditProviderName !== providerName) return
        set({
          auditEvents: [...after.auditEvents, ...page.data],
          auditNextCursor: page.nextCursor,
          auditHasMore: page.hasMore,
          auditLoadingMore: false,
        })
      } catch (err) {
        log.warn('Failed to fetch more provider audit:', getErrorMessage(err))
        set({
          auditLoadingMore: false,
          auditError: getErrorMessage(err),
        })
      }
    },

    clearAudit: (): void => {
      set({
        auditEvents: [],
        auditNextCursor: null,
        auditHasMore: false,
        auditLoading: false,
        auditLoadingMore: false,
        auditError: null,
        auditProviderName: null,
      })
    },

    fetchRateLimits: async (name: string): Promise<void> => {
      set({ rateLimitsLoading: true, rateLimitsError: null })
      try {
        const config = await getProviderRateLimits(name)
        set({ rateLimits: config, rateLimitsLoading: false })
      } catch (err) {
        log.warn('Failed to fetch rate limits:', getErrorMessage(err))
        set({
          rateLimitsLoading: false,
          rateLimitsError: getErrorMessage(err),
        })
      }
    },

    fetchPresetOverride: async (presetName: string): Promise<void> => {
      set({ presetOverrideLoading: true, presetOverrideError: null })
      try {
        const override = await getPresetOverride(presetName)
        set({ presetOverride: override, presetOverrideLoading: false })
      } catch (err) {
        log.warn('Failed to fetch preset override:', getErrorMessage(err))
        set({
          presetOverrideLoading: false,
          presetOverrideError: getErrorMessage(err),
        })
      }
    },
  }
}
