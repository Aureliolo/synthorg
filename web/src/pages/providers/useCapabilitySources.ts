/**
 * Data controller for the Capability Sources panel. Hydrates every declared
 * source with its last ingest outcome and writes each enable / disable /
 * refresh straight back through the capability-source REST API (Pure API
 * Consumer: no client-side persistence, the backend is the sole source of
 * truth).
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import {
  listCapabilitySources,
  refreshCapabilitySource,
  refreshDueCapabilitySources,
  setCapabilitySource,
} from '@/api/endpoints/providers'
import type { CapabilitySourceDTO } from '@/api/types/providers'
import { useToastStore } from '@/stores/toast'
import { createLogger } from '@/lib/logger'
import { sanitizeForLog } from '@/utils/logging'
import { getErrorMessage } from '@/utils/errors'
import { createCancellationToken, type CancellationToken } from '@/utils/cancellation'

const log = createLogger('CapabilitySources')

export interface CapabilitySourcesState {
  sources: readonly CapabilitySourceDTO[]
  anyHealthy: boolean
  loading: boolean
  error: string | null
  busyLabels: ReadonlySet<string>
  refreshingAll: boolean
}

export interface CapabilitySourcesController {
  state: CapabilitySourcesState
  load: () => void
  setEnabled: (label: string, enabled: boolean) => void
  refreshOne: (label: string) => void
  refreshAll: () => void
}

const INITIAL: CapabilitySourcesState = {
  sources: [],
  anyHealthy: false,
  loading: true,
  error: null,
  busyLabels: new Set(),
  refreshingAll: false,
}

/** Enabled sources that are not currently answering. */
export function failingSources(
  sources: readonly CapabilitySourceDTO[],
): readonly CapabilitySourceDTO[] {
  return sources.filter((source) => source.enabled && !source.is_healthy)
}

function withLabel(
  set: (updater: (prev: CapabilitySourcesState) => CapabilitySourcesState) => void,
  label: string,
  busy: boolean,
): void {
  set((prev) => {
    const next = new Set(prev.busyLabels)
    if (busy) next.add(label)
    else next.delete(label)
    return { ...prev, busyLabels: next }
  })
}

function useLoad(
  setState: (u: (p: CapabilitySourcesState) => CapabilitySourcesState) => void,
): (token: CancellationToken) => void {
  return useCallback(
    (token: CancellationToken) => {
      setState((prev) => ({ ...prev, loading: true, error: null }))
      void listCapabilitySources()
        .then((response) => {
          if (token.cancelled()) return
          setState((prev) => ({
            ...prev,
            sources: response.sources,
            anyHealthy: response.any_healthy,
            loading: false,
            error: null,
          }))
        })
        .catch((err: unknown) => {
          if (token.cancelled()) return
          const message = getErrorMessage(err)
          log.error('load capability sources failed', {
            error: sanitizeForLog(message),
          })
          setState((prev) => ({ ...prev, loading: false, error: message }))
        })
    },
    [setState],
  )
}

function useSetEnabled(
  setState: (u: (p: CapabilitySourcesState) => CapabilitySourcesState) => void,
): CapabilitySourcesController['setEnabled'] {
  return useCallback(
    (label, enabled) => {
      withLabel(setState, label, true)
      // No feed_url: the write is a full replace, and sending an empty
      // one here would reset an operator's custom URL every time somebody
      // toggled the switch.
      void setCapabilitySource(label, { enabled })
        .then((response) => {
          setState((prev) => ({
            ...prev,
            sources: response.sources,
            anyHealthy: response.any_healthy,
          }))
          useToastStore.getState().add({
            variant: 'success',
            title: enabled ? 'Source enabled' : 'Source disabled',
            description: enabled
              ? 'Its measurements grade models again.'
              : 'Its rows are kept, so re-enabling needs no re-fetch.',
          })
        })
        .catch((err: unknown) => {
          const message = getErrorMessage(err)
          log.error('set capability source failed', {
            error: sanitizeForLog(message),
          })
          useToastStore.getState().add({
            variant: 'error',
            title: 'Could not change the source',
            description: message,
          })
        })
        .finally(() => {
          withLabel(setState, label, false)
        })
    },
    [setState],
  )
}

function useRefreshOne(
  setState: (u: (p: CapabilitySourcesState) => CapabilitySourcesState) => void,
): CapabilitySourcesController['refreshOne'] {
  return useCallback(
    (label) => {
      withLabel(setState, label, true)
      void refreshCapabilitySource(label)
        .then((response) => {
          setState((prev) => ({
            ...prev,
            sources: response.sources,
            anyHealthy: response.any_healthy,
          }))
          const refreshed = response.sources.find((s) => s.label === label)
          // A refresh that fails is a completed request, so the toast
          // reports the outcome rather than the round trip.
          useToastStore.getState().add(
            refreshed?.is_healthy ?? false
              ? { variant: 'success', title: 'Source refreshed' }
              : {
                  variant: 'error',
                  title: 'The source did not answer',
                  description: refreshed?.last_error ?? '',
                },
          )
        })
        .catch((err: unknown) => {
          const message = getErrorMessage(err)
          log.error('refresh capability source failed', {
            error: sanitizeForLog(message),
          })
          useToastStore.getState().add({
            variant: 'error',
            title: 'Could not refresh the source',
            description: message,
          })
        })
        .finally(() => {
          withLabel(setState, label, false)
        })
    },
    [setState],
  )
}

function useRefreshAll(
  setState: (u: (p: CapabilitySourcesState) => CapabilitySourcesState) => void,
): CapabilitySourcesController['refreshAll'] {
  return useCallback(() => {
    setState((prev) => ({ ...prev, refreshingAll: true }))
    void refreshDueCapabilitySources({ force: true })
      .then((response) => {
        setState((prev) => ({
          ...prev,
          sources: response.sources,
          anyHealthy: response.any_healthy,
        }))
        useToastStore.getState().add({
          variant: response.any_healthy ? 'success' : 'error',
          title: response.any_healthy
            ? 'Sources refreshed'
            : 'No source is answering',
        })
      })
      .catch((err: unknown) => {
        const message = getErrorMessage(err)
        log.error('refresh capability sources failed', {
          error: sanitizeForLog(message),
        })
        useToastStore.getState().add({
          variant: 'error',
          title: 'Could not refresh the sources',
          description: message,
        })
      })
      .finally(() => {
        setState((prev) => ({ ...prev, refreshingAll: false }))
      })
  }, [setState])
}

export function useCapabilitySources(): CapabilitySourcesController {
  const [state, setState] = useState<CapabilitySourcesState>(INITIAL)
  const tokenRef = useRef<CancellationToken | null>(null)
  const loadWith = useLoad(setState)

  const load = useCallback(() => {
    tokenRef.current?.cancel()
    const token = createCancellationToken()
    tokenRef.current = token
    loadWith(token)
  }, [loadWith])

  useEffect(() => {
    load()
    return () => {
      tokenRef.current?.cancel()
    }
  }, [load])

  return {
    state,
    load,
    setEnabled: useSetEnabled(setState),
    refreshOne: useRefreshOne(setState),
    refreshAll: useRefreshAll(setState),
  }
}
