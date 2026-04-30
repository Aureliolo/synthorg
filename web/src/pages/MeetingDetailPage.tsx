import { useCallback, useEffect, useRef } from 'react'
import { useNavigate, useParams } from 'react-router'
import { Video } from 'lucide-react'
import { Breadcrumbs } from '@/components/ui/breadcrumbs'
import { DetailNavBar } from '@/components/ui/detail-nav-bar'
import { ErrorBanner } from '@/components/ui/error-banner'
import { ErrorBoundary } from '@/components/ui/error-boundary'
import { EmptyState } from '@/components/ui/empty-state'
import { SectionCard } from '@/components/ui/section-card'
import { useMeetingDetailData } from '@/hooks/useMeetingDetailData'
import { useMeetingsStore } from '@/stores/meetings'
import {
  useDetailNavigation,
  useDetailNavigationCallbacks,
} from '@/hooks/use-detail-navigation'
import { ROUTES } from '@/router/routes'
import { MeetingDetailHeader } from './meetings/MeetingDetailHeader'
import { MeetingAgendaSection } from './meetings/MeetingAgendaSection'
import { MeetingTokenBreakdown } from './meetings/MeetingTokenBreakdown'
import { MeetingContributions } from './meetings/MeetingContributions'
import { MeetingDecisions } from './meetings/MeetingDecisions'
import { MeetingActionItems } from './meetings/MeetingActionItems'
import { MeetingDetailSkeleton } from './meetings/MeetingDetailSkeleton'

export default function MeetingDetailPage() {
  const { meetingId } = useParams<{ meetingId: string }>()
  const navigate = useNavigate()
  const {
    meeting,
    loading,
    error,
    wsConnected,
    wsSetupError,
  } = useMeetingDetailData(meetingId ?? '')

  const wasConnectedRef = useRef(false)
  useEffect(() => {
    if (wsConnected) wasConnectedRef.current = true
  }, [wsConnected])

  // Walk the parent meeting list (uses ``meeting_id`` as the URL key).
  const allMeetings = useMeetingsStore((s) => s.meetings)
  const routeForMeeting = useCallback(
    (item: { id: string }) =>
      ROUTES.MEETING_DETAIL.replace(':meetingId', encodeURIComponent(item.id)),
    [],
  )
  const navItems = allMeetings.map((m) => ({ id: m.meeting_id }))
  const nav = useDetailNavigation({
    items: navItems,
    currentId: meetingId,
    routeFor: routeForMeeting,
  })
  const { goPrev, goNext } = useDetailNavigationCallbacks(nav)

  // Missing meetingId
  if (!meetingId) {
    return (
      <EmptyState
        icon={Video}
        title="Meeting not found"
        description="No meeting ID was provided."
        action={{ label: 'Back to meetings', onClick: () => navigate(ROUTES.MEETINGS) }}
      />
    )
  }

  // Loading state
  if (loading && !meeting) {
    return <MeetingDetailSkeleton />
  }

  // Error state with no data
  if (error && !meeting) {
    return (
      <div className="space-y-section-gap">
        <Breadcrumbs items={[{ label: 'Meetings', to: ROUTES.MEETINGS }, { label: 'Unknown meeting' }]} />
        <ErrorBanner
          severity="error"
          title="Could not load meeting"
          description={error}
          onRetry={() => useMeetingsStore.getState().fetchMeeting(meetingId)}
        />
      </div>
    )
  }

  if (!meeting) return <MeetingDetailSkeleton />

  return (
    <div className="space-y-section-gap">
      <div className="flex flex-wrap items-center gap-3">
        <Breadcrumbs items={[{ label: 'Meetings', to: ROUTES.MEETINGS }, { label: meeting.meeting_type_name || meeting.meeting_id }]} />
        <DetailNavBar
          canPrev={nav.canPrev}
          canNext={nav.canNext}
          onPrev={goPrev}
          onNext={goNext}
          position={nav.position}
        />
      </div>

      {error && (
        <ErrorBanner severity="error" title="Could not load meeting" description={error} />
      )}

      {(wsSetupError || (wasConnectedRef.current && !wsConnected)) && !loading && (
        <ErrorBanner
          variant="offline"
          title="Real-time updates disconnected"
          description={wsSetupError ?? 'Data may be stale until the connection recovers.'}
        />
      )}

      <ErrorBoundary level="section">
        <MeetingDetailHeader meeting={meeting} />
      </ErrorBoundary>

      {meeting.minutes && (
        <>
          <ErrorBoundary level="section">
            <MeetingAgendaSection agenda={meeting.minutes.agenda} />
          </ErrorBoundary>

          <ErrorBoundary level="section">
            <MeetingTokenBreakdown meeting={meeting} />
          </ErrorBoundary>

          {meeting.minutes.contributions.length > 0 && (
            <ErrorBoundary level="section">
              <MeetingContributions contributions={meeting.minutes.contributions} />
            </ErrorBoundary>
          )}

          <div className="grid grid-cols-1 gap-grid-gap lg:grid-cols-2">
            <ErrorBoundary level="section">
              <MeetingDecisions decisions={meeting.minutes.decisions} />
            </ErrorBoundary>
            <ErrorBoundary level="section">
              <MeetingActionItems actionItems={meeting.minutes.action_items} />
            </ErrorBoundary>
          </div>

          {meeting.minutes.summary && (
            <SectionCard title="Summary">
              <p className="text-sm text-foreground leading-relaxed">
                {meeting.minutes.summary}
              </p>
            </SectionCard>
          )}
        </>
      )}

      {!meeting.minutes && meeting.status === 'in_progress' && (
        <SectionCard title="Meeting In Progress">
          <p className="text-sm text-muted-foreground">
            This meeting is currently in progress. Minutes will be available once the meeting completes.
          </p>
        </SectionCard>
      )}

      {!meeting.minutes && meeting.status === 'scheduled' && (
        <SectionCard title="Meeting Scheduled">
          <p className="text-sm text-muted-foreground">
            This meeting has not started yet. Minutes will be available once the meeting runs.
          </p>
        </SectionCard>
      )}

      {meeting.error_message && (
        <SectionCard title="Error">
          <p className="text-sm text-danger">{meeting.error_message}</p>
        </SectionCard>
      )}
    </div>
  )
}
