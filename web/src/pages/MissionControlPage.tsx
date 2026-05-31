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
}: {
  tab: CockpitTab
  initialExecutionId: string | null
  initialProjectId: string | null
}) {
  if (tab === 'live') return <LiveCockpit />
  if (tab === 'steering') return <Steering initialProjectId={initialProjectId} />
  return <FlightRecorder initialExecutionId={initialExecutionId} />
}

export default function MissionControlPage() {
  const [tab, setTab] = useState<CockpitTab>('live')
  const [searchParams] = useSearchParams()
  const initialExecutionId = searchParams.get('executionId') ?? searchParams.get('taskId')
  const initialProjectId = searchParams.get('project')

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
        initialExecutionId={initialExecutionId}
        initialProjectId={initialProjectId}
      />
    </div>
  )
}
