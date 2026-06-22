import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useSetupWizardStore } from '@/stores/setup-wizard'
import { useToastStore } from '@/stores/toast'
import type { TemplateInfoResponse } from '@/api/types/setup'
import {
  CATEGORY_ORDER,
  deriveCategoryFromTags,
  getCategoryLabel,
} from '@/utils/template-categories'
import { makeEnumParser } from '@/utils/type-guards'

export const MAX_COMPARE = 3

/** Template size tags used for recommendation heuristics. */
const TAG_SOLO = 'solo'
const TAG_SMALL_TEAM = 'small-team'

/** Agent-count filter buckets. */
export type SizeFilter = 'all' | 'small' | 'medium' | 'large'

export const SIZE_OPTIONS: readonly { value: SizeFilter; label: string }[] = [
  { value: 'all', label: 'Any size' },
  { value: 'small', label: '1-3 agents' },
  { value: 'medium', label: '4-8 agents' },
  { value: 'large', label: '9+ agents' },
]

export const parseSizeFilter = makeEnumParser<SizeFilter>(SIZE_OPTIONS.map((o) => o.value))

function matchesSize(template: TemplateInfoResponse, size: SizeFilter): boolean {
  if (size === 'all') return true
  const count = template.agent_count
  if (size === 'small') return count >= 1 && count <= 3
  if (size === 'medium') return count >= 4 && count <= 8
  return count >= 9
}

interface TemplateFilters {
  searchQuery: string
  categoryFilter: string
  sizeFilter: SizeFilter
}

function matchesFilters(
  template: TemplateInfoResponse,
  categoryFilter: string,
  sizeFilter: SizeFilter,
  query: string,
): boolean {
  if (categoryFilter !== 'all' && deriveCategoryFromTags(template.tags) !== categoryFilter) {
    return false
  }
  if (!matchesSize(template, sizeFilter)) return false
  if (!query) return true
  const keywords = query.split(' ').filter(Boolean)
  if (keywords.length === 0) return true
  const haystack =
    `${template.display_name} ${template.description} ${template.tags.join(' ')} ${template.workflow} ${template.autonomy_level}`.toLowerCase()
  return keywords.every((kw) => haystack.includes(kw))
}

function filterTemplates(
  templates: readonly TemplateInfoResponse[],
  { searchQuery, categoryFilter, sizeFilter }: TemplateFilters,
): TemplateInfoResponse[] {
  const query = searchQuery.trim().toLowerCase()
  return templates.filter((t) => matchesFilters(t, categoryFilter, sizeFilter, query))
}

/**
 * Recommended templates are derived from tags alone, surfacing
 * approachable starting points (solo / small-team / startup / mvp) so
 * first-time users see a manageable shape before scrolling the grid.
 */
function computeRecommendedTemplates(
  templates: readonly TemplateInfoResponse[],
): ReadonlySet<string> {
  const recommended = new Set<string>()
  const smallTags = new Set([TAG_SOLO, TAG_SMALL_TEAM, 'startup', 'mvp'])
  for (const template of templates) {
    if (template.tags.some((tag) => smallTags.has(tag))) {
      recommended.add(template.name)
    }
  }
  return recommended
}

/** Categories present in the templates, in canonical order, with an "all" head. */
function computeAvailableCategories(
  templates: readonly TemplateInfoResponse[],
): { value: string; label: string }[] {
  const seen = new Set<string>()
  for (const t of templates) {
    seen.add(deriveCategoryFromTags(t.tags))
  }
  const ordered: { value: string; label: string }[] = [{ value: 'all', label: 'All categories' }]
  for (const key of CATEGORY_ORDER) {
    if (seen.has(key)) {
      ordered.push({ value: key, label: getCategoryLabel(key) })
    }
  }
  return ordered
}

function splitRecommended(
  filtered: readonly TemplateInfoResponse[],
  recommendedTemplates: ReadonlySet<string>,
): { recommended: TemplateInfoResponse[]; others: TemplateInfoResponse[] } {
  const recommended: TemplateInfoResponse[] = []
  const others: TemplateInfoResponse[] = []
  for (const t of filtered) {
    if (recommendedTemplates.has(t.name)) {
      recommended.push(t)
    } else {
      others.push(t)
    }
  }
  return { recommended, others }
}

export interface TemplateStepController {
  templates: readonly TemplateInfoResponse[]
  templatesLoading: boolean
  templatesError: string | null
  selectedTemplate: string | null
  comparedTemplates: readonly string[]
  recommendedTemplates: ReadonlySet<string>
  availableCategories: { value: string; label: string }[]
  filteredTemplates: readonly TemplateInfoResponse[]
  recommended: readonly TemplateInfoResponse[]
  others: readonly TemplateInfoResponse[]
  comparedTemplateObjects: readonly TemplateInfoResponse[]
  searchQuery: string
  setSearchQuery: (value: string) => void
  categoryFilter: string
  setCategoryFilter: (value: string) => void
  sizeFilter: SizeFilter
  setSizeFilter: (value: SizeFilter) => void
  hasActiveFilters: boolean
  handleSelect: (name: string) => void
  handleToggleCompare: (name: string) => void
  handleRemoveFromCompare: (name: string) => void
  clearFilters: () => void
  clearComparison: () => void
  onRetry: () => void
}

export function useTemplateStepController(): TemplateStepController {
  const templates = useSetupWizardStore((s) => s.templates)
  const templatesLoading = useSetupWizardStore((s) => s.templatesLoading)
  const templatesError = useSetupWizardStore((s) => s.templatesError)
  const selectedTemplate = useSetupWizardStore((s) => s.selectedTemplate)
  const comparedTemplates = useSetupWizardStore((s) => s.comparedTemplates)

  const [searchQuery, setSearchQuery] = useState('')
  const [categoryFilter, setCategoryFilter] = useState('all')
  const [sizeFilter, setSizeFilter] = useState<SizeFilter>('all')

  const hasFetchedRef = useRef(false)
  useEffect(() => {
    if (!hasFetchedRef.current && !templatesLoading && !templatesError) {
      hasFetchedRef.current = true
      void useSetupWizardStore.getState().fetchTemplates()
    }
  }, [templatesLoading, templatesError])

  const recommendedTemplates = useMemo(() => computeRecommendedTemplates(templates), [templates])
  const availableCategories = useMemo(() => computeAvailableCategories(templates), [templates])
  const filteredTemplates = useMemo(
    () => filterTemplates(templates, { searchQuery, categoryFilter, sizeFilter }),
    [templates, searchQuery, categoryFilter, sizeFilter],
  )

  // Track step completion -- validates against the full template list (not
  // filtered) so UI filters don't invalidate the selection. Skip while loading
  // AND while not-yet-fetched (``templates`` is the slice default ``[]`` before
  // the fetch resolves): on reload ``templatesLoading`` starts false, so
  // skipping only on loading would demote a previously-selected template to
  // incomplete every mount before the list arrives.
  useEffect(() => {
    if (templatesLoading || (templates.length === 0 && !templatesError)) return
    const store = useSetupWizardStore.getState()
    if (selectedTemplate && templates.some((t) => t.name === selectedTemplate)) {
      store.markStepComplete('template')
    } else {
      store.markStepIncomplete('template')
    }
  }, [selectedTemplate, templates, templatesLoading, templatesError])

  const { recommended, others } = useMemo(
    () => splitRecommended(filteredTemplates, recommendedTemplates),
    [filteredTemplates, recommendedTemplates],
  )

  const handleSelect = useCallback((name: string) => {
    useSetupWizardStore.getState().selectTemplate(name)
  }, [])

  const handleToggleCompare = useCallback((name: string) => {
    const added = useSetupWizardStore.getState().toggleCompare(name)
    if (!added) {
      useToastStore.getState().add({
        variant: 'warning',
        title: 'Compare limit reached',
        description: `You can compare up to ${MAX_COMPARE} templates at a time.`,
      })
    }
  }, [])

  const handleRemoveFromCompare = useCallback((name: string) => {
    useSetupWizardStore.getState().toggleCompare(name)
  }, [])

  const comparedTemplateObjects = useMemo(
    () => templates.filter((t) => comparedTemplates.includes(t.name)),
    [templates, comparedTemplates],
  )

  const hasActiveFilters =
    searchQuery.trim() !== '' || categoryFilter !== 'all' || sizeFilter !== 'all'

  const clearFilters = useCallback(() => {
    setSearchQuery('')
    setCategoryFilter('all')
    setSizeFilter('all')
  }, [])

  const clearComparison = useCallback(() => {
    useSetupWizardStore.getState().clearComparison()
  }, [])

  const onRetry = useCallback(() => {
    // Mark fetched so the mount effect does not fire a second fetch when
    // the retry succeeds (templatesLoading -> false, templatesError -> null
    // would otherwise re-satisfy the effect guard when it never ran).
    hasFetchedRef.current = true
    void useSetupWizardStore.getState().fetchTemplates()
  }, [])

  return {
    templates, templatesLoading, templatesError, selectedTemplate, comparedTemplates,
    recommendedTemplates, availableCategories, filteredTemplates, recommended, others,
    comparedTemplateObjects, searchQuery, setSearchQuery, categoryFilter, setCategoryFilter,
    sizeFilter, setSizeFilter, hasActiveFilters, handleSelect, handleToggleCompare,
    handleRemoveFromCompare, clearFilters, clearComparison, onRetry,
  }
}
