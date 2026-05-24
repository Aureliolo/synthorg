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

async function fetchAuditImpl(
  set: ProvidersSet,
  providerName: string,
  opts: { limit?: number },
): Promise<void> {
  const limit = opts.limit ?? DEFAULT_AUDIT_LIMIT
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
    // Guard: only commit the result if the active provider hasn't
    // changed while we were awaiting. Without this a slow fetch for
    // provider A can land after the user already switched to B and
    // overwrite B's audit page.
    set((s) => {
      if (s.auditProviderName !== providerName) return s
      return {
        ...s,
        auditEvents: page.data,
        auditNextCursor: page.nextCursor,
        auditHasMore: page.hasMore,
        auditLoading: false,
      }
    })
  } catch (err) {
    log.warn('Failed to fetch provider audit:', getErrorMessage(err))
    set((s) => {
      if (s.auditProviderName !== providerName) return s
      return {
        ...s,
        auditLoading: false,
        auditError: getErrorMessage(err),
      }
    })
  }
}

function canFetchMoreAudit(get: ProvidersGet): boolean {
  const state = get()
  return Boolean(
    state.auditHasMore
    && state.auditNextCursor
    && state.auditProviderName !== null
    && !state.auditLoading
    && !state.auditLoadingMore,
  )
}

async function fetchMoreAuditImpl(
  set: ProvidersSet,
  get: ProvidersGet,
): Promise<void> {
  if (!canFetchMoreAudit(get)) return
  const state = get()
  const providerName = state.auditProviderName as string
  const cursor = state.auditNextCursor as string
  set({ auditLoadingMore: true })
  try {
    const page = await listProviderAudit(providerName, {
      cursor,
      limit: DEFAULT_AUDIT_LIMIT,
    })
    set((s) => {
      if (s.auditProviderName !== providerName) return s
      return {
        ...s,
        auditEvents: [...s.auditEvents, ...page.data],
        auditNextCursor: page.nextCursor,
        auditHasMore: page.hasMore,
        auditLoadingMore: false,
        auditError: null,
      }
    })
  } catch (err) {
    log.warn('Failed to fetch more provider audit:', getErrorMessage(err))
    set((s) => {
      if (s.auditProviderName !== providerName) return s
      return {
        ...s,
        auditLoadingMore: false,
        auditError: getErrorMessage(err),
      }
    })
  }
}

async function fetchRateLimitsImpl(
  set: ProvidersSet,
  name: string,
): Promise<void> {
  set({
    rateLimitsLoading: true,
    rateLimitsError: null,
    rateLimits: null,
    rateLimitsProviderName: name,
  })
  try {
    const config = await getProviderRateLimits(name)
    set((s) => {
      if (s.rateLimitsProviderName !== name) return s
      return { ...s, rateLimits: config, rateLimitsLoading: false }
    })
  } catch (err) {
    log.warn('Failed to fetch rate limits:', getErrorMessage(err))
    set((s) => {
      if (s.rateLimitsProviderName !== name) return s
      return {
        ...s,
        rateLimitsLoading: false,
        rateLimitsError: getErrorMessage(err),
      }
    })
  }
}

async function fetchPresetOverrideImpl(
  set: ProvidersSet,
  presetName: string,
): Promise<void> {
  set({
    presetOverrideLoading: true,
    presetOverrideError: null,
    presetOverride: null,
    presetOverridePresetName: presetName,
  })
  try {
    const override = await getPresetOverride(presetName)
    set((s) => {
      if (s.presetOverridePresetName !== presetName) return s
      return { ...s, presetOverride: override, presetOverrideLoading: false }
    })
  } catch (err) {
    log.warn('Failed to fetch preset override:', getErrorMessage(err))
    set((s) => {
      if (s.presetOverridePresetName !== presetName) return s
      return {
        ...s,
        presetOverrideLoading: false,
        presetOverrideError: getErrorMessage(err),
      }
    })
  }
}

/**
 * Read-only slice for the audit drawer + rate-limit + preset-override
 * detail panes.
 */
export function createAuditActions(set: ProvidersSet, get: ProvidersGet) {
  return {
    fetchAudit: (
      providerName: string,
      opts: { limit?: number } = {},
    ) => fetchAuditImpl(set, providerName, opts),
    fetchMoreAudit: () => fetchMoreAuditImpl(set, get),
    clearAudit: () => {
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
    fetchRateLimits: (name: string) => fetchRateLimitsImpl(set, name),
    fetchPresetOverride: (presetName: string) =>
      fetchPresetOverrideImpl(set, presetName),
  }
}
