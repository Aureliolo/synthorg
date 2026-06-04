import type { Edge, Node } from '@xyflow/react'
import type { StoreApi } from 'zustand'
import {
  createWorkflow,
  getWorkflow,
  updateWorkflow,
} from '@/api/endpoints/workflows'
import { ErrorCode } from '@/api/types/errors'
import type { WorkflowDefinition } from '@/api/types/workflows'
import { createLogger } from '@/lib/logger'
import { useToastStore } from '@/stores/toast'
import { getCrudErrorTitle, getErrorCode, getErrorMessage } from '@/utils/errors'
import { sanitizeForLog } from '@/utils/logging'
import { isObject, isString } from '@/utils/type-guards'
import type {
  PersistenceSlice,
  SliceCreator,
  WorkflowEditorState,
} from './types'
import { generateNodeId, parseDefinition, regenerateYaml } from './yaml'

type WorkflowEditorSet = StoreApi<WorkflowEditorState>['setState']
type WorkflowEditorGet = StoreApi<WorkflowEditorState>['getState']

const log = createLogger('workflow-editor:persistence')

/**
 * Read a string field off a ReactFlow ``data`` payload without unsafe
 * ``as`` casts. Returns ``undefined`` when ``data`` is missing or the
 * field is not a string. Used at the persistence boundary where
 * ReactFlow types ``data`` as ``Record<string, unknown> | undefined``.
 */
function readEdgeField(data: unknown, key: string): string | undefined {
  if (!isObject(data)) return undefined
  const value = data[key]
  return isString(value) ? value : undefined
}

function readNodeLabel(data: unknown): string | undefined {
  return readEdgeField(data, 'label')
}

function readNodeConfig(data: unknown): Record<string, unknown> | undefined {
  if (!isObject(data)) return undefined
  const value = data.config
  return isObject(value) ? value : undefined
}

function validateGraphForSave(
  nodes: readonly Node[],
  edges: readonly Edge[],
): string | null {
  const badNodes = nodes.filter((n) => !n.type)
  const badEdges = edges.filter((e) => !readEdgeField(e.data, 'edgeType'))
  if (badNodes.length === 0 && badEdges.length === 0) return null
  const parts: string[] = []
  if (badNodes.length > 0) {
    parts.push(
      `nodes missing type: ${badNodes.map((n) => n.id).join(', ')}`,
    )
  }
  if (badEdges.length > 0) {
    parts.push(
      `edges missing type: ${badEdges.map((e) => e.id).join(', ')}`,
    )
  }
  return `Cannot save: ${parts.join('; ')}. Remove and re-add the affected items.`
}

function failSaveWithMessage(
  set: WorkflowEditorSet,
  message: string,
  logContext: Record<string, unknown>,
): false {
  log.warn('Failed to save workflow definition', sanitizeForLog(logContext))
  set({ error: message })
  useToastStore.getState().add({
    variant: 'error',
    title: 'Failed to save workflow',
    description: message,
  })
  return false
}

function buildUpdatePayload(
  definition: WorkflowDefinition,
  nodes: readonly Node[],
  edges: readonly Edge[],
) {
  return {
    workflow_type: (definition.workflow_type ?? 'sequential_pipeline'),
    nodes: nodes.map((n) => ({
      id: n.id,
      type: n.type!,
      label: readNodeLabel(n.data) ?? n.id,
      position_x: n.position.x,
      position_y: n.position.y,
      config: readNodeConfig(n.data) ?? {},
    })) as readonly Record<string, unknown>[],
    edges: edges.map((e) => ({
      id: e.id,
      source_node_id: e.source,
      target_node_id: e.target,
      type: readEdgeField(e.data, 'edgeType') ?? 'sequential',
      label: isString(e.label) ? e.label : null,
    })) as readonly Record<string, unknown>[],
    expected_revision: definition.revision,
  }
}

async function handleVersionConflict(
  set: WorkflowEditorSet,
  get: WorkflowEditorGet,
  definition: WorkflowDefinition,
  err: unknown,
): Promise<false> {
  log.warn('Version conflict saving workflow, reloading', sanitizeForLog(err))
  set({
    saving: false,
    error: 'Version conflict, another save occurred. Reloading...',
  })
  await get().loadDefinition(definition.id)
  // ``loadDefinition`` swallows its own errors and writes them to
  // ``state.error``. Promote the toast only after the reload settles
  // so the user is not told "reloaded" when the reload itself failed.
  if (get().error === null) {
    useToastStore.getState().add({
      variant: 'warning',
      title: 'Version conflict',
      description: 'Another save occurred. Reloaded the latest version.',
    })
  } else {
    useToastStore.getState().add({
      variant: 'error',
      title: 'Reload failed after version conflict',
      description:
        'Could not reload the workflow after a concurrent save. Refresh the page to retry.',
    })
  }
  return false
}

function handleSaveError(
  set: WorkflowEditorSet,
  err: unknown,
): false {
  log.warn('Failed to save workflow definition', sanitizeForLog(err))
  useToastStore.getState().add({
    variant: 'error',
    ...getCrudErrorTitle(err, 'Failed to save workflow'),
    description: getErrorMessage(err),
  })
  set({ saving: false, error: getErrorMessage(err) })
  return false
}

async function saveDefinitionImpl(
  set: WorkflowEditorSet,
  get: WorkflowEditorGet,
): Promise<boolean> {
  const { definition, nodes, edges } = get()
  if (!definition) {
    return failSaveWithMessage(
      set,
      'Cannot save: no workflow loaded',
      { reason: 'no_definition' },
    )
  }
  const validationMessage = validateGraphForSave(nodes, edges)
  if (validationMessage !== null) {
    return failSaveWithMessage(set, validationMessage, {
      badNodeIds: nodes.filter((n) => !n.type).map((n) => n.id),
      badEdgeIds: edges
        .filter((e) => !readEdgeField(e.data, 'edgeType'))
        .map((e) => e.id),
    })
  }
  set({ saving: true, error: null })
  try {
    const updatedDef = await updateWorkflow(
      definition.id,
      buildUpdatePayload(definition, nodes, edges),
    )
    set({
      definition: updatedDef,
      saving: false,
      dirty: false,
      validationResult: null,
    })
    useToastStore.getState().add({
      variant: 'success',
      title: `Workflow ${updatedDef.name} saved`,
    })
    return true
  } catch (err) {
    if (getErrorCode(err) === ErrorCode.VERSION_CONFLICT) {
      return handleVersionConflict(set, get, definition, err)
    }
    return handleSaveError(set, err)
  }
}

async function loadDefinitionImpl(
  set: WorkflowEditorSet,
  id: string,
): Promise<void> {
  set((prev) => ({
    loading: true,
    error: null,
    versions: [],
    versionsLoading: false,
    versionsHasMore: false,
    versionsNextCursor: null,
    diffResult: null,
    diffLoading: false,
    _versionsRequestId: prev._versionsRequestId + 1,
    _diffRequestId: prev._diffRequestId + 1,
  }))
  try {
    const def = await getWorkflow(id)
    const { nodes, edges, yaml } = parseDefinition(def)
    set({
      definition: def,
      nodes,
      edges,
      loading: false,
      dirty: false,
      selectedNodeId: null,
      undoStack: [],
      redoStack: [],
      yamlPreview: yaml,
      validationResult: null,
    })
  } catch (err) {
    log.warn('Failed to load workflow definition', sanitizeForLog(err))
    set({ loading: false, error: getErrorMessage(err) })
  }
}

function startCreateDefinition(set: WorkflowEditorSet): void {
  set((prev) => ({
    loading: true,
    error: null,
    versions: [],
    versionsLoading: false,
    versionsHasMore: false,
    versionsNextCursor: null,
    diffResult: null,
    diffLoading: false,
    _versionsRequestId: prev._versionsRequestId + 1,
    _diffRequestId: prev._diffRequestId + 1,
  }))
}

async function createDefinitionImpl(
  set: WorkflowEditorSet,
  name: string,
  workflowType: string,
): Promise<void> {
  startCreateDefinition(set)
  try {
    const startId = generateNodeId()
    const endId = generateNodeId()
    const def = await createWorkflow({
      name,
      description: '',
      version: '1.0.0',
      workflow_type: workflowType as
        | 'sequential_pipeline'
        | 'parallel_execution'
        | 'kanban'
        | 'agile_kanban',
      inputs: [],
      outputs: [],
      is_subworkflow: false,
      nodes: [
        { id: startId, type: 'start', label: 'Start', position_x: 250, position_y: 50, config: {} },
        { id: endId, type: 'end', label: 'End', position_x: 250, position_y: 400, config: {} },
      ],
      edges: [],
    })
    const nodes: Node[] = [
      { id: startId, type: 'start', position: { x: 250, y: 50 }, data: { label: 'Start', config: {} } },
      { id: endId, type: 'end', position: { x: 250, y: 400 }, data: { label: 'End', config: {} } },
    ]
    const yaml = regenerateYaml(nodes, [], def)
    set({
      definition: def,
      nodes,
      edges: [],
      loading: false,
      dirty: false,
      selectedNodeId: null,
      undoStack: [],
      redoStack: [],
      yamlPreview: yaml,
      validationResult: null,
    })
    useToastStore.getState().add({
      variant: 'success',
      title: `Workflow ${def.name} created`,
    })
  } catch (err) {
    log.warn('Failed to create workflow definition', sanitizeForLog(err))
    useToastStore.getState().add({
      variant: 'error',
      ...getCrudErrorTitle(err, 'Failed to create workflow'),
      description: getErrorMessage(err),
    })
    set({ loading: false, error: getErrorMessage(err) })
  }
}

function resetPersistenceImpl(set: WorkflowEditorSet): void {
  set((prev) => ({
    definition: null,
    nodes: [],
    edges: [],
    selectedNodeId: null,
    dirty: false,
    saving: false,
    loading: false,
    error: null,
    validationResult: null,
    validating: false,
    undoStack: [],
    redoStack: [],
    yamlPreview: '',
    clipboard: null,
    versionHistoryOpen: false,
    versions: [],
    versionsLoading: false,
    versionsHasMore: false,
    versionsNextCursor: null,
    diffResult: null,
    diffLoading: false,
    _versionsRequestId: prev._versionsRequestId + 1,
    _diffRequestId: prev._diffRequestId + 1,
  }))
}

export const createPersistenceSlice: SliceCreator<PersistenceSlice> = (
  set,
  get,
) => ({
  definition: null,
  saving: false,
  loading: false,
  error: null,

  loadDefinition: (id) => loadDefinitionImpl(set, id),
  createDefinition: (name, workflowType) =>
    createDefinitionImpl(set, name, workflowType),
  saveDefinition: () => saveDefinitionImpl(set, get),

  exportYaml: () => {
    const { definition, yamlPreview } = get()
    if (!definition) {
      return Promise.reject(new Error('Cannot export: no workflow loaded'))
    }
    return Promise.resolve(yamlPreview)
  },

  reset: () => resetPersistenceImpl(set),
})
