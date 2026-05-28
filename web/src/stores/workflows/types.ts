import type { StoreApi } from 'zustand'
import type {
  BlueprintInfo,
  CreateFromBlueprintRequest,
  CreateWorkflowDefinitionRequest,
  WorkflowDefinition,
} from '@/api/types/workflows'

export interface BatchDeleteOutcome {
  succeeded: number
  failed: number
  failedReasons: readonly string[]
}

export interface WorkflowsState {
  // List
  workflows: readonly WorkflowDefinition[]
  totalWorkflows: number
  nextCursor: string | null
  hasMore: boolean
  listLoading: boolean
  listLoadingMore: boolean
  listError: string | null

  // Blueprints
  blueprints: readonly BlueprintInfo[]
  blueprintsLoading: boolean
  blueprintsError: string | null

  // Filters
  searchQuery: string
  workflowTypeFilter: string | null

  // Actions
  fetchWorkflows: () => Promise<void>
  fetchMoreWorkflows: () => Promise<void>
  loadBlueprints: () => Promise<void>
  createWorkflow: (
    data: CreateWorkflowDefinitionRequest,
  ) => Promise<WorkflowDefinition | null>
  createFromBlueprint: (
    data: CreateFromBlueprintRequest,
  ) => Promise<WorkflowDefinition | null>
  deleteWorkflow: (id: string) => Promise<boolean>
  /** Export the persisted definition as YAML and trigger a download. */
  exportWorkflow: (id: string) => Promise<boolean>
  batchDeleteWorkflows: (
    ids: readonly string[],
  ) => Promise<BatchDeleteOutcome | false>
  setSearchQuery: (q: string) => void
  setWorkflowTypeFilter: (t: string | null) => void
  updateFromWsEvent: () => void
}

export type WorkflowsSet = StoreApi<WorkflowsState>['setState']
export type WorkflowsGet = StoreApi<WorkflowsState>['getState']
