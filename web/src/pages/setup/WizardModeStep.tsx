import { useCallback, useId, useRef } from 'react'
import { useNavigate } from 'react-router'
import { useSetupWizardStore } from '@/stores/setup-wizard'
import type { WizardMode } from '@/stores/setup-wizard'
import { cn, FOCUS_RING } from '@/lib/utils'
import { Sparkles, Zap } from 'lucide-react'

/** Option order in the radiogroup; drives roving-tabindex + arrow nav. */
const MODES: readonly WizardMode[] = ['guided', 'quick']

/**
 * Arrow / Home / End -> next focused index, keyed by event.key. Table-driven
 * so the keydown handler stays a single lookup (complexity cap) and the
 * radiogroup honours the WAI-ARIA radio keyboard contract.
 */
const KEY_TO_NEXT_INDEX: Record<string, (current: number, count: number) => number> = {
  ArrowDown: (c, n) => (c + 1) % n,
  ArrowRight: (c, n) => (c + 1) % n,
  ArrowUp: (c, n) => (c - 1 + n) % n,
  ArrowLeft: (c, n) => (c - 1 + n) % n,
  Home: () => 0,
  End: (_c, n) => n - 1,
}

interface ModeOptionProps {
  icon: React.ElementType
  title: string
  description: string
  recommended?: boolean
  selected: boolean
  tabIndex: number
  onClick: () => void
  buttonRef: (el: HTMLButtonElement | null) => void
}

function ModeOption({
  icon: Icon,
  title,
  description,
  recommended,
  selected,
  tabIndex,
  onClick,
  buttonRef,
}: ModeOptionProps) {
  const titleId = useId()
  const descId = useId()
  return (
    <button
      ref={buttonRef}
      type="button"
      role="radio"
      aria-checked={selected}
      tabIndex={tabIndex}
      aria-labelledby={titleId}
      aria-describedby={descId}
      onClick={onClick}
      className={cn(
        'flex flex-col items-center gap-grid-gap rounded-lg border p-card text-center transition-colors',
        FOCUS_RING,
        selected
          ? 'border-accent bg-accent/5 shadow-[var(--so-shadow-accent-glow)]'
          : 'border-border bg-card hover:bg-card-hover',
      )}
    >
      <div className={cn(
        'flex size-14 items-center justify-center rounded-full',
        selected ? 'bg-accent/15 text-accent' : 'bg-surface text-muted-foreground',
      )}>
        <Icon className="size-7" />
      </div>

      <div className="space-y-1">
        <div className="flex items-center justify-center gap-2">
          <h3 id={titleId} className="text-base font-semibold text-foreground">{title}</h3>
          {recommended && (
            <span className="rounded-full bg-accent/10 px-2 py-0.5 text-compact font-medium text-accent">
              Recommended
            </span>
          )}
        </div>
        <p id={descId} className="text-sm text-muted-foreground">{description}</p>
      </div>
    </button>
  )
}

export function WizardModeStep() {
  const navigate = useNavigate()
  const wizardMode = useSetupWizardStore((s) => s.wizardMode)
  const setWizardMode = useSetupWizardStore((s) => s.setWizardMode)
  const markStepComplete = useSetupWizardStore((s) => s.markStepComplete)
  const optionsRef = useRef<(HTMLButtonElement | null)[]>([])

  const handleSelect = useCallback((mode: WizardMode) => {
    setWizardMode(mode)
    markStepComplete('mode')
    // Auto-advance to the next step after mode selection
    const order = useSetupWizardStore.getState().stepOrder
    const modeIdx = order.indexOf('mode')
    if (modeIdx >= 0 && modeIdx < order.length - 1) {
      void navigate(`/setup/${order[modeIdx + 1]}`)
    }
  }, [setWizardMode, markStepComplete, navigate])

  // Arrow keys move focus between the radio options (roving tabindex); the
  // native button Enter / Space still commits the focused option via onClick.
  const handleKeyDown = useCallback((e: React.KeyboardEvent<HTMLDivElement>) => {
    const compute = KEY_TO_NEXT_INDEX[e.key]
    if (!compute) return
    e.preventDefault()
    const count = optionsRef.current.length
    if (count === 0) return
    const focused = optionsRef.current.findIndex((el) => el === e.target)
    const base = focused === -1 ? 0 : focused
    optionsRef.current[compute(base, count)]?.focus()
  }, [])

  // Roving tabindex: only the selected option (or the first when none is
  // selected yet) is tabbable; arrows move focus among the rest.
  const selectedIndex = MODES.indexOf(wizardMode)
  const tabbableIndex = selectedIndex === -1 ? 0 : selectedIndex

  return (
    <div className="space-y-section-gap">
      <div className="space-y-2">
        <h2 className="text-lg font-semibold text-foreground">How would you like to set up?</h2>
        <p className="text-sm text-muted-foreground">
          Choose how much control you want over the initial configuration.
        </p>
      </div>

      <div
        role="radiogroup"
        aria-label="Setup mode"
        onKeyDown={handleKeyDown}
        className="grid grid-cols-2 gap-grid-gap max-sm:grid-cols-1"
      >
        <ModeOption
          icon={Sparkles}
          title="Guided Setup"
          description="Walk through each step to configure your organisation: pick a template, add providers, customise agents, and set your theme."
          recommended
          selected={wizardMode === 'guided'}
          tabIndex={tabbableIndex === 0 ? 0 : -1}
          buttonRef={(el) => {
            optionsRef.current[0] = el
          }}
          onClick={() => handleSelect('guided')}
        />
        <ModeOption
          icon={Zap}
          title="Quick Setup"
          description="Add a provider, set a company name, and get started. You can configure everything else later in Settings."
          selected={wizardMode === 'quick'}
          tabIndex={tabbableIndex === 1 ? 0 : -1}
          buttonRef={(el) => {
            optionsRef.current[1] = el
          }}
          onClick={() => handleSelect('quick')}
        />
      </div>
    </div>
  )
}
