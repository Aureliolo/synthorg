import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useSearchParams } from 'react-router'
import { Video } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { EmptyState } from '@/components/ui/empty-state'
import { ErrorBanner } from '@/components/ui/error-banner'
import { ErrorBoundary } from '@/components/ui/error-boundary'
import { ListHeader } from '@/components/ui/list-header'
import { StaggerGroup, StaggerItem } from '@/components/ui/stagger-group'
import { useEmptyStateProps } from '@/hooks/use-empty-state-props'
import { useMeetingsData } from '@/hooks/useMeetingsData'
import { filterMeetings, type MeetingPageFilters } from '@/utils/meetings'
import { MEETING_STATUS_VALUES, type MeetingStatus } from '@/api/types/meetings'
import { MeetingMetricCards } from './meetings/MeetingMetricCards'
import { MeetingFilterBar } from './meetings/MeetingFilterBar'
import { MeetingTimeline } from './meetings/MeetingTimeline'
import { MeetingCard } from './meetings/MeetingCard'
import { TriggerMeetingDialog } from './meetings/TriggerMeetingDialog'
import { MeetingsSkeleton } from './meetings/MeetingsSkeleton'

const VALID_STATUSES: ReadonlySet<string> = new Set(MEETING_STATUS_VALUES)

export default function MeetingsPage() {
  const {
    meetings,
    loading,
    error,
    triggering,
    wsConnected,
    wsSetupError,
    triggerMeeting,
  } = useMeetingsData()

  const [searchParams, setSearchParams] = useSearchParams()
  const [triggerOpen, setTriggerOpen] = useState(false)
  const wasConnectedRef = useRef(false)
  useEffect(() => {
    if (wsConnected) wasConnectedRef.current = true
  }, [wsConnected])

  // URL-synced filters
  const filters: MeetingPageFilters = useMemo(() => {
    const rawStatus = searchParams.get('status')
    return {
      status: rawStatus && VALID_STATUSES.has(rawStatus) ? rawStatus as MeetingStatus : undefined,
      meetingType: searchParams.get('type') ?? undefined,
    }
  }, [searchParams])

  const handleFiltersChange = useCallback((newFilters: MeetingPageFilters) => {
    setSearchParams((prev) => {
      const next = new URLSearchParams(prev)
      next.delete('status')
      next.delete('type')
      if (newFilters.status) next.set('status', newFilters.status)
      if (newFilters.meetingType) next.set('type', newFilters.meetingType)
      return next
    })
  }, [setSearchParams])

  const handleTrigger = useCallback(async (eventName: string): Promise<boolean> => {
    // Sentinel-return contract: the store owns success/error toasts.
    // Returning ``false`` on failure tells ConfirmDialog to keep the
    // dialog open so the user can retry without losing their input;
    // any other return value (true here) closes it.
    const triggered = await triggerMeeting({ event_name: eventName })
    if (triggered.length === 0) return false
    setTriggerOpen(false)
    return true
  }, [triggerMeeting])

  // Derived data
  const filtered = useMemo(() => filterMeetings(meetings, filters), [meetings, filters])
  const meetingTypes = useMemo(
    () => [...new Set(meetings.map((m) => m.meeting_type_name))].sort(),
    [meetings],
  )

  const hasFilters = !!(filters.status || filters.meetingType)

  // Centralise the "no data ever" vs "no data after filter" branching
  // via useEmptyStateProps so the discriminator matches the dashboard's
  // canonical empty-state derivation pattern (see web/CLAUDE.md).
  const emptyStateProps = useEmptyStateProps({
    filteredCount: filtered.length,
    totalCount: meetings.length,
    filterActive: hasFilters,
    icon: Video,
    empty: {
      title: 'No meetings yet',
      description: "When meetings are scheduled or triggered, they'll appear here.",
      action: { label: 'Trigger Meeting', onClick: () => setTriggerOpen(true) },
    },
    filtered: {
      title: 'No matching meetings',
      description: 'Try adjusting your filters.',
      action: { label: 'Clear filters', onClick: () => handleFiltersChange({}) },
    },
  })

  // Loading state
  if (loading && meetings.length === 0) {
    return <MeetingsSkeleton />
  }

  return (
    <div className="space-y-section-gap">
      <ListHeader
        title="Meetings"
        count={filtered.length}
        primaryAction={<Button onClick={() => setTriggerOpen(true)}>Trigger meeting</Button>}
      />

      {error && (
        <ErrorBanner severity="error" title="Could not load meetings" description={error} />
      )}

      {(wsSetupError || (wasConnectedRef.current && !wsConnected)) && !loading && (
        <ErrorBanner
          variant="offline"
          title="Real-time updates disconnected"
          description={wsSetupError ?? 'Data may be stale until the connection recovers.'}
        />
      )}

      <ErrorBoundary level="section">
        <MeetingMetricCards meetings={filtered} />
      </ErrorBoundary>

      <MeetingFilterBar
        filters={filters}
        onFiltersChange={handleFiltersChange}
        meetingTypes={meetingTypes}
      />

      <ErrorBoundary level="section">
        <MeetingTimeline meetings={filtered} />
      </ErrorBoundary>

      {filtered.length > 0 && (
        <ErrorBoundary level="section">
          <StaggerGroup className="grid grid-cols-1 gap-grid-gap md:grid-cols-2 lg:grid-cols-3">
            {filtered.map((meeting) => (
              <StaggerItem key={meeting.meeting_id}>
                <MeetingCard meeting={meeting} />
              </StaggerItem>
            ))}
          </StaggerGroup>
        </ErrorBoundary>
      )}

      {emptyStateProps && !error && <EmptyState {...emptyStateProps} />}

      <TriggerMeetingDialog
        open={triggerOpen}
        onOpenChange={setTriggerOpen}
        onConfirm={handleTrigger}
        loading={triggering}
      />
    </div>
  )
}
