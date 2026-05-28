import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

import { paginateAll } from '@/api/client'
import {
  createAdminPreset,
  deleteAdminPreset,
  listAdminPresets,
} from '@/api/endpoints/personalities'
import type { PresetSummaryResponse } from '@/api/types'
import { useListPagination } from '@/hooks/use-list-pagination'
import { createLogger } from '@/lib/logger'
import { useToastStore } from '@/stores/toast'
import { getCrudErrorTitle, getErrorMessage } from '@/utils/errors'
import { getLocale } from '@/utils/locale'
import { sanitizeForLog } from '@/utils/logging'

const log = createLogger('PersonalitiesAdminPage')

// Hoisted out of render so ``navigator.language`` is read once at module init
// (``@eslint-react/globals`` flags in-render reads of navigator).
const LOCALE = getLocale()

export type PresetSortKey = 'name-asc' | 'name-desc'

export interface PersonalitiesAdminController {
  presets: readonly PresetSummaryResponse[]
  loading: boolean
  error: string | null
  visible: readonly PresetSummaryResponse[]
  searchQuery: string
  sortKey: PresetSortKey
  createOpen: boolean
  createName: string
  createDescription: string
  createTraits: string[]
  createSaving: boolean
  deleteTarget: PresetSummaryResponse | null
  deleting: boolean
  pagination: ReturnType<typeof useListPagination<PresetSummaryResponse>>
  setSearchQuery: (value: string) => void
  setSortKey: (value: PresetSortKey) => void
  openCreateDrawer: () => void
  setCreateName: (value: string) => void
  setCreateDescription: (value: string) => void
  setCreateTraits: (value: string[]) => void
  setDeleteTarget: (target: PresetSummaryResponse | null) => void
  handleCreateClose: () => void
  handleCreateSubmit: () => Promise<void>
  handleDeleteConfirm: () => Promise<void>
}

export function usePersonalitiesAdminController(): PersonalitiesAdminController {
  const list = usePresetsListState()
  const createState = useCreateDrawerState()
  const deleteState = useDeleteDialogState()

  const visible = useMemo(
    () => filterAndSortPresets(list.presets, list.searchQuery, list.sortKey),
    [list.presets, list.searchQuery, list.sortKey],
  )
  const pagination = useListPagination({
    items: visible,
    namespace: 'personalities',
    defaultPageSize: 24,
    pageSizeOptions: [12, 24, 48],
  })
  useEffect(() => {
    pagination.resetPage()
    // ``pagination`` is a fresh object every render from ``useListPagination``
    // (no useMemo on the returned object), so including it would re-run this
    // effect on every render and reset the page on every keystroke. The
    // ``resetPage`` reference is what we actually need; depending on the
    // search/sort primitives is sufficient.
    // eslint-disable-next-line @eslint-react/exhaustive-deps -- pagination.resetPage identity is unstable; sort/search are the real triggers
  }, [list.searchQuery, list.sortKey])

  const handleCreateSubmit = useCallback(
    () => submitCreate(createState, list.refresh),
    [createState, list.refresh],
  )
  const handleDeleteConfirm = useCallback(
    () => confirmDelete(deleteState, list.refresh),
    [deleteState, list.refresh],
  )

  return {
    presets: list.presets,
    loading: list.loading,
    error: list.error,
    visible,
    searchQuery: list.searchQuery,
    sortKey: list.sortKey,
    createOpen: createState.createOpen,
    createName: createState.createName,
    createDescription: createState.createDescription,
    createTraits: createState.createTraits,
    createSaving: createState.createSaving,
    deleteTarget: deleteState.deleteTarget,
    deleting: deleteState.deleting,
    pagination,
    setSearchQuery: list.setSearchQuery,
    setSortKey: list.setSortKey,
    openCreateDrawer: () => createState.setCreateOpen(true),
    setCreateName: createState.setCreateName,
    setCreateDescription: createState.setCreateDescription,
    setCreateTraits: createState.setCreateTraits,
    setDeleteTarget: deleteState.setDeleteTarget,
    handleCreateClose: createState.handleCreateClose,
    handleCreateSubmit,
    handleDeleteConfirm,
  }
}

interface PresetsListState {
  presets: readonly PresetSummaryResponse[]
  loading: boolean
  error: string | null
  searchQuery: string
  sortKey: PresetSortKey
  setSearchQuery: (v: string) => void
  setSortKey: (v: PresetSortKey) => void
  refresh: () => Promise<void>
}

function usePresetsListState(): PresetsListState {
  const [presets, setPresets] = useState<readonly PresetSummaryResponse[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [searchQuery, setSearchQuery] = useState('')
  const [sortKey, setSortKey] = useState<PresetSortKey>('name-asc')
  const refreshIdRef = useRef(0)

  const refresh = useCallback(
    () => runPresetsRefresh(refreshIdRef, setPresets, setError, setLoading),
    [],
  )

  useEffect(() => {
    const ctrl = { cancelled: false }
    void Promise.resolve().then(() => {
      if (ctrl.cancelled) return
      setLoading(true)
      void refresh()
    })
    return () => {
      ctrl.cancelled = true
    }
  }, [refresh])

  return {
    presets,
    loading,
    error,
    searchQuery,
    sortKey,
    setSearchQuery,
    setSortKey,
    refresh,
  }
}

interface CreateDrawerState {
  createOpen: boolean
  createName: string
  createDescription: string
  createTraits: string[]
  createSaving: boolean
  setCreateOpen: (v: boolean) => void
  setCreateName: (v: string) => void
  setCreateDescription: (v: string) => void
  setCreateTraits: (v: string[]) => void
  setCreateSaving: (v: boolean) => void
  handleCreateClose: () => void
}

function useCreateDrawerState(): CreateDrawerState {
  const [createOpen, setCreateOpen] = useState(false)
  const [createName, setCreateName] = useState('')
  const [createDescription, setCreateDescription] = useState('')
  const [createTraits, setCreateTraits] = useState<string[]>([])
  const [createSaving, setCreateSaving] = useState(false)
  const handleCreateClose = useCallback(() => {
    setCreateOpen(false)
    setCreateName('')
    setCreateDescription('')
    setCreateTraits([])
  }, [])
  return {
    createOpen,
    createName,
    createDescription,
    createTraits,
    createSaving,
    setCreateOpen,
    setCreateName,
    setCreateDescription,
    setCreateTraits,
    setCreateSaving,
    handleCreateClose,
  }
}

interface DeleteDialogState {
  deleteTarget: PresetSummaryResponse | null
  deleting: boolean
  setDeleteTarget: (target: PresetSummaryResponse | null) => void
  setDeleting: (v: boolean) => void
}

function useDeleteDialogState(): DeleteDialogState {
  const [deleteTarget, setDeleteTarget] = useState<PresetSummaryResponse | null>(null)
  const [deleting, setDeleting] = useState(false)
  return { deleteTarget, deleting, setDeleteTarget, setDeleting }
}

async function submitCreate(
  state: CreateDrawerState,
  refresh: () => Promise<void>,
): Promise<void> {
  const trimmed = state.createName.trim()
  if (trimmed === '') return
  state.setCreateSaving(true)
  try {
    await submitCreatePreset({
      name: trimmed,
      description: state.createDescription.trim(),
      traits: state.createTraits,
    })
    useToastStore.getState().add({
      variant: 'success',
      title: 'Personality preset created',
    })
    state.handleCreateClose()
    await refresh()
  } catch (err) {
    handlePresetCrudError(err, 'Failed to create personality preset')
  } finally {
    state.setCreateSaving(false)
  }
}

async function confirmDelete(
  state: DeleteDialogState,
  refresh: () => Promise<void>,
): Promise<void> {
  if (!state.deleteTarget) return
  state.setDeleting(true)
  try {
    await deleteAdminPreset(state.deleteTarget.name)
    useToastStore.getState().add({
      variant: 'success',
      title: `Personality preset '${state.deleteTarget.name}' deleted`,
    })
    state.setDeleteTarget(null)
    await refresh()
  } catch (err) {
    handlePresetCrudError(err, 'Failed to delete personality preset')
  } finally {
    state.setDeleting(false)
  }
}

async function runPresetsRefresh(
  refreshIdRef: React.MutableRefObject<number>,
  setPresets: (rows: readonly PresetSummaryResponse[]) => void,
  setError: (msg: string | null) => void,
  setLoading: (v: boolean) => void,
): Promise<void> {
  const requestId = ++refreshIdRef.current
  setError(null)
  try {
    // ``listAdminPresets`` is cursor-aware per the pagination convention.
    // This page does client-side search/sort/filter, so it materialises the
    // full set via ``paginateAll`` (capped internally to PAGINATE_ALL_MAX_PAGES).
    const rows = await paginateAll<PresetSummaryResponse>((cursor) =>
      listAdminPresets({ cursor }),
    )
    if (requestId !== refreshIdRef.current) return
    setPresets(rows)
  } catch (err) {
    if (requestId !== refreshIdRef.current) return
    const message = getErrorMessage(err)
    log.error('listAdminPresets failed', { error: sanitizeForLog(message) })
    setError(message)
  } finally {
    if (requestId === refreshIdRef.current) setLoading(false)
  }
}

function filterAndSortPresets(
  presets: readonly PresetSummaryResponse[],
  searchQuery: string,
  sortKey: PresetSortKey,
): readonly PresetSummaryResponse[] {
  const q = searchQuery.trim().toLowerCase()
  const matched = q
    ? presets.filter(
        (preset) =>
          preset.name.toLowerCase().includes(q) ||
          preset.description.toLowerCase().includes(q),
      )
    : presets
  return [...matched].sort((a, b) => {
    const cmp = a.name.localeCompare(b.name, LOCALE)
    return sortKey === 'name-asc' ? cmp : -cmp
  })
}

interface CreatePresetInput {
  name: string
  description: string
  traits: string[]
}

async function submitCreatePreset(input: CreatePresetInput): Promise<void> {
  await createAdminPreset({
    name: input.name,
    description: input.description,
    traits: input.traits,
    // Numeric Big-Five axes default to 0.5 on the backend; sending them
    // explicitly keeps the wire shape predictable for snapshot tests.
    agreeableness: 0.5,
    conscientiousness: 0.5,
    extraversion: 0.5,
    openness: 0.5,
    stress_response: 0.5,
    communication_style: 'neutral',
  })
}

function handlePresetCrudError(err: unknown, fallback: string): void {
  log.error(fallback, sanitizeForLog(err))
  useToastStore.getState().add({
    variant: 'error',
    ...getCrudErrorTitle(err, fallback),
    description: getErrorMessage(err),
  })
}
