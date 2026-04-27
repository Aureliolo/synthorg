import { useCallback, useEffect, useState } from 'react'
import { useParams } from 'react-router'
import {
  CheckCircle,
  MinusCircle,
  ShieldCheck,
  XCircle,
} from 'lucide-react'

import {
  decideReviewStage,
  getReviewPipeline,
  type PipelineResult,
  type ReviewStageResult,
  type StageVerdict,
} from '@/api/endpoints/clients'
import { Button } from '@/components/ui/button'
import { ErrorBanner } from '@/components/ui/error-banner'
import { ListHeader } from '@/components/ui/list-header'
import { SectionCard } from '@/components/ui/section-card'
import { SkeletonCard } from '@/components/ui/skeleton'
import { createLogger } from '@/lib/logger'

const log = createLogger('ReviewPipelinePage')

function VerdictIcon({
  verdict,
}: {
  verdict: ReviewStageResult['verdict']
}) {
  if (verdict === 'pass') {
    return <CheckCircle className="size-4 text-success" aria-label="Pass" />
  }
  if (verdict === 'fail') {
    return <XCircle className="size-4 text-danger" aria-label="Fail" />
  }
  return <MinusCircle className="size-4 text-warning" aria-label="Skip" />
}

/**
 * Review pipeline visualization for a single task.
 *
 * Resolves the task via the review controller and renders the
 * per-stage breakdown with verdict icons, reasons, and timing.
 */
export default function ReviewPipelinePage() {
  const { taskId } = useParams<{ taskId: string }>()
  const [pipeline, setPipeline] = useState<PipelineResult | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [actionError, setActionError] = useState<string | null>(null)
  const [decisionNotice, setDecisionNotice] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)

  const handleDecide = useCallback(
    async (stageName: string, verdict: StageVerdict) => {
      if (!taskId || submitting) return
      setSubmitting(true)
      setActionError(null)
      try {
        const result = await decideReviewStage(taskId, stageName, {
          verdict,
          reason: `Manual ${verdict} from dashboard`,
        })
        setDecisionNotice(
          `Recorded ${verdict.toUpperCase()} for ${stageName}`,
        )
        setPipeline(result.pipeline_result)
      } catch (err) {
        log.error('decide_stage_failed', err)
        setActionError('Failed to record stage decision.')
      } finally {
        setSubmitting(false)
      }
    },
    [taskId, submitting],
  )

  useEffect(() => {
    if (!taskId) {
      const timer = setTimeout(() => {
        setError('Missing task id in URL')
        setLoading(false)
      }, 0)
      return () => clearTimeout(timer)
    }
    let cancelled = false
    const load = async () => {
      setLoading(true)
      setPipeline(null)
      setError(null)
      setDecisionNotice(null)
      setActionError(null)
      try {
        const result = await getReviewPipeline(taskId)
        if (cancelled) return
        setPipeline(result)
      } catch (err) {
        if (cancelled) return
        log.error('get_review_pipeline_failed', err)
        setError('Failed to load review pipeline for this task.')
      } finally {
        if (!cancelled) setLoading(false)
      }
    }
    void load()
    return () => {
      cancelled = true
    }
  }, [taskId])

  if (loading) {
    return (
      <div className="space-y-section-gap">
        <ListHeader title="Review Pipeline" />
        <SkeletonCard />
      </div>
    )
  }

  if (error || !pipeline) {
    return (
      <div className="space-y-section-gap">
        <ListHeader title="Review Pipeline" />
        <ErrorBanner severity="error" title="Pipeline result not available" description={error ?? undefined} />
      </div>
    )
  }

  return (
    <div className="space-y-section-gap">
      <ListHeader title="Review Pipeline" description={`Task ${pipeline.task_id}`} />

      <SectionCard title="Overall verdict" icon={ShieldCheck}>
        <div className="flex items-center gap-2 text-sm">
          <VerdictIcon verdict={pipeline.final_verdict} />
          <span className="font-medium text-foreground">
            {pipeline.final_verdict.toUpperCase()}
          </span>
          <span className="text-text-secondary">
            · {pipeline.total_duration_ms} ms
          </span>
        </div>
      </SectionCard>

      <SectionCard title="Stage breakdown" icon={ShieldCheck}>
        {actionError && (
          <div className="mb-card">
            <ErrorBanner variant="section" severity="error" title="Stage action failed" description={actionError} />
          </div>
        )}
        {decisionNotice && (
          <div
            role="status"
            aria-live="polite"
            className="mb-card rounded-md border border-success/30 bg-success/5 p-card text-sm text-success"
          >
            {decisionNotice}
          </div>
        )}
        <ul className="space-y-3">
          {pipeline.stage_results.map((stage) => (
            <li
              key={stage.stage_name}
              className="rounded-md border border-border bg-card-hover p-card text-sm"
            >
              <div className="flex items-center gap-2">
                <VerdictIcon verdict={stage.verdict} />
                <span className="font-medium text-foreground">
                  {stage.stage_name}
                </span>
                <span className="ml-auto text-xs text-text-secondary">
                  {stage.duration_ms} ms
                </span>
              </div>
              {stage.reason && (
                <p className="mt-2 text-text-secondary">{stage.reason}</p>
              )}
              <div className="mt-2 flex flex-wrap gap-2">
                <Button
                  size="sm"
                  variant="outline"
                  disabled={submitting}
                  onClick={() => void handleDecide(stage.stage_name, 'pass')}
                >
                  Override pass
                </Button>
                <Button
                  size="sm"
                  variant="outline"
                  disabled={submitting}
                  onClick={() => void handleDecide(stage.stage_name, 'fail')}
                >
                  Override fail
                </Button>
              </div>
            </li>
          ))}
        </ul>
      </SectionCard>
    </div>
  )
}
