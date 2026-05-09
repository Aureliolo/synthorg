import { validateWorkflowDraft } from '@/api/endpoints/workflows'
import {
  isWorkflowEdgeType,
  isWorkflowNodeType,
  type WorkflowEdgeType,
  type WorkflowNodeType,
} from '@/api/types/workflows'
import { createLogger } from '@/lib/logger'
import { getErrorMessage } from '@/utils/errors'
import { sanitizeForLog } from '@/utils/logging'
import { isObject, isString } from '@/utils/type-guards'
import type { SliceCreator, ValidationSlice } from './types'

const log = createLogger('workflow-editor:validation')

function readString(data: unknown, key: string): string | undefined {
  if (!isObject(data)) return undefined
  const value = data[key]
  return isString(value) ? value : undefined
}

function readRecord(data: unknown, key: string): Record<string, unknown> | undefined {
  if (!isObject(data)) return undefined
  const value = data[key]
  return isObject(value) ? value : undefined
}

export const createValidationSlice: SliceCreator<ValidationSlice> = (set, get) => ({
  validationResult: null,
  validating: false,

  validate: async () => {
    const { definition, nodes, edges } = get()
    if (!definition) {
      set({ error: 'Cannot validate: no workflow loaded' })
      return
    }
    const badNodes = nodes.filter((n) => !isWorkflowNodeType(n.type))
    const badEdges = edges.filter((e) => !isWorkflowEdgeType(readString(e.data, 'edgeType')))
    if (badNodes.length > 0 || badEdges.length > 0) {
      const parts: string[] = []
      if (badNodes.length > 0) parts.push(`nodes missing/invalid type: ${badNodes.map((n) => n.id).join(', ')}`)
      if (badEdges.length > 0) parts.push(`edges missing/invalid type: ${badEdges.map((e) => e.id).join(', ')}`)
      set({
        error: `Cannot validate -- ${parts.join('; ')}. Remove and re-add the affected items.`,
        validating: false,
      })
      return
    }

    set({ validating: true })
    try {
      const result = await validateWorkflowDraft({
        name: definition.name,
        workflow_type: definition.workflow_type,
        nodes: nodes.map((n) => {
          const nodeType: WorkflowNodeType = isWorkflowNodeType(n.type)
            ? n.type
            : 'task'
          return {
            id: n.id,
            type: nodeType,
            label: readString(n.data, 'label') ?? n.id,
            position_x: n.position.x,
            position_y: n.position.y,
            config: readRecord(n.data, 'config') ?? {},
          }
        }),
        edges: edges.map((e) => {
          const dataType = readString(e.data, 'edgeType')
          const edgeType: WorkflowEdgeType = isWorkflowEdgeType(dataType)
            ? dataType
            : 'sequential'
          return {
            id: e.id,
            source_node_id: e.source,
            target_node_id: e.target,
            type: edgeType,
            label: isString(e.label) ? e.label : null,
          }
        }),
      })
      set({ validationResult: result, validating: false })
    } catch (err) {
      log.warn('Workflow validation failed', sanitizeForLog(err))
      set({ validating: false, validationResult: null, error: getErrorMessage(err) })
    }
  },
})
