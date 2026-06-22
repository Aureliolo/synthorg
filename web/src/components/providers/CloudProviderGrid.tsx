import { cn } from '@/lib/utils'
import type { CloudPreset } from '@/api/types/providers'
import { ProviderLogo } from './ProviderLogo'

interface CloudProviderGridProps {
  presets: readonly CloudPreset[]
  /** Names of presets that already have a configured provider. */
  addedPresets?: ReadonlySet<string>
  /** Called with the preset name when the user clicks a card. */
  onSelect: (presetName: string) => void
}

interface CloudProviderCardProps {
  preset: CloudPreset
  added: boolean
  onClick: () => void
}

function CloudProviderCard({ preset, added, onClick }: CloudProviderCardProps) {
  return (
    <button
      type="button"
      aria-label={`Add ${preset.display_name}${added ? ' (already configured)' : ''}`}
      onClick={onClick}
      disabled={added}
      className={cn(
        'flex flex-col items-center gap-2 rounded-lg border p-card text-center transition-all',
        'duration-[var(--so-transition-fast)]',
        'hover:bg-card-hover hover:border-bright',
        'disabled:cursor-not-allowed disabled:opacity-60 disabled:hover:border-border disabled:hover:bg-card',
        'border-border bg-card',
      )}
    >
      <ProviderLogo name={preset.name} size={32} />
      <span className="text-sm font-medium text-foreground">{preset.display_name}</span>
      <span className="line-clamp-2 text-xs text-text-muted">{preset.description}</span>
      {added && (
        <span className="text-xs font-medium text-success" aria-hidden="true">
          Configured
        </span>
      )}
    </button>
  )
}

/**
 * Cloud-providers grid -- one card per `CloudPreset`.
 *
 * Click → `onSelect(presetName)` -- the consumer is expected to open
 * the credential form modal pre-filled with the chosen preset.
 *
 * Already-configured presets render disabled with a "Configured" tag.
 */
export function CloudProviderGrid({
  presets,
  addedPresets,
  onSelect,
}: CloudProviderGridProps) {
  return (
    <div className="grid grid-cols-3 gap-grid-gap max-[1023px]:grid-cols-2">
      {presets.map((preset) => (
        <CloudProviderCard
          key={preset.name}
          preset={preset}
          added={addedPresets?.has(preset.name) ?? false}
          onClick={() => onSelect(preset.name)}
        />
      ))}
    </div>
  )
}
