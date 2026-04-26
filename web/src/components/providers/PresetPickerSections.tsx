import { useMemo } from 'react'
import type {
  CloudPreset,
  LocalPreset,
  ProbePresetResponse,
  ProviderConfig,
  ProviderPreset,
} from '@/api/types/providers'
import { CloudProviderGrid } from './CloudProviderGrid'
import { CustomConfigButton } from './CustomConfigButton'
import { DetectedLocalList } from './DetectedLocalList'

export interface PresetPickerSectionsProps {
  /** All presets returned by `GET /providers/presets`. */
  presets: readonly ProviderPreset[]
  /** Probe-local results, keyed by preset name. */
  probeResults: Readonly<Partial<Record<string, ProbePresetResponse>>>
  /** True while a batch probe is in flight. */
  probing: boolean
  /** Currently configured providers (for "already added" badges). */
  providers: Readonly<Record<string, ProviderConfig>>
  /** Open the credential form pre-filled with a cloud preset. */
  onSelectCloud: (presetName: string) => void
  /** Add a local preset using its detected base URL. */
  onAddLocal: (presetName: string, detectedUrl: string) => void | Promise<void>
  /**
   * Open the credential form pre-filled with a cloud counterpart preset
   * (e.g. ``ollama-cloud`` when the user clicks "Add cloud" on the
   * detected local Ollama row).  Re-uses ``onSelectCloud`` semantics.
   */
  onAddCloudCounterpart?: (cloudPresetName: string) => void
  /** Re-run the local-provider batch probe. */
  onReprobe: () => void | Promise<void>
  /** Open the credential form in custom-endpoint mode (no preset). */
  onConfigureManually: () => void
}

/**
 * Three-section provider picker (Cloud / Detected / Manual).
 *
 * Reused on both the wizard's Providers step and the Settings page so
 * first-run and ongoing management share the same UX.  All state and
 * data come in via props -- the component owns no fetching of its own.
 */
export function PresetPickerSections({
  presets,
  probeResults,
  probing,
  providers,
  onSelectCloud,
  onAddLocal,
  onAddCloudCounterpart,
  onReprobe,
  onConfigureManually,
}: PresetPickerSectionsProps) {
  const cloudPresets: readonly CloudPreset[] = useMemo(
    () => presets.filter((p): p is CloudPreset => p.kind === 'cloud'),
    [presets],
  )
  const localPresetsWithCandidates: readonly LocalPreset[] = useMemo(
    () =>
      presets.filter(
        (p): p is LocalPreset => p.kind === 'local' && p.candidate_urls.length > 0,
      ),
    [presets],
  )
  const addedPresets = useMemo(
    () =>
      new Set(
        Object.values(providers)
          .map((p) => p.preset_name)
          .filter((name): name is string => Boolean(name)),
      ),
    [providers],
  )

  return (
    <div className="space-y-section-gap">
      <section aria-labelledby="cloud-providers-heading" className="space-y-3">
        <h3
          id="cloud-providers-heading"
          className="text-sm font-semibold text-foreground"
        >
          Cloud providers
        </h3>
        <CloudProviderGrid
          presets={cloudPresets}
          addedPresets={addedPresets}
          onSelect={onSelectCloud}
        />
      </section>

      <DetectedLocalList
        localPresets={localPresetsWithCandidates}
        probeResults={probeResults}
        probing={probing}
        providers={providers}
        onAddLocal={onAddLocal}
        onAddCloud={onAddCloudCounterpart}
        onReprobe={onReprobe}
      />

      <section aria-labelledby="custom-endpoint-heading" className="space-y-2">
        <h3
          id="custom-endpoint-heading"
          className="text-sm font-semibold text-foreground"
        >
          Configure manually
        </h3>
        <p className="text-xs text-text-muted">
          Use a private gateway, an Anthropic-compatible self-hosted API, or any
          provider not listed above.
        </p>
        <CustomConfigButton onClick={onConfigureManually} />
      </section>
    </div>
  )
}
