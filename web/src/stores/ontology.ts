/**
 * Zustand store for ontology entity catalog and drift monitor.
 */
import { create, type StoreApi } from 'zustand'
import { paginateAll } from '@/api/client'
import {
  deleteEntity as apiDeleteEntity,
  listEntities,
  listDriftReports,
} from '@/api/endpoints/ontology'
import type {
  DriftReportResponse,
  EntityListMeta,
  EntityResponse,
} from '@/api/types/ontology'
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
  /**
   * Catalog-wide aggregates from the list endpoint's ``meta`` envelope
   * (core/user/total counts + drift summary); ``null`` until first load.
   */
  entityMeta: EntityListMeta | null
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

// Keep the catalog-summary aggregates aligned with an optimistic list mutation
// so the "N total (X core, Y user)" line does not drift from the rendered grid.
// ``drift_summary`` is backend-only, so it is preserved untouched.
function adjustEntityMeta(
  meta: EntityListMeta | null,
  entity: EntityResponse,
  delta: number,
): EntityListMeta | null {
  if (meta === null) return null
  const isCore = entity.tier === 'core'
  return {
    ...meta,
    total_count: Math.max(0, meta.total_count + delta),
    core_count: isCore ? Math.max(0, meta.core_count + delta) : meta.core_count,
    user_count: isCore ? meta.user_count : Math.max(0, meta.user_count + delta),
  }
}

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
    entityMeta: removed ? adjustEntityMeta(s.entityMeta, removed, -1) : s.entityMeta,
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
        entityMeta: shouldRestore
          ? adjustEntityMeta(s.entityMeta, removed, 1)
          : s.entityMeta,
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
  entityMeta: null,
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
      // EntityCatalog filters / sorts / searches / paginates client-side, so
      // it needs the WHOLE catalog, not the first page. Walk every cursor page
      // via paginateAll; the ``meta`` aggregates are catalog-wide (identical on
      // each page), so capture the latest page's copy for the summary line.
      // Held on an object (not a bare ``let``) so the closure assignment is
      // visible to the type after the await.
      const captured: { meta: EntityListMeta | null } = { meta: null }
      const entities = await paginateAll<EntityResponse>(async (cursor) => {
        const result = await listEntities({ cursor, limit: 200 })
        captured.meta = result.meta
        return result
      })
      set({
        entities,
        totalEntities: captured.meta?.total_count ?? entities.length,
        entityMeta: captured.meta,
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
