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
import { getCrudErrorTitle, getErrorMessage } from '@/utils/errors'
import { SettingRow } from '../settings/SettingRow'

const log = createLogger('ProviderRoutingSection')

/** Keys surfaced here, in display order. */
const ROUTING_KEYS: readonly string[] = [
  'routing_strategy',
  'retry_max_attempts',
  'discovery_allowlist',
  'ollama_default_port',
  'cassette_mode',
  'cassette_path',
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

export function ProviderRoutingSection() {
  const [state, setState] = useState<RoutingState>({ entries: [], loading: true, error: null })
  const [dirty, setDirty] = useState<Readonly<Record<string, string>>>({})
  const [saving, setSaving] = useState(false)

  const load = useCallback(() => {
    setState((prev) => ({ ...prev, loading: true, error: null }))
    void getNamespaceSettings('providers')
      .then((all) => {
        const entries = ROUTING_KEYS.map((key) =>
          all.find((e) => e.definition.key === key),
        ).filter((e): e is SettingEntry => e !== undefined)
        setState({ entries, loading: false, error: null })
        setDirty({})
      })
      .catch((err: unknown) => {
        const message = getErrorMessage(err)
        log.error('load routing settings failed', { error: sanitizeForLog(message) })
        setState({ entries: [], loading: false, error: message })
      })
  }, [])

  useEffect(() => {
    void Promise.resolve().then(load)
  }, [load])

  const handleChange = useCallback((key: string, value: string) => {
    setDirty((prev) => ({ ...prev, [key]: value }))
  }, [])

  const handleSave = useCallback(() => {
    const changed = Object.entries(dirty)
    if (changed.length === 0) return
    setSaving(true)
    void Promise.all(
      changed.map(([key, value]) =>
        updateSetting('providers', key, { value }),
      ),
    )
      .then(() => {
        useToastStore.getState().add({ variant: 'success', title: 'Routing settings saved' })
        load()
      })
      .catch((err: unknown) => {
        log.error('save routing settings failed', { error: sanitizeForLog(getErrorMessage(err)) })
        useToastStore.getState().add({
          variant: 'error',
          ...getCrudErrorTitle(err, 'Could not save routing settings'),
          description: getErrorMessage(err),
        })
      })
      .finally(() => setSaving(false))
  }, [dirty, load])

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
