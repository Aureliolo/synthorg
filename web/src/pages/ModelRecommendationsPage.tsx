import { useEffect } from 'react'
import { RefreshCw, Sparkles } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { EmptyState } from '@/components/ui/empty-state'
import { ErrorBanner } from '@/components/ui/error-banner'
import { ListHeader } from '@/components/ui/list-header'
import { Skeleton } from '@/components/ui/skeleton'
import { StatPill } from '@/components/ui/stat-pill'
import { useAuthStore } from '@/stores/auth'
import { useRecommendationsStore } from '@/stores/recommendations'
import { ModelRecommendationCard } from './agents/ModelRecommendationCard'

function RefreshStatusBar() {
  const status = useRecommendationsStore((s) => s.status)
  const refreshing = useRecommendationsStore((s) => s.refreshing)
  const runRefresh = useRecommendationsStore((s) => s.runRefresh)
  return (
    <div className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-border bg-card p-card">
      <div className="flex flex-wrap items-center gap-2">
        {status != null && <StatPill label="MODE" value={status.mode} />}
        {status != null && (
          <StatPill
            label="AUTO-APPLY"
            value={status.auto_apply_within_family ? 'on (in-family)' : 'off'}
          />
        )}
        <span className="text-compact text-muted-foreground">
          Approvals reassign every pinned agent to the recommended model.
        </span>
      </div>
      <Button size="sm" variant="outline" onClick={() => void runRefresh()} disabled={refreshing} className="gap-1">
        <RefreshCw className="size-3.5" />
        {refreshing ? 'Refreshing...' : 'Run refresh now'}
      </Button>
    </div>
  )
}

function RecommendationsList() {
  const recommendations = useRecommendationsStore((s) => s.recommendations)
  const listLoading = useRecommendationsStore((s) => s.listLoading)
  const decidingId = useRecommendationsStore((s) => s.decidingId)
  const approve = useRecommendationsStore((s) => s.approve)
  const reject = useRecommendationsStore((s) => s.reject)
  const decidedBy = useAuthStore((s) => s.user?.username ?? 'operator')

  if (listLoading && recommendations.length === 0) {
    return <Skeleton className="h-40 w-full" />
  }
  if (recommendations.length === 0) {
    return (
      <EmptyState
        icon={Sparkles}
        title="No upgrade recommendations"
        description="When the refresh service finds a newer in-family model, it appears here for review."
      />
    )
  }
  return (
    <div className="flex flex-col gap-grid-gap">
      {recommendations.map((rec) => (
        <ModelRecommendationCard
          key={rec.id}
          recommendation={rec}
          deciding={decidingId === rec.id}
          onApprove={() => void approve(rec.id, decidedBy)}
          onReject={() => void reject(rec.id, decidedBy)}
        />
      ))}
    </div>
  )
}

export default function ModelRecommendationsPage() {
  const fetchRecommendations = useRecommendationsStore((s) => s.fetchRecommendations)
  const fetchStatus = useRecommendationsStore((s) => s.fetchStatus)
  const listError = useRecommendationsStore((s) => s.listError)

  useEffect(() => {
    void fetchRecommendations()
    void fetchStatus()
  }, [fetchRecommendations, fetchStatus])

  return (
    <div className="space-y-section-gap">
      <ListHeader title="Model Recommendations" />
      {listError != null && (
        <ErrorBanner
          severity="error"
          title="Could not load recommendations"
          description={listError}
          onRetry={() => void fetchRecommendations()}
        />
      )}
      <RefreshStatusBar />
      <RecommendationsList />
    </div>
  )
}
