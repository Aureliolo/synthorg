import { useState } from 'react'
import { useSearchParams } from 'react-router'

import { SegmentedControl } from '@/components/ui/segmented-control'
import { LiveCockpit } from '@/pages/mission-control/LiveCockpit'
import { FlightRecorder } from '@/pages/mission-control/FlightRecorder'
import { Steering } from '@/pages/mission-control/Steering'

type CockpitTab = 'live' | 'steering' | 'recorder'

const TAB_OPTIONS = [
  { value: 'live', label: 'Live' },
  { value: 'steering', label: 'Steering' },
  { value: 'recorder', label: 'Flight Recorder' },
] as const satisfies readonly { value: CockpitTab; label: string }[]

function CockpitTabPanel({
  tab,
  initialExecutionId,
  initialProjectId,
  onReplay,
}: {
  tab: CockpitTab
  initialExecutionId: string | null
  initialProjectId: string | null
  onReplay: (executionId: string) => void
}) {
  if (tab === 'live') return <LiveCockpit onReplay={onReplay} />
  if (tab === 'steering') return <Steering initialProjectId={initialProjectId} />
  // Key by the execution id so a deep-link from a live agent row remounts
  // the recorder against the new run (and its mount effect auto-loads it)
  // rather than leaving the previously-typed run on screen.
  return (
    <FlightRecorder
      key={initialExecutionId ?? 'manual'}
      initialExecutionId={initialExecutionId}
    />
  )
}

export default function MissionControlPage() {
  const [searchParams] = useSearchParams()
  const urlExecutionId = searchParams.get('executionId') ?? searchParams.get('taskId')
  const initialProjectId = searchParams.get('project')
  // A deep link carrying an execution id lands on the recorder tab (the same
  // surface handleReplay switches to), not the default live tab.
  const [tab, setTab] = useState<CockpitTab>(urlExecutionId ? 'recorder' : 'live')
  const [replayExecutionId, setReplayExecutionId] = useState<string | null>(
    urlExecutionId,
  )
  // useState seeds tab/replay only on first render. When the URL execution id
  // changes while the page stays mounted (e.g. a fresh deep link), adjust state
  // during render -- React's sanctioned alternative to a sync-in-effect: it
  // re-renders before committing, with no extra paint, and the recorder follows
  // the URL instead of freezing on the first value.
  const [syncedExecutionId, setSyncedExecutionId] = useState(urlExecutionId)
  if (urlExecutionId !== syncedExecutionId) {
    setSyncedExecutionId(urlExecutionId)
    setReplayExecutionId(urlExecutionId)
    setTab(urlExecutionId ? 'recorder' : 'live')
  }

  function handleReplay(executionId: string): void {
    setReplayExecutionId(executionId)
    setTab('recorder')
  }

  return (
    <div className="space-y-section-gap">
      <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h1 className="text-xl font-semibold text-foreground">Mission Control</h1>
          <p className="text-sm text-text-secondary">
            Watch the company work, steer in-flight agents on a project, and
            replay a completed run step-by-step.
          </p>
        </div>
        <SegmentedControl<CockpitTab>
          label="Cockpit view"
          options={TAB_OPTIONS}
          value={tab}
          onChange={setTab}
        />
      </div>

      <CockpitTabPanel
        tab={tab}
        initialExecutionId={replayExecutionId}
        initialProjectId={initialProjectId}
        onReplay={handleReplay}
      />
    </div>
  )
}
