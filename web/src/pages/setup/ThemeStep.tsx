import { useEffect } from 'react'
import { cn } from '@/lib/utils'
import {
  ANIMATION_PRESETS,
  COLOR_PALETTES,
  DENSITIES,
  SIDEBAR_MODES,
  useThemeStore,
} from '@/stores/theme'
import type {
  AnimationPreset,
  ColorPalette,
  Density,
  SidebarMode,
} from '@/stores/theme'
import { ThemePreview } from './ThemePreview'
import { useStepCompletionSync } from './_hooks'

interface OptionGroupProps<V extends string> {
  label: string
  name: string
  options: readonly { value: V; label: string; description: string }[]
  current: V
  onSelect: (value: V) => void
}

function OptionGroup<V extends string>({
  label,
  name,
  options,
  current,
  onSelect,
}: OptionGroupProps<V>) {
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
              name={name}
              value={opt.value}
              checked={current === opt.value}
              onChange={() => onSelect(opt.value)}
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

interface OptionMeta {
  label: string
  description: string
}

// Each axis is described by a total ``Record`` over its union and rendered in
// the order of the store's exported tuple, so a mode added to the store is a
// type error here rather than an option silently missing from the wizard.
function optionsFor<V extends string>(
  values: readonly V[],
  meta: Record<V, OptionMeta>,
): readonly { value: V; label: string; description: string }[] {
  return values.map((value) => ({ value, ...meta[value] }))
}

const PALETTE_META: Record<ColorPalette, OptionMeta> = {
  'warm-ops': { label: 'Warm Ops', description: 'Warm soft blue accent. The default.' },
  'ice-station': { label: 'Ice Station', description: 'Cool emerald green tones.' },
  stealth: { label: 'Stealth', description: 'Muted purple, low contrast.' },
  signal: { label: 'Signal', description: 'Warm orange, high energy.' },
  neon: { label: 'Neon', description: 'Vibrant cyan, deep blacks.' },
}

const DENSITY_META: Record<Density, OptionMeta> = {
  dense: { label: 'Dense', description: '12px padding, tight gaps. For power users.' },
  balanced: { label: 'Balanced', description: '16px padding. Recommended for most users.' },
  medium: { label: 'Medium', description: '14px padding, one notch tighter than balanced.' },
  sparse: { label: 'Sparse', description: '20px padding, relaxed layout.' },
}

const ANIMATION_META: Record<AnimationPreset, OptionMeta> = {
  minimal: { label: 'Minimal', description: 'Quick fades only, no movement.' },
  spring: { label: 'Spring', description: 'Playful spring physics, bouncy feedback.' },
  instant: { label: 'Instant', description: 'No animations at all. Maximum performance.' },
  'status-driven': {
    label: 'Status-driven',
    description: 'Only changed elements animate; the rest stay put.',
  },
  aggressive: {
    label: 'Aggressive',
    description: 'Motion on every transition, at full strength.',
  },
}

const SIDEBAR_META: Record<SidebarMode, OptionMeta> = {
  rail: { label: 'Rail', description: 'Icons only in a 56px rail; labels on hover.' },
  collapsible: {
    label: 'Collapsible',
    description: 'Toggles between 220px and a 56px rail, remembering your choice.',
  },
  hidden: { label: 'Hidden', description: 'No sidebar; full-width content with a menu toggle.' },
  persistent: {
    label: 'Persistent',
    description: 'Always expanded at 220px with labels, no collapse toggle.',
  },
  compact: { label: 'Compact', description: 'Icons with labels in a tighter 180px column.' },
}

const PALETTE_OPTIONS = optionsFor(COLOR_PALETTES, PALETTE_META)
const DENSITY_OPTIONS = optionsFor(DENSITIES, DENSITY_META)
const ANIMATION_OPTIONS = optionsFor(ANIMATION_PRESETS, ANIMATION_META)
const SIDEBAR_OPTIONS = optionsFor(SIDEBAR_MODES, SIDEBAR_META)

export function ThemeStep() {
  const colorPalette = useThemeStore((s) => s.colorPalette)
  const density = useThemeStore((s) => s.density)
  const animation = useThemeStore((s) => s.animation)
  const sidebarMode = useThemeStore((s) => s.sidebarMode)
  const typography = useThemeStore((s) => s.typography)
  const setColorPalette = useThemeStore((s) => s.setColorPalette)
  const setDensity = useThemeStore((s) => s.setDensity)
  const setAnimation = useThemeStore((s) => s.setAnimation)
  const setSidebarMode = useThemeStore((s) => s.setSidebarMode)
  const hydrated = useThemeStore((s) => s.hydrated)
  const hydrate = useThemeStore((s) => s.hydrate)

  // Theme step is always valid (every option has a sensible default).
  useStepCompletionSync('theme', true)

  // The wizard renders outside the authed shell that normally hydrates the
  // theme store, so hydrate here: a mid-wizard reload then shows the operator's
  // already-persisted choices instead of snapping back to defaults. Each axis
  // change writes straight through to ``appearance.*`` via the store setters,
  // exactly like every other wizard step.
  useEffect(() => {
    if (!hydrated) void hydrate()
  }, [hydrated, hydrate])

  return (
    <div className="space-y-section-gap">
      <div className="space-y-2">
        <h2 className="text-lg font-semibold text-foreground">Personalize Your Experience</h2>
        <p className="text-sm text-muted-foreground">
          Choose how your dashboard looks and feels. Changes apply and save right away.
        </p>
      </div>

      <div className="grid grid-cols-1 gap-grid-gap lg:grid-cols-[45%_1fr]">
        {/* Options (left) */}
        <div className="space-y-section-gap">
          <OptionGroup
            label="Color Palette"
            name="palette"
            options={PALETTE_OPTIONS}
            current={colorPalette}
            onSelect={setColorPalette}
          />
          <OptionGroup
            label="Density"
            name="density"
            options={DENSITY_OPTIONS}
            current={density}
            onSelect={setDensity}
          />
          <OptionGroup
            label="Animation"
            name="animation"
            options={ANIMATION_OPTIONS}
            current={animation}
            onSelect={setAnimation}
          />
          <OptionGroup
            label="Sidebar"
            name="sidebar"
            options={SIDEBAR_OPTIONS}
            current={sidebarMode}
            onSelect={setSidebarMode}
          />
        </div>

        {/* Live preview (right). Sticky on desktop so it follows while the
            options scroll; ``self-start`` keeps the column content-height so
            ``sticky`` has room to pin (a stretched grid item cannot stick). On
            a stacked mobile layout it sits inline below the options. */}
        <div className="lg:sticky lg:top-8 lg:self-start">
          <h3 className="mb-3 text-sm font-semibold text-foreground">Live Preview</h3>
          <ThemePreview
            settings={{
              palette: colorPalette,
              density,
              animation,
              sidebar: sidebarMode,
              typography,
            }}
          />
        </div>
      </div>
    </div>
  )
}
