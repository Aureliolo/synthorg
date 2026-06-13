import type { Edge, Node } from '@xyflow/react'
import type { WorkflowDefinition } from '@/api/types/workflows'
import { generateYamlPreview } from '@/pages/workflow-editor/workflow-to-yaml'

export function regenerateYaml(
  nodes: Node[],
  edges: Edge[],
  definition: WorkflowDefinition | null,
): string {
  return generateYamlPreview(
    nodes,
    edges,
    definition?.name ?? 'Untitled',
    definition?.workflow_type ?? 'sequential_pipeline',
  )
}

export function generateNodeId(): string {
  return `node-${crypto.randomUUID().slice(0, 8)}`
}

export function generateEdgeId(): string {
  return `edge-${crypto.randomUUID().slice(0, 8)}`
}

export function nodeTypeToEdgeType(sourceType: string | undefined): string {
  if (sourceType === 'conditional') return 'conditional'
  if (sourceType === 'parallel_split') return 'parallel_branch'
  return 'sequential'
}

interface EdgeMeta {
  visualType: string
  sourceHandle: string | undefined
  edgeType: string
  branch: string | undefined
}

function mapPersistedEdge(edgeType: string): EdgeMeta {
  const isTrue = edgeType === 'conditional_true'
  const isFalse = edgeType === 'conditional_false'
  if (isTrue || isFalse) {
    return {
      visualType: 'conditional',
      sourceHandle: isTrue ? 'true' : 'false',
      edgeType,
      branch: isTrue ? 'true' : 'false',
    }
  }
  if (edgeType === 'parallel_branch') {
    return { visualType: 'parallel_branch', sourceHandle: undefined, edgeType, branch: undefined }
  }
  return { visualType: 'sequential', sourceHandle: undefined, edgeType, branch: undefined }
}

/** Parse a WorkflowDefinition into React Flow nodes, edges, and YAML. */
export function parseDefinition(def: WorkflowDefinition): {
  nodes: Node[]
  edges: Edge[]
  yaml: string
} {
  const nodes: Node[] = def.nodes.map((n) => ({
    id: n.id,
    type: n.type,
    position: { x: n.position_x, y: n.position_y },
    data: { label: n.label, config: n.config },
  }))
  const edges: Edge[] = def.edges.map((e) => {
    const meta = mapPersistedEdge(e.type)
    return {
      id: e.id,
      source: e.source_node_id,
      target: e.target_node_id,
      type: meta.visualType,
      sourceHandle: meta.sourceHandle ?? null,
      data: { edgeType: meta.edgeType, branch: meta.branch },
      ...(e.label != null && { label: e.label }),
    }
  })
  const yaml = regenerateYaml(nodes, edges, def)
  return { nodes, edges, yaml }
}
