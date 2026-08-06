/**
 * Subscribe to a task's live AG-UI progress stream and accumulate it into
 * renderable stages.
 *
 * Fills the "nothing is happening" gap between approving proposed work and the
 * completion review: while the backgrounded run executes, the operator sees it
 * start, make progress tool-call by tool-call, pause for any approval, and
 * finish or fail.
 *
 * Pure API consumer: no domain state is persisted client-side; the progress is
 * hydrated live from the replayable SSE stream and discarded on unmount.
 */

import { useEffect, useReducer } from 'react'

import {
  openTaskProgressStream,
  type TaskProgressStream,
} from '@/api/sse/task-progress-client'
import { AguiEventType, type AguiStreamEvent } from '@/api/sse/agui-types'
import type {
  ProgressStage,
  ProgressStageStatus,
} from '@/components/ui/progress-indicator'

export type TaskRunStatus = 'running' | 'finished' | 'error' | 'disconnected'

export interface TaskProgressState {
  status: TaskRunStatus
  stages: readonly ProgressStage[]
}

type Action =
  | { kind: 'reset' }
  | { kind: 'event'; event: AguiStreamEvent }
  | { kind: 'exhausted' }

/** Keep the stage list bounded so a long run cannot grow state without limit. */
const MAX_STAGES = 50

const INITIAL: TaskProgressState = { status: 'running', stages: [] }

function markRunningDone(stages: readonly ProgressStage[]): ProgressStage[] {
  return stages.map((s): ProgressStage =>
    s.status === 'running' ? { ...s, status: 'done' } : s,
  )
}

function append(
  stages: readonly ProgressStage[],
  stage: ProgressStage,
): ProgressStage[] {
  const next = [...markRunningDone(stages), stage]
  return next.length > MAX_STAGES ? next.slice(next.length - MAX_STAGES) : next
}

function markLast(
  stages: readonly ProgressStage[],
  status: ProgressStageStatus,
): ProgressStage[] {
  const lastIndex = stages.length - 1
  return stages.map((s, i): ProgressStage =>
    i === lastIndex ? { ...s, status } : s,
  )
}

function toolStage(event: AguiStreamEvent): ProgressStage {
  const turnValue = event.payload['turn']
  const turn = typeof turnValue === 'number' ? turnValue : null
  const toolsValue = event.payload['tools']
  const tools = Array.isArray(toolsValue) ? (toolsValue as string[]) : []
  return {
    id: event.id,
    label: turn !== null ? `Step ${turn}` : 'Working',
    status: 'running',
    ...(tools.length > 0 ? { description: tools.join(', ') } : {}),
  }
}

type Handler = (state: TaskProgressState, event: AguiStreamEvent) => TaskProgressState

/** Per-event-type reducer handlers; an unmapped type leaves state unchanged. */
const HANDLERS: Partial<Record<AguiEventType, Handler>> = {
  [AguiEventType.RunStarted]: () => ({ status: 'running', stages: [] }),
  [AguiEventType.ToolCallStart]: (state, event) => ({
    status: 'running',
    stages: append(state.stages, toolStage(event)),
  }),
  [AguiEventType.StepStarted]: (state, event) => ({
    status: 'running',
    stages: append(state.stages, toolStage(event)),
  }),
  [AguiEventType.ApprovalInterrupt]: (state, event) => ({
    status: 'running',
    stages: append(state.stages, {
      id: event.id,
      label: 'Paused for approval',
      status: 'running',
    }),
  }),
  [AguiEventType.ApprovalResumed]: (state) => ({
    status: 'running',
    stages: markLast(state.stages, 'done'),
  }),
  [AguiEventType.StepFinished]: (state) => ({
    status: state.status,
    stages: markLast(state.stages, 'done'),
  }),
  [AguiEventType.StepFailed]: (state) => ({
    status: state.status,
    stages: markLast(state.stages, 'failed'),
  }),
  [AguiEventType.RunFinished]: (state) => ({
    status: 'finished',
    stages: markRunningDone(state.stages),
  }),
  [AguiEventType.RunError]: (state) => ({
    status: 'error',
    stages: markLast(state.stages, 'failed'),
  }),
}

function reduce(state: TaskProgressState, action: Action): TaskProgressState {
  if (action.kind === 'reset') return INITIAL
  if (action.kind === 'exhausted') {
    // The stream gave up reconnecting. Only surface the lost connection while a
    // run is still in progress: once a terminal RunFinished/RunError frame has
    // set 'finished'/'error', the backend closing its side is expected, so
    // 'disconnected' must not overwrite the truthful terminal outcome.
    if (state.status !== 'running') return state
    return { status: 'disconnected', stages: markRunningDone(state.stages) }
  }
  const handler = HANDLERS[action.event.type]
  return handler ? handler(state, action.event) : state
}

/**
 * Subscribe to `taskId`'s progress stream while `taskId` is non-null, returning
 * the accumulated run status + stages. Returns `null` when there is no task to
 * watch. The `EventSource` is torn down on unmount and when `taskId` changes.
 */
export function useTaskProgress(taskId: string | null): TaskProgressState | null {
  const [state, dispatch] = useReducer(reduce, INITIAL)

  useEffect(() => {
    if (taskId === null) return
    dispatch({ kind: 'reset' })
    let stream: TaskProgressStream | null = openTaskProgressStream(taskId, {
      onEvent: (event) => {
        dispatch({ kind: 'event', event })
        // The per-task stream ends after a terminal frame; the backend closes
        // its side, so close ours too rather than letting EventSource burn its
        // whole reconnect budget on the now-dead stream after every run.
        if (
          event.type === AguiEventType.RunFinished ||
          event.type === AguiEventType.RunError
        ) {
          stream?.close()
          stream = null
        }
      },
      // The stream exhausted its reconnect budget and closed for good; surface
      // it so the panel stops showing an indefinite "Working" spinner.
      onExhausted: () => {
        dispatch({ kind: 'exhausted' })
      },
    })
    return () => {
      stream?.close()
      stream = null
    }
  }, [taskId])

  return taskId === null ? null : state
}
