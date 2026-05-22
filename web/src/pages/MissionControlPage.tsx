import { useState } from 'react'
import { useSearchParams } from 'react-router'

import { SegmentedControl } from '@/components/ui/segmented-control'
import { LiveCockpit } from '@/pages/mission-control/LiveCockpit'
import { FlightRecorder } from '@/pages/mission-control/FlightRecorder'

type CockpitTab = 'live' | 'recorder'

const TAB_OPTIONS = [
  { value: 'live' as const, label: 'Live' },
  { value: 'recorder' as const, label: 'Flight Recorder' },
]

export default function MissionControlPage() {
  const [tab, setTab] = useState<CockpitTab>('live')
  const [searchParams] = useSearchParams()
  const initialExecutionId = searchParams.get('executionId') ?? searchParams.get('taskId')

  return (
    <div className="space-y-section-gap">
      <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h1 className="text-xl font-semibold text-foreground">Mission Control</h1>
          <p className="text-sm text-text-secondary">
            Watch the company work, intervene on a stuck or runaway agent, and
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

      {tab === 'live' ? (
        <LiveCockpit />
      ) : (
        <FlightRecorder initialExecutionId={initialExecutionId} />
      )}
    </div>
  )
}
