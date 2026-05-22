import { useEffect, useState } from 'react'

import { Button } from '@/components/ui/button'
import { EmptyState } from '@/components/ui/empty-state'
import { ErrorBanner } from '@/components/ui/error-banner'
import { InputField } from '@/components/ui/input-field'
import { SegmentedControl } from '@/components/ui/segmented-control'
import { Timeline, type TimelineFrame } from '@/components/ui/timeline'
import { useMissionControlStore } from '@/stores/mission-control'
import { DEFAULT_CURRENCY } from '@/utils/currencies'
import { formatCurrency } from '@/utils/format'
import { Film } from 'lucide-react'

/** Base ms between frames at 1x; scaled by the selected playback speed. */
const BASE_PLAYBACK_INTERVAL_MS = 1_200

const SPEED_OPTIONS = [
  { value: '0.5', label: '0.5x' },
  { value: '1', label: '1x' },
  { value: '2', label: '2x' },
]

interface FlightRecorderProps {
  initialExecutionId?: string | null
}

export function FlightRecorder({ initialExecutionId }: FlightRecorderProps) {
  const [executionId, setExecutionId] = useState(initialExecutionId ?? '')
  const [index, setIndex] = useState(0)
  const [playing, setPlaying] = useState(false)
  const [speed, setSpeed] = useState('1')

  const frames = useMissionControlStore((s) => s.frames)
  const framesError = useMissionControlStore((s) => s.framesError)
  const fetchFrames = useMissionControlStore((s) => s.fetchFrames)

  // Frames arrive newest-first; show the timeline oldest-first for replay.
  const ordered = [...frames].reverse()
  const current = ordered[index]
  const timelineFrames: readonly TimelineFrame[] = ordered.map((f) => ({
    turnIndex: f.turn_index,
    status: f.status,
  }))

  const lastIndex = ordered.length - 1

  useEffect(() => {
    if (!playing || ordered.length === 0) return undefined
    const interval = BASE_PLAYBACK_INTERVAL_MS / Number(speed)
    const id = setInterval(() => {
      setIndex((prev) => {
        if (prev >= lastIndex) {
          setPlaying(false)
          return prev
        }
        return prev + 1
      })
    }, interval)
    return () => clearInterval(id)
  }, [playing, speed, lastIndex, ordered.length])

  function loadFrames(): void {
    if (executionId.trim() === '') return
    setIndex(0)
    setPlaying(false)
    void fetchFrames(executionId.trim())
  }

  return (
    <div className="space-y-section-gap">
      {framesError != null && (
        <ErrorBanner title="Failed to load frames" description={framesError} />
      )}

      <div className="flex flex-wrap items-end gap-2">
        <div className="min-w-64 flex-1">
          <InputField
            label="Execution ID"
            placeholder="Execution to replay"
            value={executionId}
            onChange={(e) => setExecutionId(e.target.value)}
          />
        </div>
        <Button variant="default" onClick={loadFrames} disabled={executionId.trim() === ''}>
          Load run
        </Button>
      </div>

      {ordered.length === 0 ? (
        <EmptyState
          icon={Film}
          title="No frames loaded"
          description="Enter an execution id and load a completed run to scrub through it turn-by-turn."
        />
      ) : (
        <>
          <Timeline
            frames={timelineFrames}
            currentIndex={index}
            onSeek={(i) => {
              setPlaying(false)
              setIndex(i)
            }}
          />

          <div className="flex flex-wrap items-center gap-2">
            <Button
              variant="outline"
              size="sm"
              disabled={index <= 0}
              onClick={() => {
                setPlaying(false)
                setIndex((p) => Math.max(0, p - 1))
              }}
            >
              Prev
            </Button>
            <Button
              variant="default"
              size="sm"
              disabled={ordered.length === 0}
              onClick={() => setPlaying((p) => !p)}
            >
              {playing ? 'Pause' : 'Play'}
            </Button>
            <Button
              variant="outline"
              size="sm"
              disabled={index >= lastIndex}
              onClick={() => {
                setPlaying(false)
                setIndex((p) => Math.min(lastIndex, p + 1))
              }}
            >
              Next
            </Button>
            <SegmentedControl
              label="Playback speed"
              size="sm"
              options={SPEED_OPTIONS}
              value={speed}
              onChange={setSpeed}
            />
            <span className="text-xs text-text-secondary">
              Step {index + 1} of {ordered.length}
            </span>
          </div>

          {current != null && <FrameDetail frame={current} />}
        </>
      )}
    </div>
  )
}

function FrameDetail({
  frame,
}: {
  frame: ReturnType<typeof useMissionControlStore.getState>['frames'][number]
}) {
  return (
    <div className="space-y-3 rounded-lg border border-border bg-card p-card">
      <div className="flex flex-wrap items-center justify-between gap-2 text-sm">
        <span className="font-medium text-foreground">
          Turn {frame.turn_index} . {frame.agent_id}
        </span>
        <span className="uppercase text-text-secondary">{frame.status}</span>
      </div>
      {frame.decision != null && (
        <p className="text-xs text-text-secondary">Decision: {frame.decision}</p>
      )}
      {frame.response_summary != null && (
        <pre className="whitespace-pre-wrap rounded bg-surface p-2 font-mono text-xs text-foreground">
          {frame.response_summary}
        </pre>
      )}
      {frame.tool_calls.length > 0 && (
        <p className="text-xs text-text-secondary">
          Tools: {frame.tool_calls.join(', ')}
        </p>
      )}
      <div className="grid grid-cols-3 gap-grid-gap text-xs">
        <div>
          <div className="text-text-secondary">Input tokens</div>
          <div className="font-mono text-foreground">{frame.input_tokens}</div>
        </div>
        <div>
          <div className="text-text-secondary">Output tokens</div>
          <div className="font-mono text-foreground">{frame.output_tokens}</div>
        </div>
        <div>
          <div className="text-text-secondary">Cost</div>
          <div className="font-mono text-foreground">
            {formatCurrency(frame.cost, DEFAULT_CURRENCY)}
          </div>
        </div>
      </div>
    </div>
  )
}
