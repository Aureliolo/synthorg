import type { Node } from '@xyflow/react'
import {
  createWorkflow,
  getWorkflow,
  updateWorkflow,
} from '@/api/endpoints/workflows'
import { createLogger } from '@/lib/logger'
import { getErrorMessage, isAxiosError } from '@/utils/errors'
import { sanitizeForLog } from '@/utils/logging'
import { isObject, isString } from '@/utils/type-guards'
import type { PersistenceSlice, SliceCreator } from './types'
import { generateNodeId, parseDefinition, regenerateYaml } from './yaml'

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

export const createPersistenceSlice: SliceCreator<PersistenceSlice> = (set, get) => ({
  definition: null,
  saving: false,
  loading: false,
  error: null,

  loadDefinition: async (id) => {
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
  },

  createDefinition: async (name, workflowType) => {
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
      const startId = generateNodeId()
      const endId = generateNodeId()
      const def = await createWorkflow({
        name,
        workflow_type: workflowType,
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
    } catch (err) {
      log.warn('Failed to create workflow definition', sanitizeForLog(err))
      set({ loading: false, error: getErrorMessage(err) })
    }
  },

  saveDefinition: async () => {
    const { definition, nodes, edges } = get()
    if (!definition) {
      set({ error: 'Cannot save: no workflow loaded' })
      return
    }
    const badNodes = nodes.filter((n) => !n.type)
    const badEdges = edges.filter((e) => !readEdgeField(e.data, 'edgeType'))
    if (badNodes.length > 0 || badEdges.length > 0) {
      const parts: string[] = []
      if (badNodes.length > 0) parts.push(`nodes missing type: ${badNodes.map((n) => n.id).join(', ')}`)
      if (badEdges.length > 0) parts.push(`edges missing type: ${badEdges.map((e) => e.id).join(', ')}`)
      set({ error: `Cannot save -- ${parts.join('; ')}. Remove and re-add the affected items.` })
      return
    }

    set({ saving: true, error: null })
    try {
      const updatedDef = await updateWorkflow(definition.id, {
        nodes: nodes.map((n) => ({
          id: n.id,
          type: n.type!,
          label: readNodeLabel(n.data) ?? n.id,
          position_x: n.position.x,
          position_y: n.position.y,
          config: readNodeConfig(n.data) ?? {},
        })),
        edges: edges.map((e) => ({
          id: e.id,
          source_node_id: e.source,
          target_node_id: e.target,
          type: readEdgeField(e.data, 'edgeType') ?? 'sequential',
          label: isString(e.label) ? e.label : null,
        })),
        expected_revision: definition.revision,
      })
      set({ definition: updatedDef, saving: false, dirty: false, validationResult: null })
    } catch (err) {
      // ``isAxiosError`` lets us read response.status without an
      // unsafe ``as`` cast through a hand-rolled shape.
      const status = isAxiosError(err) ? err.response?.status : undefined
      if (status === 409 && definition) {
        log.warn('Version conflict saving workflow, reloading', sanitizeForLog(err))
        set({ saving: false, error: 'Version conflict -- another save occurred. Reloading...' })
        await get().loadDefinition(definition.id)
      } else {
        log.warn('Failed to save workflow definition', sanitizeForLog(err))
        set({ saving: false, error: getErrorMessage(err) })
      }
    }
  },

  exportYaml: async () => {
    const { definition, yamlPreview } = get()
    if (!definition) throw new Error('Cannot export: no workflow loaded')
    return yamlPreview
  },

  reset: () => {
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
  },
})
