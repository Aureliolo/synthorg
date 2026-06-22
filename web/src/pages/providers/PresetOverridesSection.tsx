import { useState } from 'react'
import { SlidersHorizontal } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { SectionCard } from '@/components/ui/section-card'
import type { ProviderPreset } from '@/api/types/providers'
import { PresetOverrideDrawer } from './PresetOverrideDrawer'

export interface PresetOverridesSectionProps {
  presets: readonly ProviderPreset[]
}

/**
 * Operator surface for overriding a preset's connection defaults
 * (cloud base URL / local candidate URLs) without editing code. Each
 * preset opens the {@link PresetOverrideDrawer}; the drawer owns the
 * fetch + persist round-trip via the providers store.
 */
export function PresetOverridesSection({ presets }: PresetOverridesSectionProps) {
  const [selected, setSelected] = useState<ProviderPreset | null>(null)

  if (presets.length === 0) return null

  return (
    <SectionCard title="Preset connection overrides" icon={SlidersHorizontal}>
      <p className="mb-3 text-xs text-text-secondary">
        Point a built-in preset at a self-hosted gateway or alternate endpoint.
        Overrides apply to every provider created from the preset.
      </p>
      <ul className="divide-y divide-border">
        {presets.map((preset) => (
          <li
            key={preset.name}
            className="flex items-center justify-between gap-3 py-2"
          >
            <div className="min-w-0">
              <p className="truncate text-sm text-foreground">
                {preset.display_name}
              </p>
              <p className="truncate text-xs text-text-secondary">{preset.name}</p>
            </div>
            <Button
              variant="secondary"
              size="sm"
              onClick={() => setSelected(preset)}
            >
              Override
            </Button>
          </li>
        ))}
      </ul>
      <PresetOverrideDrawer
        preset={selected}
        open={selected !== null}
        onClose={() => setSelected(null)}
      />
    </SectionCard>
  )
}
