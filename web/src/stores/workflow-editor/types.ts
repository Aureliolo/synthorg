import type { StateCreator } from 'zustand'
import type { Connection, Edge, EdgeChange, Node, NodeChange } from '@xyflow/react'
import type {
  WorkflowDefinition,
  WorkflowDefinitionVersionSummary,
  WorkflowDiff,
  WorkflowNodeType,
  WorkflowValidationResult,
} from '@/api/types/workflows'
import type { ClipboardData } from '@/pages/workflow-editor/copy-paste'

export interface WorkflowSnapshot {
  nodes: Node[]
  edges: Edge[]
}

export interface GraphSlice {
  nodes: Node[]
  edges: Edge[]
  selectedNodeId: string | null
  dirty: boolean
  yamlPreview: string
  addNode: (type: WorkflowNodeType, position: { x: number; y: number }) => void
  updateNodeConfig: (nodeId: string, config: Record<string, unknown>) => void
  removeNode: (nodeId: string) => void
  onConnect: (connection: Connection) => void
  removeEdge: (edgeId: string) => void
  onNodesChange: (changes: NodeChange[]) => void
  onEdgesChange: (changes: EdgeChange[]) => void
  selectNode: (nodeId: string | null) => void
}

export interface UndoRedoSlice {
  undoStack: WorkflowSnapshot[]
  redoStack: WorkflowSnapshot[]
  undo: () => void
  redo: () => void
}

export interface ValidationSlice {
  validationResult: WorkflowValidationResult | null
  validating: boolean
  validate: () => Promise<void>
}

export interface ClipboardSlice {
  clipboard: ClipboardData | null
  copySelectedNodes: () => void
  pasteNodes: () => void
}

/**
 * Discriminates the most recent persistence error so the editor banner can show
 * a context-aware title instead of a single generic one.
 */
export type WorkflowEditorErrorKind =
  | 'save'
  | 'load'
  | 'create'
  | 'conflict'
  | 'validation'

export interface PersistenceSlice {
  definition: WorkflowDefinition | null
  saving: boolean
  loading: boolean
  error: string | null
  /** Kind of the current ``error``, or null when there is no error. */
  errorKind: WorkflowEditorErrorKind | null
  loadDefinition: (id: string) => Promise<void>
  createDefinition: (name: string, workflowType: string) => Promise<void>
  saveDefinition: () => Promise<boolean>
  exportYaml: () => Promise<string>
  reset: () => void
}

export interface VersionsSlice {
  versionHistoryOpen: boolean
  versions: readonly WorkflowDefinitionVersionSummary[]
  versionsLoading: boolean
  versionsHasMore: boolean
  /** Opaque cursor for the next page; null on the final page. */
  versionsNextCursor: string | null
  diffResult: WorkflowDiff | null
  diffLoading: boolean
  /** @internal Request counter to discard stale version responses. */
  _versionsRequestId: number
  /** @internal Request counter to discard stale diff responses. */
  _diffRequestId: number
  toggleVersionHistory: () => void
  loadVersions: () => Promise<void>
  loadMoreVersions: () => Promise<void>
  loadDiff: (fromVersion: number, toVersion: number) => Promise<void>
  clearDiff: () => void
  rollback: (targetVersion: number) => Promise<void>
}

export type WorkflowEditorState =
  & GraphSlice
  & UndoRedoSlice
  & ValidationSlice
  & ClipboardSlice
  & PersistenceSlice
  & VersionsSlice

export type SliceCreator<T> = StateCreator<WorkflowEditorState, [], [], T>

export const MAX_UNDO = 50
