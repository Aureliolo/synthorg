import type { FineTuneStage } from '@/api/endpoints/fine-tuning'
import { ProgressIndicator } from '@/components/ui/progress-indicator'

/**
 * Warning threshold (seconds) for the indeterminate fine-tuning bar.
 *
 * Indeterminate stages (data generation, negative mining, evaluation,
 * deployment) typically finish in <10 minutes on a healthy GPU host;
 * once 15 minutes elapse without progress moving to a determinate
 * stage the operator should investigate before the job times out
 * server-side.
 */
const FINE_TUNING_INDETERMINATE_WARNING_SECONDS = 900

interface PipelineProgressBarProps {
  stage: FineTuneStage
  progress: number | null
  /**
   * ISO-8601 timestamp from the current run's ``started_at``. Used to
   * render a live elapsed counter on the indeterminate variant so
   * operators can see how long the current stage has been running.
   */
  startedAt?: string | null
}

export function PipelineProgressBar({ stage, progress, startedAt }: PipelineProgressBarProps) {
  const label = `Stage: ${formatStage(stage)}`
  if (progress == null) {
    return (
      <div className="pt-4">
        <ProgressIndicator
          variant="indeterminate"
          label={label}
          startedAt={startedAt ?? null}
          warningAfterSeconds={FINE_TUNING_INDETERMINATE_WARNING_SECONDS}
        />
      </div>
    )
  }
  const value = Math.round(progress * 100)
  return (
    <div className="pt-4">
      <ProgressIndicator
        variant="determinate"
        value={value}
        label={label}
      />
    </div>
  )
}

function formatStage(stage: FineTuneStage): string {
  const labels: Record<string, string> = {
    generating_data: 'Generating Training Data',
    mining_negatives: 'Mining Hard Negatives',
    training: 'Contrastive Fine-Tuning',
    evaluating: 'Evaluating Checkpoint',
    deploying: 'Deploying Checkpoint',
  }
  return labels[stage] ?? stage
}
