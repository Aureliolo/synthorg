import { useEffect, useState } from 'react'
import { motion, AnimatePresence, type Transition } from 'motion/react'
import { cn } from '@/lib/utils'
import { tweenDefault, tweenFast } from '@/lib/motion'
import { MetricCard } from '@/components/ui/metric-card'
import { AgentCard } from '@/components/ui/agent-card'
import { DeptHealthBar } from '@/components/ui/dept-health-bar'
import { Button } from '@/components/ui/button'
import { SectionCard } from '@/components/ui/section-card'
import { StatPill } from '@/components/ui/stat-pill'
import type { ThemeSettings } from '@/stores/setup-wizard'
import { BarChart3, Home, Users, ListChecks, Settings, ChevronRight } from 'lucide-react'

const DENSITY_CLASS: Record<string, string> = {
  dense: 'density-dense',
  balanced: 'density-medium',
  sparse: 'density-sparse',
}

const PALETTE_CLASS: Record<ThemeSettings['palette'], string> = {
  'warm-ops': '',
  'ice-station': 'theme-ice-station',
  stealth: 'theme-stealth',
  signal: 'theme-signal',
  neon: 'theme-neon',
}

// Animation transitions for the preview. The bouncier spring (lower damping
// than springGentle) is preview-specific: deliberately more expressive than
// the dashboard spring so users can see the difference.
const ANIMATION_TRANSITIONS: Record<ThemeSettings['animation'], Transition> = {
  minimal: tweenFast,
  'status-driven': tweenDefault,
  spring: { type: 'spring', stiffness: 200, damping: 15 },
  instant: { type: 'tween', duration: 0 },
}

// Duration for the "agent active" dot pulse. Long enough to read as
// breathing, short enough to cycle multiple times per visible interaction.
const PULSE_DURATION_S = 0.6

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
  isCompact: boolean
}

function SidebarNavItem({ icon: Icon, label, isActive, isCompact }: SidebarNavItemProps) {
  return (
    <div
      className={cn(
        'flex items-center gap-1.5 rounded-md px-1.5 py-1 text-text-secondary',
        isActive && 'bg-accent/10 text-accent',
      )}
    >
      <Icon className="size-3.5 shrink-0" />
      {!isCompact && <span className="truncate text-[9px]">{label}</span>}
    </div>
  )
}

function SidebarPreview({ mode }: { mode: ThemeSettings['sidebar'] }) {
  if (mode === 'hidden') return null

  const isCompact = mode === 'compact'

  return (
    <div
      className={cn(
        'flex flex-col gap-2 rounded-lg border border-border bg-bg-surface p-2 transition-all duration-200',
        isCompact ? 'w-10' : 'w-28',
      )}
    >
      {SIDEBAR_NAV.map(({ icon, label }) => (
        <SidebarNavItem
          key={label}
          icon={icon}
          label={label}
          isActive={label === 'Overview'}
          isCompact={isCompact}
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

function AnimationDemo({ animation }: { animation: ThemeSettings['animation'] }) {
  const [cycling, setCycling] = useState(true)
  const transition = ANIMATION_TRANSITIONS[animation]
  const showCard = animation === 'instant' || cycling

  useEffect(() => {
    if (animation === 'instant') return
    // Start with the card visible; interval toggles visibility
    let active = true
    const interval = setInterval(() => {
      if (active) setCycling((v) => !v)
    }, 1500)
    return () => {
      active = false
      clearInterval(interval)
    }
  }, [animation])

  return (
    <div className="flex items-center gap-3">
      <AnimatePresence mode="wait">
        {showCard && (
          <motion.div
            key="demo-card"
            initial={{ opacity: 0, y: 8, scale: 0.95 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: -8, scale: 0.95 }}
            transition={transition}
            className="flex items-center gap-2 rounded-md border border-border bg-bg-surface px-3 py-1.5"
          >
            <motion.div
              animate={{ scale: [1, 1.3, 1] }}
              transition={{
                ...transition,
                repeat: Infinity,
                repeatDelay: 1,
                duration: animation === 'instant' ? 0 : PULSE_DURATION_S,
              }}
              className="size-2 rounded-full bg-success"
            />
            <span className="text-xs text-foreground">Agent active</span>
          </motion.div>
        )}
      </AnimatePresence>
      <span className="text-[9px] text-text-muted italic">
        {animation === 'instant' ? 'No animations' : animation}
      </span>
    </div>
  )
}

export interface ThemePreviewProps {
  settings: ThemeSettings
}

export function ThemePreview({ settings }: ThemePreviewProps) {
  return (
    <div
      className={cn(
        'flex gap-3 rounded-lg border border-border bg-background p-4',
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
      <div className="flex-1 space-y-4">
        {/* Metric cards */}
        <div className="grid grid-cols-2 gap-3">
          <MetricCard label="Active Agents" value={12} />
          <MetricCard label="Tasks Today" value={47} />
        </div>

        {/* Agent card mock */}
        <AgentCard name="Akira Tanaka" role="CEO" department="executive" status="idle" />

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
