import { useSetupWizardStore } from '@/stores/setup-wizard'
import type { ThemeSettings } from '@/stores/setup-wizard'
import { cn } from '@/lib/utils'
import { ThemePreview } from './ThemePreview'
import { useStepCompletionSync } from './_hooks'

interface OptionGroupProps<K extends keyof ThemeSettings> {
  label: string
  settingKey: K
  options: readonly { value: ThemeSettings[K]; label: string; description: string }[]
  current: ThemeSettings[K]
  onChange: (key: K, value: ThemeSettings[K]) => void
}

function OptionGroup<K extends keyof ThemeSettings>({
  label,
  settingKey,
  options,
  current,
  onChange,
}: OptionGroupProps<K>) {
  return (
    <fieldset className="space-y-2">
      <legend className="text-sm font-semibold text-foreground">{label}</legend>
      <div className="space-y-1">
        {options.map((opt) => (
          <label
            key={opt.value}
            className={cn(
              'flex cursor-pointer items-start gap-3 rounded-md border p-card-snug transition-colors',
              // Keyboard focus lands on the native radio; surface it on the
              // whole option row so the focus target is visible (DS ring).
              'focus-within:ring-2 focus-within:ring-accent focus-within:ring-offset-2 focus-within:ring-offset-background',
              current === opt.value
                ? 'border-accent bg-accent/5'
                : 'border-border hover:bg-card-hover',
            )}
          >
            <input
              type="radio"
              name={settingKey}
              value={opt.value}
              checked={current === opt.value}
              onChange={() => onChange(settingKey, opt.value)}
              className="mt-0.5 accent-accent"
            />
            <div>
              <span className="text-sm font-medium text-foreground">{opt.label}</span>
              <p className="text-xs text-muted-foreground">{opt.description}</p>
            </div>
          </label>
        ))}
      </div>
    </fieldset>
  )
}

const PALETTE_OPTIONS = [
  { value: 'warm-ops' as const, label: 'Warm Ops', description: 'Warm soft blue accent. The default.' },
  { value: 'ice-station' as const, label: 'Ice Station', description: 'Cool emerald green tones.' },
  { value: 'stealth' as const, label: 'Stealth', description: 'Muted purple, low contrast.' },
  { value: 'signal' as const, label: 'Signal', description: 'Warm orange, high energy.' },
  { value: 'neon' as const, label: 'Neon', description: 'Vibrant cyan, deep blacks.' },
]

const DENSITY_OPTIONS = [
  { value: 'dense' as const, label: 'Dense', description: '12px padding, tight gaps. For power users.' },
  { value: 'balanced' as const, label: 'Balanced', description: '16px padding. Recommended for most users.' },
  { value: 'sparse' as const, label: 'Sparse', description: '20px padding, relaxed layout.' },
]

const ANIMATION_OPTIONS = [
  { value: 'minimal' as const, label: 'Minimal', description: 'Quick fades only, subtle transitions.' },
  { value: 'status-driven' as const, label: 'Status-driven', description: 'Only changed elements animate. Smart and efficient.' },
  { value: 'spring' as const, label: 'Spring', description: 'Playful spring physics, bouncy feedback.' },
  { value: 'instant' as const, label: 'Instant', description: 'No animations at all. Maximum performance.' },
]

const SIDEBAR_OPTIONS = [
  { value: 'rail' as const, label: 'Rail', description: 'Always visible with icons and labels (220px).' },
  { value: 'collapsible' as const, label: 'Collapsible', description: 'Expands and collapses, remembers your preference.' },
  { value: 'hidden' as const, label: 'Hidden', description: 'Hamburger toggle only, full-width content.' },
  { value: 'compact' as const, label: 'Compact', description: 'Icons prominent, text secondary (56px).' },
]

export function ThemeStep() {
  const themeSettings = useSetupWizardStore((s) => s.themeSettings)
  const setThemeSetting = useSetupWizardStore((s) => s.setThemeSetting)

  // Theme step is always valid (every option has a sensible default).
  useStepCompletionSync('theme', true)

  return (
    <div className="space-y-section-gap">
      <div className="space-y-2">
        <h2 className="text-lg font-semibold text-foreground">Personalize Your Experience</h2>
        <p className="text-sm text-muted-foreground">
          Choose how your dashboard looks and feels.
        </p>
      </div>

      <div className="grid grid-cols-1 gap-grid-gap lg:grid-cols-[45%_1fr]">
        {/* Options (left) */}
        <div className="space-y-section-gap">
          <OptionGroup
            label="Color Palette"
            settingKey="palette"
            options={PALETTE_OPTIONS}
            current={themeSettings.palette}
            onChange={setThemeSetting}
          />
          <OptionGroup
            label="Density"
            settingKey="density"
            options={DENSITY_OPTIONS}
            current={themeSettings.density}
            onChange={setThemeSetting}
          />
          <OptionGroup
            label="Animation"
            settingKey="animation"
            options={ANIMATION_OPTIONS}
            current={themeSettings.animation}
            onChange={setThemeSetting}
          />
          <OptionGroup
            label="Sidebar"
            settingKey="sidebar"
            options={SIDEBAR_OPTIONS}
            current={themeSettings.sidebar}
            onChange={setThemeSetting}
          />
        </div>

        {/* Live preview (right). Sticky only on desktop; on a stacked
            mobile layout it sits inline below the options. */}
        <div className="lg:sticky lg:top-8">
          <h3 className="mb-3 text-sm font-semibold text-foreground">Live Preview</h3>
          <ThemePreview settings={themeSettings} />
        </div>
      </div>
    </div>
  )
}
