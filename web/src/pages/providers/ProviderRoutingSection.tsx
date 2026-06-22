/**
 * Routing & resilience settings, surfaced on the Providers page so the
 * operator can tune model routing in context with the providers they are
 * configuring. Reads/writes the ``providers`` settings namespace via the
 * settings API, reusing the shared SettingRow renderer for parity with
 * the main settings screen (badges, env-lock notices, restart hints).
 */
import { useCallback, useEffect, useState } from 'react'
import { Loader2, Route } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { ErrorBanner } from '@/components/ui/error-banner'
import { SectionCard } from '@/components/ui/section-card'
import { SkeletonText } from '@/components/ui/skeleton'
import { getNamespaceSettings, updateSetting } from '@/api/endpoints/settings'
import type { SettingEntry } from '@/api/types/settings'
import { useToastStore } from '@/stores/toast'
import { createLogger } from '@/lib/logger'
import { sanitizeForLog } from '@/utils/logging'
import { getErrorMessage } from '@/utils/errors'
import { SettingRow } from '../settings/SettingRow'

const log = createLogger('ProviderRoutingSection')

/** Keys surfaced here, in display order. */
const ROUTING_KEYS: readonly string[] = [
  'routing_strategy',
  'retry_max_attempts',
  'discovery_allowlist',
  'ollama_default_port',
  'model_refresh_mode',
  'model_refresh_interval_seconds',
  'model_refresh_auto_apply_within_family',
  'cassette_mode',
  'cassette_path',
  // The full provider config blob. Marked sensitive in the registry, so
  // SettingRow renders it redacted; surfaced here so operators can confirm a
  // value is set and paste a corrected blob without leaving the dashboard.
  'configs',
]

interface RoutingState {
  entries: readonly SettingEntry[]
  loading: boolean
  error: string | null
}

function RoutingRows({
  entries,
  dirty,
  saving,
  onChange,
}: {
  entries: readonly SettingEntry[]
  dirty: Readonly<Record<string, string>>
  saving: boolean
  onChange: (key: string, value: string) => void
}) {
  return (
    <div className="divide-y divide-border">
      {entries.map((entry) => (
        <SettingRow
          key={entry.definition.key}
          entry={entry}
          dirtyValue={dirty[entry.definition.key]}
          onChange={(value) => onChange(entry.definition.key, value)}
          saving={saving}
        />
      ))}
    </div>
  )
}

interface RoutingController {
  state: RoutingState
  dirty: Readonly<Record<string, string>>
  saving: boolean
  load: () => void
  handleChange: (key: string, value: string) => void
  handleSave: () => void
}

function useProviderRouting(): RoutingController {
  const [state, setState] = useState<RoutingState>({ entries: [], loading: true, error: null })
  const [dirty, setDirty] = useState<Readonly<Record<string, string>>>({})
  const [saving, setSaving] = useState(false)

  // Reload the persisted base values, resetting the dirty map to
  // ``keepDirty`` (default empty). A partial save passes the edits that
  // failed so they stay pending for a retry while the saved ones clear.
  const load = useCallback((keepDirty: Readonly<Record<string, string>> = {}) => {
    setState((prev) => ({ ...prev, loading: true, error: null }))
    void getNamespaceSettings('providers')
      .then((all) => {
        const entries = ROUTING_KEYS.map((key) =>
          all.find((e) => e.definition.key === key),
        ).filter((e): e is SettingEntry => e !== undefined)
        setState({ entries, loading: false, error: null })
        setDirty(keepDirty)
      })
      .catch((err: unknown) => {
        const message = getErrorMessage(err)
        log.error('load routing settings failed', { error: sanitizeForLog(message) })
        setState({ entries: [], loading: false, error: message })
      })
  }, [])

  useEffect(() => {
    void Promise.resolve().then(() => { load() })
  }, [load])

  const handleChange = useCallback(
    (key: string, value: string) => {
      setDirty((prev) => {
        // Typing the persisted value back in clears the key rather than
        // recording a no-op edit, so "Save" disables and we never POST
        // an unchanged setting.
        const persisted = state.entries.find((entry) => entry.definition.key === key)?.value
        if (persisted === undefined) return prev
        if (value === persisted) {
          return Object.fromEntries(Object.entries(prev).filter(([k]) => k !== key))
        }
        return { ...prev, [key]: value }
      })
    },
    [state.entries],
  )

  const handleSave = useCallback(() => {
    const changed = Object.entries(dirty)
    if (changed.length === 0) return
    setSaving(true)
    void Promise.allSettled(
      changed.map(([key, value]) => updateSetting('providers', key, { value })),
    )
      .then((results) => {
        const failed = changed.filter((_, i) => results[i]?.status === 'rejected')
        if (failed.length === 0) {
          useToastStore.getState().add({ variant: 'success', title: 'Routing settings saved' })
          load()
          return
        }
        // Partial failure: persist the successes (reload base values) but
        // keep the failed edits pending so the operator can retry just
        // those instead of losing every edit to one rejection.
        const firstRejection = results.find(
          (r): r is PromiseRejectedResult => r.status === 'rejected',
        )
        const message = firstRejection
          ? getErrorMessage(firstRejection.reason)
          : 'Some settings could not be saved'
        log.error('save routing settings partial failure', {
          failed: failed.length,
          error: sanitizeForLog(message),
        })
        const failedDirty = Object.fromEntries(failed)
        useToastStore.getState().add({
          variant: 'error',
          title: `Could not save ${String(failed.length)} of ${String(changed.length)} settings`,
          description: message,
        })
        load(failedDirty)
      })
      .finally(() => setSaving(false))
  }, [dirty, load])

  return { state, dirty, saving, load, handleChange, handleSave }
}

export function ProviderRoutingSection() {
  const { state, dirty, saving, load, handleChange, handleSave } = useProviderRouting()
  const dirtyCount = Object.keys(dirty).length

  return (
    <SectionCard title="Routing &amp; resilience" icon={Route}>
      {state.loading ? (
        <SkeletonText lines={5} />
      ) : state.error != null ? (
        <ErrorBanner
          severity="warning"
          title="Could not load routing settings"
          description={state.error}
          onRetry={load}
        />
      ) : (
        <div className="space-y-section-gap">
          <RoutingRows
            entries={state.entries}
            dirty={dirty}
            saving={saving}
            onChange={handleChange}
          />
          <Button onClick={handleSave} disabled={saving || dirtyCount === 0}>
            {saving && <Loader2 className="mr-2 size-4 animate-spin" />}
            Save routing settings
          </Button>
        </div>
      )}
    </SectionCard>
  )
}
