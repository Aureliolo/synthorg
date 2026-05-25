import { ChevronDown } from 'lucide-react'
import { useMemo } from 'react'
import { cn } from '@/lib/utils'
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
  /**
   * Per-preset probe failures, keyed by preset name.  Disjoint with
   * ``probeResults``.  Surfaced in the detected-local section so
   * partial failures are visible to the operator.
   */
  probeErrors?: Readonly<Partial<Record<string, string>>>
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
function MoreCloudPresets({
  presets,
  addedPresets,
  onSelect,
}: {
  presets: readonly CloudPreset[]
  addedPresets: ReadonlySet<string>
  onSelect: (presetName: string) => void
}) {
  return (
    <section aria-labelledby="more-providers-heading" className="space-y-3">
      <details className="group space-y-3">
        <summary
          id="more-providers-heading"
          className={cn(
            // Suppress the native disclosure triangle; the
            // ChevronDown below is the only indicator.
            'list-none [&::-webkit-details-marker]:hidden',
            'flex cursor-pointer items-center justify-between',
            'rounded-lg border border-border bg-card p-card',
            'text-sm font-semibold text-foreground',
            'transition-colors duration-[var(--so-transition-fast)]',
            'hover:bg-card-hover hover:border-bright',
          )}
        >
          <span>More providers via LiteLLM ({presets.length})</span>
          <ChevronDown
            aria-hidden="true"
            className="size-4 text-text-muted transition-transform group-open:rotate-180"
          />
        </summary>
        <p className="text-xs text-text-muted">
          Auto-derived from the LiteLLM model catalog. Logos and curated
          defaults are not provided -- click any card to open the credential
          form.
        </p>
        <CloudProviderGrid presets={presets} addedPresets={addedPresets} onSelect={onSelect} />
      </details>
    </section>
  )
}

function CustomEndpointSection({ onConfigureManually }: { onConfigureManually: () => void }) {
  return (
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
  )
}

interface PartitionedPresets {
  featured: readonly CloudPreset[]
  more: readonly CloudPreset[]
  localWithCandidates: readonly LocalPreset[]
  addedPresets: ReadonlySet<string>
}

function _partitionPresets(
  presets: readonly (CloudPreset | LocalPreset)[],
  providers: PresetPickerSectionsProps['providers'],
): PartitionedPresets {
  const featured = presets.filter(
    (p): p is CloudPreset => p.kind === 'cloud' && p.is_featured,
  )
  const more = presets.filter(
    (p): p is CloudPreset => p.kind === 'cloud' && !p.is_featured,
  )
  const localWithCandidates = presets.filter(
    (p): p is LocalPreset => p.kind === 'local' && p.candidate_urls.length > 0,
  )
  const addedPresets = new Set(
    Object.values(providers)
      .map((p) => p.preset_name)
      .filter((name): name is string => Boolean(name)),
  )
  return { featured, more, localWithCandidates, addedPresets }
}

export function PresetPickerSections({
  presets,
  probeResults,
  probeErrors,
  probing,
  providers,
  onSelectCloud,
  onAddLocal,
  onAddCloudCounterpart,
  onReprobe,
  onConfigureManually,
}: PresetPickerSectionsProps) {
  const { featured, more, localWithCandidates, addedPresets } = useMemo(
    () => _partitionPresets(presets, providers),
    [presets, providers],
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
          presets={featured}
          addedPresets={addedPresets}
          onSelect={onSelectCloud}
        />
      </section>
      {more.length > 0 && (
        <MoreCloudPresets
          presets={more}
          addedPresets={addedPresets}
          onSelect={onSelectCloud}
        />
      )}
      <DetectedLocalList
        localPresets={localWithCandidates}
        probeResults={probeResults}
        probeErrors={probeErrors}
        probing={probing}
        providers={providers}
        onAddLocal={onAddLocal}
        onAddCloud={onAddCloudCounterpart}
        onReprobe={onReprobe}
      />
      <CustomEndpointSection onConfigureManually={onConfigureManually} />
    </div>
  )
}
