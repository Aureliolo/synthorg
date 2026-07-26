import { useCallback, useState } from 'react'
import { decomposeTaskManually } from '@/api/endpoints/decomposition'
import type {
  DecompositionResult,
  ManualDecomposeRequest,
} from '@/api/types/decomposition'
import { createLogger } from '@/lib/logger'
import { useToastStore } from '@/stores/toast'
import { getCrudErrorTitle, getErrorMessage } from '@/utils/errors'
import { sanitizeForLog } from '@/utils/logging'

const log = createLogger('task-decompose')

const DEFAULT_MAX_SUBTASKS = 10
const DEFAULT_MAX_DEPTH = 3

/** A single editable subtask row in the manual-decomposition form. */
export interface SubtaskDraft {
  /** Stable React key, independent of array position. */
  key: string
  label: string
  title: string
  description: string
  /** Comma-separated dependency labels, parsed on submit. */
  dependencies: string
  /** One acceptance criterion per line, parsed on submit. */
  acceptanceCriteria: string
  /**
   * One expected deliverable per line, parsed on submit. Required: a work
   * unit that declares no deliverable disarms the zero-artifact guard, so
   * the backend rejects it.
   */
  expectedArtifacts: string
}

function emptyDraft(): SubtaskDraft {
  return {
    // Per-instance unique key (not a module counter, which would leak
    // across tests / Fast Refresh and could collide with surviving state).
    key: `draft-${crypto.randomUUID()}`,
    label: '',
    title: '',
    description: '',
    dependencies: '',
    acceptanceCriteria: '',
    expectedArtifacts: '',
  }
}

function parseDependencies(raw: string): readonly string[] {
  return raw
    .split(',')
    .map((token) => token.trim())
    .filter((token) => token.length > 0)
}

function parseLines(raw: string): readonly string[] {
  return raw
    .split('\n')
    .map((line) => line.trim())
    .filter((line) => line.length > 0)
}

function toRequest(drafts: readonly SubtaskDraft[]): ManualDecomposeRequest {
  return {
    subtasks: drafts.map((draft) => ({
      label: draft.label.trim(),
      title: draft.title.trim(),
      description: draft.description.trim(),
      dependencies: parseDependencies(draft.dependencies),
      acceptance_criteria: parseLines(draft.acceptanceCriteria),
      expected_artifacts: parseLines(draft.expectedArtifacts),
      estimated_complexity: 'medium',
      stakes: 'normal',
      required_skills: [],
      required_role: null,
    })),
    max_subtasks: DEFAULT_MAX_SUBTASKS,
    max_depth: DEFAULT_MAX_DEPTH,
    coordination_topology: 'auto',
  }
}

/** Whether a draft carries every field the backend requires of a subtask. */
function isComplete(draft: SubtaskDraft): boolean {
  return (
    draft.label.trim() !== '' &&
    draft.title.trim() !== '' &&
    draft.description.trim() !== '' &&
    parseLines(draft.acceptanceCriteria).length > 0 &&
    parseLines(draft.expectedArtifacts).length > 0
  )
}

/** Page-local controller for the manual task-decomposition form. */
export function useTaskDecomposeController(taskId: string | undefined) {
  const [drafts, setDrafts] = useState<readonly SubtaskDraft[]>(() => [emptyDraft()])
  const [result, setResult] = useState<DecompositionResult | null>(null)
  const [submitting, setSubmitting] = useState(false)

  const addDraft = useCallback(() => {
    setDrafts((prev) => [...prev, emptyDraft()])
  }, [])

  const removeDraft = useCallback((index: number) => {
    setDrafts((prev) => prev.filter((_, i) => i !== index))
  }, [])

  const updateDraft = useCallback(
    (index: number, patch: Partial<SubtaskDraft>) => {
      setDrafts((prev) =>
        prev.map((draft, i) => (i === index ? { ...draft, ...patch } : draft)),
      )
    },
    [],
  )

  const submit = useCallback(async () => {
    if (!taskId) return
    setSubmitting(true)
    try {
      const next = await decomposeTaskManually(taskId, toRequest(drafts))
      setResult(next)
      useToastStore.getState().add({
        variant: 'success',
        title: 'Decomposition complete',
        description: `${String(next.created_tasks.length)} subtasks planned.`,
      })
    } catch (err) {
      log.error('decomposeTaskManually failed', { error: sanitizeForLog(err) })
      useToastStore.getState().add({
        variant: 'error',
        title: getCrudErrorTitle(err, 'Decomposition failed').title,
        // The backend detail names which subtask was rejected and why, which
        // is exactly what the author needs to fix it.
        description: getErrorMessage(err),
      })
    } finally {
      setSubmitting(false)
    }
  }, [taskId, drafts])

  return {
    drafts,
    result,
    submitting,
    // Gate the submit rather than letting a guaranteed 422 round-trip: a work
    // subtask that declares no deliverable is rejected by the backend.
    canSubmit: drafts.length > 0 && drafts.every(isComplete),
    addDraft,
    removeDraft,
    updateDraft,
    submit,
  }
}
