import { useWorkflowEditorStore } from '@/stores/workflow-editor'
import type {
  GraphSlice,
  PersistenceSlice,
  UndoRedoSlice,
  ValidationSlice,
  VersionsSlice,
} from '@/stores/workflow-editor/types'

export type WorkflowEditorStateResult =
  & Pick<
    GraphSlice,
    | 'nodes'
    | 'edges'
    | 'selectedNodeId'
    | 'dirty'
    | 'yamlPreview'
    | 'addNode'
    | 'updateNodeConfig'
    | 'onConnect'
    | 'onNodesChange'
    | 'onEdgesChange'
    | 'selectNode'
  >
  & Pick<PersistenceSlice, 'definition' | 'saving' | 'loading' | 'error' | 'errorKind' | 'loadDefinition' | 'createDefinition' | 'saveDefinition' | 'exportYaml'>
  & Pick<UndoRedoSlice, 'undoStack' | 'redoStack' | 'undo' | 'redo'>
  & Pick<ValidationSlice, 'validationResult' | 'validating' | 'validate'>
  & Pick<VersionsSlice, 'versionHistoryOpen' | 'toggleVersionHistory'>

export function useWorkflowEditorState(): WorkflowEditorStateResult {
  const nodes = useWorkflowEditorStore((s) => s.nodes)
  const edges = useWorkflowEditorStore((s) => s.edges)
  const definition = useWorkflowEditorStore((s) => s.definition)
  const selectedNodeId = useWorkflowEditorStore((s) => s.selectedNodeId)
  const dirty = useWorkflowEditorStore((s) => s.dirty)
  const saving = useWorkflowEditorStore((s) => s.saving)
  const loading = useWorkflowEditorStore((s) => s.loading)
  const error = useWorkflowEditorStore((s) => s.error)
  const errorKind = useWorkflowEditorStore((s) => s.errorKind)
  const validationResult = useWorkflowEditorStore((s) => s.validationResult)
  const validating = useWorkflowEditorStore((s) => s.validating)
  const undoStack = useWorkflowEditorStore((s) => s.undoStack)
  const redoStack = useWorkflowEditorStore((s) => s.redoStack)
  const yamlPreview = useWorkflowEditorStore((s) => s.yamlPreview)
  const versionHistoryOpen = useWorkflowEditorStore((s) => s.versionHistoryOpen)

  const loadDefinition = useWorkflowEditorStore((s) => s.loadDefinition)
  const createDefinition = useWorkflowEditorStore((s) => s.createDefinition)
  const saveDefinition = useWorkflowEditorStore((s) => s.saveDefinition)
  const addNode = useWorkflowEditorStore((s) => s.addNode)
  const updateNodeConfig = useWorkflowEditorStore((s) => s.updateNodeConfig)
  const onConnect = useWorkflowEditorStore((s) => s.onConnect)
  const onNodesChange = useWorkflowEditorStore((s) => s.onNodesChange)
  const onEdgesChange = useWorkflowEditorStore((s) => s.onEdgesChange)
  const selectNode = useWorkflowEditorStore((s) => s.selectNode)
  const undo = useWorkflowEditorStore((s) => s.undo)
  const redo = useWorkflowEditorStore((s) => s.redo)
  const validate = useWorkflowEditorStore((s) => s.validate)
  const exportYaml = useWorkflowEditorStore((s) => s.exportYaml)
  const toggleVersionHistory = useWorkflowEditorStore((s) => s.toggleVersionHistory)

  return {
    nodes,
    edges,
    definition,
    selectedNodeId,
    dirty,
    saving,
    loading,
    error,
    errorKind,
    validationResult,
    validating,
    undoStack,
    redoStack,
    yamlPreview,
    versionHistoryOpen,
    loadDefinition,
    createDefinition,
    saveDefinition,
    addNode,
    updateNodeConfig,
    onConnect,
    onNodesChange,
    onEdgesChange,
    selectNode,
    undo,
    redo,
    validate,
    exportYaml,
    toggleVersionHistory,
  }
}
