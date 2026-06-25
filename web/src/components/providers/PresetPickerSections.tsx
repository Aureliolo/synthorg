import { Check, ChevronDown, Search, X } from 'lucide-react'
import { useMemo, useState } from 'react'
import { cn } from '@/lib/utils'
import { Button } from '@/components/ui/button'
import { InputField } from '@/components/ui/input-field'
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
  /**
   * Remove a configured provider. When supplied, each chip in the
   * "Configured" summary carries an X. Omit to render the summary read-only.
   */
  onRemoveProvider?: ((name: string) => void) | undefined
}

// Shared summary styling for the disclosure sections (Cloud providers,
// LiteLLM extras) so the collapsible headers stay visually identical. The
// native disclosure triangle is suppressed; the ChevronDown is the indicator.
const DISCLOSURE_SUMMARY = cn(
  'list-none [&::-webkit-details-marker]:hidden',
  'flex cursor-pointer items-center justify-between',
  'rounded-lg border border-border bg-card p-card',
  'text-sm font-semibold text-foreground',
  'transition-colors duration-[var(--so-transition-fast)]',
  'hover:bg-card-hover hover:border-bright',
)

function DisclosureChevron() {
  return (
    <ChevronDown
      aria-hidden="true"
      className="size-4 text-text-muted transition-transform group-open:rotate-180"
    />
  )
}

/**
 * Three-section provider picker (Cloud / Detected / Manual).
 *
 * Reused on both the wizard's Providers step and the Settings page so
 * first-run and ongoing management share the same UX.  All state and
 * data come in via props -- the component owns no fetching of its own.
 */
function FeaturedCloudPresets({
  presets,
  addedPresets,
  onSelect,
}: {
  presets: readonly CloudPreset[]
  addedPresets: ReadonlySet<string>
  onSelect: (presetName: string) => void
}) {
  return (
    <section aria-labelledby="cloud-providers-heading" className="space-y-3">
      <details className="group space-y-3">
        <summary id="cloud-providers-heading" className={DISCLOSURE_SUMMARY}>
          <span>Cloud providers ({presets.length})</span>
          <DisclosureChevron />
        </summary>
        <CloudProviderGrid presets={presets} addedPresets={addedPresets} onSelect={onSelect} />
      </details>
    </section>
  )
}

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
        <summary id="more-providers-heading" className={DISCLOSURE_SUMMARY}>
          <span>More providers via LiteLLM ({presets.length})</span>
          <DisclosureChevron />
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

/**
 * Positive confirmation of what the user has already set up. Especially
 * important now the provider cards collapse by default (the per-card "added"
 * badge would otherwise be the only signal). When ``onRemove`` is supplied
 * each chip carries an X to delete the provider (remove-then-re-add is also
 * how an operator re-runs model discovery).
 */
function ConfiguredProvidersSummary({
  providers,
  onRemove,
}: {
  providers: Readonly<Record<string, ProviderConfig>>
  onRemove?: ((name: string) => void) | undefined
}) {
  const entries = Object.entries(providers)
  if (entries.length === 0) return null
  return (
    <section
      aria-label="Configured providers"
      className="space-y-2 rounded-lg border border-success/30 bg-success/5 p-card"
    >
      <h3 className="flex items-center gap-1.5 text-sm font-semibold text-foreground">
        <Check className="size-4 text-success" aria-hidden="true" />
        Configured ({entries.length})
      </h3>
      <div className="flex flex-wrap gap-2">
        {entries.map(([name, config]) => (
          <span
            key={name}
            className="inline-flex items-center gap-1.5 rounded-full border border-border bg-card py-1 pl-2.5 pr-1 text-xs text-foreground"
          >
            {name}
            <span className="text-text-muted">{config.models.length} models</span>
            {onRemove ? (
              <Button
                type="button"
                variant="ghost"
                size="icon-xs"
                onClick={() => onRemove(name)}
                aria-label={`Remove ${name}`}
                title={`Remove ${name}`}
              >
                <X className="size-3" aria-hidden="true" />
              </Button>
            ) : null}
          </span>
        ))}
      </div>
    </section>
  )
}

function ProviderSearch({ query, onChange }: { query: string; onChange: (value: string) => void }) {
  return (
    <InputField
      label="Find a provider"
      value={query}
      onValueChange={onChange}
      placeholder="Search providers by name..."
      leadingIcon={<Search className="size-3.5" />}
      trailingElement={
        query ? (
          <Button
            type="button"
            variant="ghost"
            size="icon-xs"
            onClick={() => onChange('')}
            aria-label="Clear search"
          >
            <X className="size-3.5" aria-hidden="true" />
          </Button>
        ) : undefined
      }
    />
  )
}

function CloudSearchResults({
  results,
  addedPresets,
  onSelect,
}: {
  results: readonly CloudPreset[]
  addedPresets: ReadonlySet<string>
  onSelect: (presetName: string) => void
}) {
  return (
    <section aria-label="Provider search results" className="space-y-3">
      <h3 className="text-sm font-semibold text-foreground">Search results ({results.length})</h3>
      {results.length === 0 ? (
        <p className="text-xs text-text-muted">
          No cloud providers match. Try a different name, check the detected/manual options below, or
          clear the search.
        </p>
      ) : (
        <CloudProviderGrid presets={results} addedPresets={addedPresets} onSelect={onSelect} />
      )}
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
        Point SynthOrg at any provider not listed above: a private gateway, a
        self-hosted endpoint, or a custom API base URL.
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
  onRemoveProvider,
}: PresetPickerSectionsProps) {
  const { featured, more, localWithCandidates, addedPresets } = useMemo(
    () => _partitionPresets(presets, providers),
    [presets, providers],
  )
  const [query, setQuery] = useState('')
  const trimmedQuery = query.trim().toLowerCase()
  const searchResults = useMemo(() => {
    if (!trimmedQuery) return []
    return [...featured, ...more].filter((p) =>
      `${p.display_name} ${p.description} ${p.name}`.toLowerCase().includes(trimmedQuery),
    )
  }, [featured, more, trimmedQuery])

  return (
    <div className="space-y-section-gap">
      <ConfiguredProvidersSummary providers={providers} onRemove={onRemoveProvider} />
      <ProviderSearch query={query} onChange={setQuery} />
      {trimmedQuery ? (
        <CloudSearchResults
          results={searchResults}
          addedPresets={addedPresets}
          onSelect={onSelectCloud}
        />
      ) : (
        <>
          <FeaturedCloudPresets presets={featured} addedPresets={addedPresets} onSelect={onSelectCloud} />
          {more.length > 0 && (
            <MoreCloudPresets presets={more} addedPresets={addedPresets} onSelect={onSelectCloud} />
          )}
        </>
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
