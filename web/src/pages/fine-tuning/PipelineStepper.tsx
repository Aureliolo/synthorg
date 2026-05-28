import { cn } from '@/lib/utils'

import type { FineTuneStage } from '@/api/endpoints/fine-tuning'

const STAGES: { key: FineTuneStage; label: string }[] = [
  { key: 'generating_data', label: 'Data Generation' },
  { key: 'mining_negatives', label: 'Hard Negatives' },
  { key: 'training', label: 'Training' },
  { key: 'evaluating', label: 'Evaluation' },
  { key: 'deploying', label: 'Deploy' },
]

const STAGE_ORDER = STAGES.map((s) => s.key)

type StepStatus = 'complete' | 'current' | 'upcoming'

const STEP_CIRCLE_CLASSES: Readonly<Record<StepStatus, string>> = {
  complete: 'bg-accent text-accent-foreground',
  current: 'bg-accent/20 text-accent ring-2 ring-accent',
  upcoming: 'bg-muted text-muted-foreground',
}

const STEP_LABEL_CLASSES: Readonly<Record<StepStatus, string>> = {
  complete: 'text-muted-foreground',
  current: 'font-medium text-foreground',
  upcoming: 'text-muted-foreground',
}

interface StepItemProps {
  stage: { key: FineTuneStage; label: string }
  index: number
  currentIdx: number
  currentStage: FineTuneStage
}

function StepItem({ stage: s, index: i, currentIdx, currentStage }: StepItemProps) {
  const status: StepStatus =
    i < currentIdx ? 'complete' : s.key === currentStage ? 'current' : 'upcoming'
  const connectorComplete = i <= currentIdx

  return (
    <div className="flex items-center gap-2">
      {i > 0 && (
        <div className={cn('h-0.5 w-8', connectorComplete ? 'bg-accent' : 'bg-border')} />
      )}
      <div className="flex flex-col items-center gap-1">
        <div
          className={cn(
            'flex h-8 w-8 items-center justify-center rounded-full text-xs font-medium',
            STEP_CIRCLE_CLASSES[status],
          )}
        >
          {status === 'complete' ? '✓' : i + 1}
        </div>
        <span className={cn('text-xs', STEP_LABEL_CLASSES[status])}>{s.label}</span>
      </div>
    </div>
  )
}

interface PipelineStepperProps {
  stage: FineTuneStage
}

export function PipelineStepper({ stage }: PipelineStepperProps) {
  const currentIdx = STAGE_ORDER.indexOf(stage)

  return (
    <div className="flex items-center gap-2">
      {STAGES.map((s, i) => (
        <StepItem
          key={s.key}
          stage={s}
          index={i}
          currentIdx={currentIdx}
          currentStage={stage}
        />
      ))}
    </div>
  )
}
