import { useCallback } from 'react'
import { useNavigate } from 'react-router'
import { useSetupWizardStore } from '@/stores/setup-wizard'
import type { WizardMode } from '@/stores/setup-wizard'
import { cn } from '@/lib/utils'
import { Sparkles, Zap } from 'lucide-react'

interface ModeOptionProps {
  icon: React.ElementType
  title: string
  description: string
  recommended?: boolean
  selected: boolean
  onClick: () => void
}

const SELECTED_SHADOW =
  'shadow-[0_0_12px_color-mix(' +
  'in_srgb,var(--so-accent)_15%,transparent)]'

function ModeOption({ icon: Icon, title, description, recommended, selected, onClick }: ModeOptionProps) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={selected}
      aria-label={`Select ${title}`}
      className={cn(
        'flex flex-col items-center gap-grid-gap rounded-lg border p-card text-center transition-colors',
        selected
          ? `border-accent bg-accent/5 ${SELECTED_SHADOW}`
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
          <h3 className="text-base font-semibold text-foreground">{title}</h3>
          {recommended && (
            <span className="rounded-full bg-accent/10 px-2 py-0.5 text-compact font-medium text-accent">
              Recommended
            </span>
          )}
        </div>
        <p className="text-sm text-muted-foreground">{description}</p>
      </div>
    </button>
  )
}

export function WizardModeStep() {
  const navigate = useNavigate()
  const wizardMode = useSetupWizardStore((s) => s.wizardMode)
  const setWizardMode = useSetupWizardStore((s) => s.setWizardMode)
  const markStepComplete = useSetupWizardStore((s) => s.markStepComplete)

  const handleSelect = useCallback((mode: WizardMode) => {
    setWizardMode(mode)
    markStepComplete('mode')
    // Auto-advance to the next step after mode selection
    const order = useSetupWizardStore.getState().stepOrder
    const modeIdx = order.indexOf('mode')
    if (modeIdx >= 0 && modeIdx < order.length - 1) {
      navigate(`/setup/${order[modeIdx + 1]}`)
    }
  }, [setWizardMode, markStepComplete, navigate])

  return (
    <div className="space-y-section-gap">
      <div className="space-y-2 text-center">
        <h2 className="text-lg font-semibold text-foreground">How would you like to set up?</h2>
        <p className="text-sm text-muted-foreground">
          Choose how much control you want over the initial configuration.
        </p>
      </div>

      <div className="grid grid-cols-2 gap-grid-gap max-[639px]:grid-cols-1">
        <ModeOption
          icon={Sparkles}
          title="Guided Setup"
          description="Walk through each step to configure your organisation: pick a template, add providers, customise agents, and set your theme."
          recommended
          selected={wizardMode === 'guided'}
          onClick={() => handleSelect('guided')}
        />
        <ModeOption
          icon={Zap}
          title="Quick Setup"
          description="Add a provider, set a company name, and get started. You can configure everything else later in Settings."
          selected={wizardMode === 'quick'}
          onClick={() => handleSelect('quick')}
        />
      </div>
    </div>
  )
}
