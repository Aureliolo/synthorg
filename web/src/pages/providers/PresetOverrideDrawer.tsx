import { useEffect, useState } from 'react'
import { Button } from '@/components/ui/button'
import { Drawer } from '@/components/ui/drawer'
import { ErrorBanner } from '@/components/ui/error-banner'
import { InputField } from '@/components/ui/input-field'
import { Skeleton } from '@/components/ui/skeleton'
import { useProvidersStore } from '@/stores/providers'
import type {
  CloudPreset,
  LocalPreset,
  PresetOverride,
  PresetOverrideUpdateRequest,
  ProviderPreset,
} from '@/api/types/providers'

interface PresetOverrideDrawerProps {
  preset: ProviderPreset | null
  open: boolean
  onClose: () => void
}

function isCloud(preset: ProviderPreset): preset is CloudPreset {
  return preset.kind === 'cloud'
}

function isLocal(preset: ProviderPreset): preset is LocalPreset {
  return preset.kind === 'local'
}

function joinList(values: readonly string[] | null): string {
  return values === null ? '' : values.join('\n')
}

function parseLines(raw: string): readonly string[] | null {
  const lines = raw
    .split(/\r?\n/)
    .map((s) => s.trim())
    .filter((s) => s.length > 0)
  return lines.length === 0 ? null : lines
}

/**
 * Inner form for the preset override.  Re-mounted via the outer
 * drawer's ``key`` whenever the read state changes, which lets us
 * seed initial state from props without a setState-in-effect.
 */
function PresetOverrideForm({
  preset,
  override,
  onClose,
}: {
  preset: CloudPreset | LocalPreset
  override: PresetOverride | null
  onClose: () => void
}) {
  const updatePresetOverride = useProvidersStore((s) => s.updatePresetOverride)
  const deletePresetOverride = useProvidersStore((s) => s.deletePresetOverride)

  const [baseUrl, setBaseUrl] = useState(() => override?.base_url ?? '')
  const [candidateUrls, setCandidateUrls] = useState(() =>
    joinList(override?.candidate_urls ?? null),
  )
  const [submitting, setSubmitting] = useState(false)

  const handleSave = async (): Promise<void> => {
    const payload: PresetOverrideUpdateRequest = {}
    if (isCloud(preset)) {
      const trimmed = baseUrl.trim()
      payload.base_url = trimmed === '' ? null : trimmed
    } else {
      payload.candidate_urls = parseLines(candidateUrls)
    }
    setSubmitting(true)
    const result = await updatePresetOverride(preset.name, payload)
    setSubmitting(false)
    if (result !== null) {
      onClose()
    }
  }

  const handleClear = async (): Promise<void> => {
    setSubmitting(true)
    const ok = await deletePresetOverride(preset.name)
    setSubmitting(false)
    if (ok) {
      onClose()
    }
  }

  return (
    <>
      {isCloud(preset) && (
        <InputField
          label="Base URL override"
          hint={`Defaults to ${preset.default_base_url ?? '(provider default)'}`}
          value={baseUrl}
          onChange={(e) => setBaseUrl(e.target.value)}
          placeholder="https://api.example.com/v1"
        />
      )}
      {isLocal(preset) && (
        <InputField
          label="Candidate URLs"
          hint="One URL per line; replaces the preset's discovery candidates."
          value={candidateUrls}
          onChange={(e) => setCandidateUrls(e.target.value)}
          multiline
          rows={4}
        />
      )}

      <div className="flex justify-end gap-grid-gap pt-card">
        <Button
          variant="ghost"
          onClick={() => void handleClear()}
          disabled={submitting || override === null}
        >
          Clear override
        </Button>
        <Button variant="secondary" onClick={onClose} disabled={submitting}>
          Cancel
        </Button>
        <Button onClick={() => void handleSave()} disabled={submitting}>
          {submitting ? 'Saving…' : 'Save override'}
        </Button>
      </div>
    </>
  )
}

/**
 * Drawer for reading + writing the operator override on top of an
 * in-code preset.  Field shape is gated by the preset's ``kind``:
 * cloud presets cannot override ``candidate_urls``; local presets
 * cannot override ``base_url``.  The backend rejects mismatches
 * with HTTP 422 if the form somehow still sends them.
 */
export function PresetOverrideDrawer({
  preset,
  open,
  onClose,
}: PresetOverrideDrawerProps) {
  const override = useProvidersStore((s) => s.presetOverride)
  const loading = useProvidersStore((s) => s.presetOverrideLoading)
  const error = useProvidersStore((s) => s.presetOverrideError)
  const fetchPresetOverride = useProvidersStore((s) => s.fetchPresetOverride)

  useEffect(() => {
    if (open && preset) {
      void fetchPresetOverride(preset.name)
    }
  }, [open, preset, fetchPresetOverride])

  if (preset === null) return null

  // Form state is seeded from the loaded override via remount-on-key.
  const formKey = override
    ? `${override.preset_name}/${override.updated_at ?? 'new'}`
    : 'no-override'

  return (
    <Drawer
      open={open}
      onClose={onClose}
      title={`Override · ${preset.display_name}`}
      ariaLabel="Edit preset override"
      width="narrow"
    >
      <div className="flex flex-col gap-section-gap p-card">
        {error && (
          <ErrorBanner
            severity="error"
            title="Failed to load override"
            description={error}
            onRetry={() => {
              void fetchPresetOverride(preset.name)
            }}
          />
        )}

        {loading ? (
          <div className="flex flex-col gap-grid-gap">
            <Skeleton className="h-12 w-full" />
            <Skeleton className="h-24 w-full" />
          </div>
        ) : (
          <PresetOverrideForm
            key={formKey}
            preset={preset}
            override={override}
            onClose={onClose}
          />
        )}
      </div>
    </Drawer>
  )
}
