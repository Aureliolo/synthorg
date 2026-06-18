import { useCallback, useState } from 'react'
import { decomposeTaskManually } from '@/api/endpoints/decomposition'
import type { DecompositionResult, ManualDecomposeRequest } from '@/api/types'
import { createLogger } from '@/lib/logger'
import { useToastStore } from '@/stores/toast'
import { getCrudErrorTitle } from '@/utils/errors'
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
}

let _draftCounter = 0

function emptyDraft(): SubtaskDraft {
  _draftCounter += 1
  return {
    key: `draft-${String(_draftCounter)}`,
    label: '',
    title: '',
    description: '',
    dependencies: '',
  }
}

function parseDependencies(raw: string): readonly string[] {
  return raw
    .split(',')
    .map((token) => token.trim())
    .filter((token) => token.length > 0)
}

function toRequest(
  drafts: readonly SubtaskDraft[],
  maxSubtasks: number,
  maxDepth: number,
): ManualDecomposeRequest {
  return {
    subtasks: drafts.map((draft) => ({
      label: draft.label.trim(),
      title: draft.title.trim(),
      description: draft.description.trim(),
      dependencies: parseDependencies(draft.dependencies),
      estimated_complexity: 'medium',
      stakes: 'normal',
      required_skills: [],
      required_role: null,
    })),
    max_subtasks: maxSubtasks,
    max_depth: maxDepth,
    coordination_topology: 'auto',
  }
}

/** Page-local controller for the manual task-decomposition form. */
export function useTaskDecomposeController(taskId: string | undefined) {
  const [drafts, setDrafts] = useState<readonly SubtaskDraft[]>(() => [emptyDraft()])
  const [maxSubtasks, setMaxSubtasks] = useState(DEFAULT_MAX_SUBTASKS)
  const [maxDepth, setMaxDepth] = useState(DEFAULT_MAX_DEPTH)
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
      const next = await decomposeTaskManually(
        taskId,
        toRequest(drafts, maxSubtasks, maxDepth),
      )
      setResult(next)
      useToastStore.getState().add({
        variant: 'success',
        title: 'Decomposition complete',
        description: `${String(next.created_tasks.length)} subtasks planned.`,
      })
    } catch (err) {
      log.error('decomposeTaskManually failed', { error: sanitizeForLog(String(err)) })
      useToastStore.getState().add({
        variant: 'error',
        title: getCrudErrorTitle(err, 'Decomposition failed').title,
        description: 'Could not decompose the task. Check the plan and try again.',
      })
    } finally {
      setSubmitting(false)
    }
  }, [taskId, drafts, maxSubtasks, maxDepth])

  return {
    drafts,
    maxSubtasks,
    maxDepth,
    result,
    submitting,
    setMaxSubtasks,
    setMaxDepth,
    addDraft,
    removeDraft,
    updateDraft,
    submit,
  }
}
