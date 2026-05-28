/**
 * Zustand store for ontology entity catalog and drift monitor.
 */
import { create, type StoreApi } from 'zustand'
import {
  deleteEntity as apiDeleteEntity,
  listEntities,
  listDriftReports,
  type EntityResponse,
  type DriftReportResponse,
} from '@/api/endpoints/ontology'
import { createLogger } from '@/lib/logger'
import { useToastStore } from '@/stores/toast'
import { getCrudErrorTitle, getErrorMessage } from '@/utils/errors'

const log = createLogger('ontology')

type TierFilter = 'all' | 'core' | 'user'

export type EntitySortKey = 'name' | 'tier' | 'attribute_count'
export type SortDirection = 'asc' | 'desc'

interface OntologyState {
  // ── Entity catalog ──
  entities: readonly EntityResponse[]
  totalEntities: number
  entitiesLoading: boolean
  entitiesError: string | null

  // ── Drift monitor ──
  driftReports: readonly DriftReportResponse[]
  driftLoading: boolean
  driftError: string | null

  // ── Filters + sort ──
  tierFilter: TierFilter
  searchQuery: string
  entitySortBy: EntitySortKey
  entitySortDirection: SortDirection

  // ── Selected entity ──
  selectedEntity: EntityResponse | null

  // ── Mutation flag ──
  mutating: boolean

  // ── Actions ──
  fetchEntities: () => Promise<void>
  fetchDriftReports: () => Promise<void>
  deleteEntity: (name: string) => Promise<boolean>
  setTierFilter: (tier: TierFilter) => void
  setSearchQuery: (q: string) => void
  setEntitySort: (key: EntitySortKey, direction?: SortDirection) => void
  setSelectedEntity: (entity: EntityResponse | null) => void
}

type OntologySet = StoreApi<OntologyState>['setState']
type OntologyGet = StoreApi<OntologyState>['getState']

async function deleteEntityImpl(
  set: OntologySet,
  get: OntologyGet,
  name: string,
): Promise<boolean> {
  // Capture only the row we optimistically remove so a failure
  // rollback cannot clobber entities a concurrent fetch refreshed.
  const removed = get().entities.find((e) => e.name === name) ?? null
  const previousSelected = get().selectedEntity
  set((s) => ({
    mutating: true,
    entities: s.entities.filter((e) => e.name !== name),
    totalEntities: Math.max(0, s.totalEntities - (removed ? 1 : 0)),
    selectedEntity: s.selectedEntity?.name === name ? null : s.selectedEntity,
  }))
  try {
    await apiDeleteEntity(name)
    set({ mutating: false })
    useToastStore.getState().add({
      variant: 'success',
      title: `Entity ${name} deleted`,
    })
    return true
  } catch (err) {
    set((s) => {
      const alreadyBack = s.entities.some((e) => e.name === name)
      const shouldRestore = !alreadyBack && removed !== null
      return {
        mutating: false,
        entities: shouldRestore ? [removed, ...s.entities] : s.entities,
        totalEntities: shouldRestore ? s.totalEntities + 1 : s.totalEntities,
        selectedEntity: shouldRestore ? previousSelected : s.selectedEntity,
      }
    })
    log.error('Delete entity failed:', getErrorMessage(err))
    useToastStore.getState().add({
      variant: 'error',
      ...getCrudErrorTitle(err, 'Failed to delete entity'),
      description: getErrorMessage(err),
    })
    return false
  }
}

export const useOntologyStore = create<OntologyState>()((set, get) => ({
  // ── Defaults ──
  entities: [],
  totalEntities: 0,
  entitiesLoading: false,
  entitiesError: null,

  driftReports: [],
  driftLoading: false,
  driftError: null,

  tierFilter: 'all',
  searchQuery: '',
  entitySortBy: 'name',
  entitySortDirection: 'asc',
  selectedEntity: null,
  mutating: false,

  // ── Actions ──
  fetchEntities: async () => {
    set({ entitiesLoading: true, entitiesError: null })
    try {
      const result = await listEntities({ limit: 200 })
      set({
        entities: result.data,
        totalEntities: result.data.length,
        entitiesLoading: false,
      })
    } catch (err) {
      log.error('Failed to fetch entities:', getErrorMessage(err))
      set({
        entitiesError: getErrorMessage(err),
        entitiesLoading: false,
      })
    }
  },

  fetchDriftReports: async () => {
    set({ driftLoading: true, driftError: null })
    try {
      const result = await listDriftReports({ limit: 100 })
      set({
        driftReports: result.data,
        driftLoading: false,
      })
    } catch (err) {
      log.error('Failed to fetch drift reports:', getErrorMessage(err))
      set({
        driftError: getErrorMessage(err),
        driftLoading: false,
      })
    }
  },

  deleteEntity: (name: string) => deleteEntityImpl(set, get, name),

  setTierFilter: (tier: TierFilter) => set({ tierFilter: tier }),
  setSearchQuery: (q: string) => set({ searchQuery: q }),
  setEntitySort: (key, direction) =>
    set((state) => {
      // When the same key is re-selected without an explicit direction,
      // toggle.  Otherwise apply the explicit direction or default to
      // ascending for a new key.
      const nextDirection: SortDirection =
        direction ??
        (state.entitySortBy === key
          ? state.entitySortDirection === 'asc'
            ? 'desc'
            : 'asc'
          : 'asc')
      return { entitySortBy: key, entitySortDirection: nextDirection }
    }),
  setSelectedEntity: (entity: EntityResponse | null) =>
    set({ selectedEntity: entity }),
}))
