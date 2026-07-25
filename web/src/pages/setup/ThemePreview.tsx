import { useEffect, useState } from 'react'
import {
  motion,
  AnimatePresence,
  type TargetAndTransition,
  type Transition,
} from 'motion/react'
import { cn } from '@/lib/utils'
import { reducedMotionInstant, tweenDefault, tweenFast } from '@/lib/motion'
import { MetricCard } from '@/components/ui/metric-card'
import { AgentCard } from '@/components/ui/agent-card'
import { DeptHealthBar } from '@/components/ui/dept-health-bar'
import { Button } from '@/components/ui/button'
import { SectionCard } from '@/components/ui/section-card'
import { StatPill } from '@/components/ui/stat-pill'
import type {
  AnimationPreset,
  ColorPalette,
  Density,
  SidebarMode,
  Typography,
} from '@/stores/theme'
import { BarChart3, Home, Users, ListChecks, Settings, ChevronRight } from 'lucide-react'

// The preview renders the same axes the dashboard-wide theme store owns, so it
// mirrors exactly what the live app will look like once a choice is written
// through to ``appearance.*``.
export interface ThemePreviewSettings {
  palette: ColorPalette
  density: Density
  animation: AnimationPreset
  sidebar: SidebarMode
  typography: Typography
}

// Mirror the theme store: ``balanced`` is the default and carries no override
// class; ``medium`` is a distinct, denser tier (``density-medium``).
const DENSITY_CLASS: Record<Density, string> = {
  dense: 'density-dense',
  balanced: '',
  medium: 'density-medium',
  sparse: 'density-sparse',
}

const PALETTE_CLASS: Record<ColorPalette, string> = {
  'warm-ops': '',
  'ice-station': 'theme-ice-station',
  stealth: 'theme-stealth',
  signal: 'theme-signal',
  neon: 'theme-neon',
}

// Entry transition per preset. The springs are deliberately more expressive
// than the dashboard defaults so the difference is legible in the small demo.
const DEMO_TRANSITION: Record<AnimationPreset, Transition> = {
  minimal: tweenFast,
  'status-driven': tweenDefault,
  spring: { type: 'spring', stiffness: 220, damping: 12 },
  instant: reducedMotionInstant,
  aggressive: { type: 'spring', stiffness: 420, damping: 9 },
}

// Entry MOTION per preset, chosen so each reads as visibly distinct rather than
// differing only by a few milliseconds of tween: minimal is a pure fade (no
// travel), status-driven slides, spring overshoots from far, instant snaps,
// aggressive snaps hard with a twist.
const DEMO_VARIANTS: Record<
  AnimationPreset,
  { initial: TargetAndTransition; animate: TargetAndTransition; exit: TargetAndTransition }
> = {
  minimal: { initial: { opacity: 0 }, animate: { opacity: 1 }, exit: { opacity: 0 } },
  'status-driven': {
    initial: { opacity: 0, x: 16 },
    animate: { opacity: 1, x: 0 },
    exit: { opacity: 0, x: -16 },
  },
  spring: {
    initial: { opacity: 0, y: 28, scale: 0.85 },
    animate: { opacity: 1, y: 0, scale: 1 },
    exit: { opacity: 0, y: -28, scale: 0.85 },
  },
  instant: { initial: { opacity: 1 }, animate: { opacity: 1 }, exit: { opacity: 1 } },
  aggressive: {
    initial: { opacity: 0, scale: 0.6, rotate: -8 },
    animate: { opacity: 1, scale: 1, rotate: 0 },
    exit: { opacity: 0, scale: 0.6, rotate: 8 },
  },
}

// Fixed illustration widths for the mock sidebar (preview-only chrome, not
// the real sidebar-width tokens).
const SIDEBAR_PREVIEW_WIDTH: Record<'rail' | 'compact' | 'full', string> = {
  rail: 'w-10',
  compact: 'w-20',
  full: 'w-28',
}

// Mirrors the real sidebar's per-mode shape: ``rail`` is the only desktop mode
// that hides labels, and ``compact`` differs from a fully expanded sidebar by
// column width alone.
const SIDEBAR_PREVIEW_SHAPE: Record<
  Exclude<SidebarMode, 'hidden'>,
  { width: keyof typeof SIDEBAR_PREVIEW_WIDTH; labels: boolean }
> = {
  rail: { width: 'rail', labels: false },
  compact: { width: 'compact', labels: true },
  collapsible: { width: 'full', labels: true },
  persistent: { width: 'full', labels: true },
}

const SIDEBAR_NAV = [
  { icon: Home, label: 'Overview' },
  { icon: Users, label: 'Agents' },
  { icon: ListChecks, label: 'Tasks' },
  { icon: Settings, label: 'Settings' },
]

interface SidebarNavItemProps {
  icon: React.ElementType
  label: string
  isActive: boolean
  showLabel: boolean
}

function SidebarNavItem({ icon: Icon, label, isActive, showLabel }: SidebarNavItemProps) {
  return (
    <div
      className={cn(
        'flex items-center gap-1.5 rounded-md px-1.5 py-1 text-text-secondary',
        isActive && 'bg-accent/10 text-accent',
      )}
    >
      <Icon className="size-3.5 shrink-0" />
      {showLabel && <span className="truncate text-micro">{label}</span>}
    </div>
  )
}

function SidebarPreview({ mode }: { mode: SidebarMode }) {
  if (mode === 'hidden') return null

  const shape = SIDEBAR_PREVIEW_SHAPE[mode]

  return (
    <div
      className={cn(
        'flex flex-col gap-2 rounded-lg border border-border bg-bg-surface p-card-snug',
        'transition-[width] duration-[var(--so-transition-default)]',
        SIDEBAR_PREVIEW_WIDTH[shape.width],
      )}
    >
      {SIDEBAR_NAV.map(({ icon, label }) => (
        <SidebarNavItem
          key={label}
          icon={icon}
          label={label}
          isActive={label === 'Overview'}
          showLabel={shape.labels}
        />
      ))}
      {mode === 'collapsible' && (
        <div className="mt-auto flex justify-center pt-1 text-text-muted">
          <ChevronRight className="size-3" />
        </div>
      )}
    </div>
  )
}

function AnimationDemo({ animation }: { animation: AnimationPreset }) {
  const [cycling, setCycling] = useState(true)
  const variant = DEMO_VARIANTS[animation]
  const transition = DEMO_TRANSITION[animation]
  const showCard = animation === 'instant' || cycling

  useEffect(() => {
    if (animation === 'instant') return
    // Start with the card visible; interval toggles visibility. Skip the toggle
    // while the tab is hidden so a backgrounded preview does not burn CPU.
    let active = true
    const interval = setInterval(() => {
      if (active && !document.hidden) setCycling((v) => !v)
    }, 1600)
    return () => {
      active = false
      clearInterval(interval)
    }
  }, [animation])

  return (
    <div className="flex items-center gap-3">
      {/* status-driven means "only changed elements animate", so a static
          neighbour makes that literal: this idle chip never moves while the
          card beside it does. */}
      {animation === 'status-driven' && (
        <div className="flex items-center gap-2 rounded-md border border-border bg-bg-surface px-3 py-1.5 opacity-60">
          <span className="size-2 rounded-full bg-text-muted" />
          <span className="text-xs text-text-muted">Idle</span>
        </div>
      )}
      <AnimatePresence mode="wait">
        {showCard && (
          <motion.div
            key="demo-card"
            initial={variant.initial}
            animate={variant.animate}
            exit={variant.exit}
            transition={transition}
            className="flex items-center gap-2 rounded-md border border-border bg-bg-surface px-3 py-1.5"
          >
            <span className="size-2 rounded-full bg-success" />
            <span className="text-xs text-foreground">Agent active</span>
          </motion.div>
        )}
      </AnimatePresence>
      <span className="text-micro text-text-muted italic">
        {animation === 'instant' ? 'No animations' : animation}
      </span>
    </div>
  )
}

export interface ThemePreviewProps {
  settings: ThemePreviewSettings
}

export function ThemePreview({ settings }: ThemePreviewProps) {
  return (
    <div
      className={cn(
        'flex gap-grid-gap rounded-lg border border-border bg-background p-card',
        DENSITY_CLASS[settings.density],
        PALETTE_CLASS[settings.palette],
      )}
      data-density={settings.density}
      data-animation={settings.animation}
      data-sidebar={settings.sidebar}
      data-typography={settings.typography}
    >
      {/* Sidebar mockup */}
      <SidebarPreview mode={settings.sidebar} />

      {/* Main content */}
      <div className="flex-1 space-y-section-gap">
        {/* Metric cards */}
        <div className="grid grid-cols-2 gap-grid-gap">
          <MetricCard label="Active Agents" value={12} />
          <MetricCard label="Tasks Today" value={47} />
        </div>

        {/* Agent card mock (placeholder name; no real people per the
            vendor-name policy) */}
        <AgentCard name="Sample Agent" role="CEO" department="executive" status="idle" />

        {/* Health bar */}
        <DeptHealthBar name="Engineering" health={72} agentCount={3} />

        {/* Animation demo */}
        <AnimationDemo animation={settings.animation} />

        {/* Buttons */}
        <div className="flex flex-wrap gap-2">
          <Button size="sm">Default</Button>
          <Button variant="outline" size="sm">Outline</Button>
          <Button variant="ghost" size="sm">Ghost</Button>
          <Button variant="secondary" size="sm">Secondary</Button>
        </div>

        {/* Section card */}
        <SectionCard title="Sample Section" icon={BarChart3}>
          <p className="text-sm text-muted-foreground">
            Content with <span className="text-foreground">text-foreground</span> and{' '}
            <span className="text-compact text-muted-foreground">timestamps</span>.
          </p>
          <div className="mt-2 flex gap-2">
            <StatPill label="Agents" value={5} />
            <StatPill label="Cost" value="~45/mo" />
          </div>
        </SectionCard>
      </div>
    </div>
  )
}
