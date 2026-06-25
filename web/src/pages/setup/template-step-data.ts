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
import {
  hasIntent,
  rankTemplates,
  type BuildGoal,
  type OversightPref,
  type RankedTemplate,
} from './template-recommendation'

export {
  GOAL_OPTIONS,
  OVERSIGHT_OPTIONS,
} from './template-recommendation'
export type { BuildGoal, OversightPref, RankedTemplate } from './template-recommendation'

export const MAX_COMPARE = 3

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

export interface TemplateStepController {
  templates: readonly TemplateInfoResponse[]
  templatesLoading: boolean
  templatesError: string | null
  selectedTemplate: string | null
  blankSelected: boolean
  comparedTemplates: readonly string[]
  availableCategories: { value: string; label: string }[]
  filteredTemplates: readonly TemplateInfoResponse[]
  matches: readonly RankedTemplate[]
  comparedTemplateObjects: readonly TemplateInfoResponse[]
  searchQuery: string
  setSearchQuery: (value: string) => void
  categoryFilter: string
  setCategoryFilter: (value: string) => void
  sizeFilter: SizeFilter
  setSizeFilter: (value: SizeFilter) => void
  buildGoal: BuildGoal
  setBuildGoal: (value: BuildGoal) => void
  oversight: OversightPref
  setOversight: (value: OversightPref) => void
  recommendationPersonalised: boolean
  hasActiveFilters: boolean
  handleSelect: (name: string) => void
  handleSelectBlank: () => void
  handleToggleCompare: (name: string) => void
  handleRemoveFromCompare: (name: string) => void
  clearFilters: () => void
  clearComparison: () => void
  onRetry: () => void
}

interface RecommendationIntentState {
  buildGoal: BuildGoal
  setBuildGoal: (value: BuildGoal) => void
  oversight: OversightPref
  setOversight: (value: OversightPref) => void
  matches: readonly RankedTemplate[]
  recommendationPersonalised: boolean
}

/** Intent state + the derived ranked matches (extracted to keep the controller
 * hook under the line cap). Matches are only meaningful once an intent is set;
 * the caller gates on ``recommendationPersonalised``. */
function useRecommendationIntent(
  templates: readonly TemplateInfoResponse[],
): RecommendationIntentState {
  const [buildGoal, setBuildGoal] = useState<BuildGoal>('any')
  const [oversight, setOversight] = useState<OversightPref>('any')
  const matches = useMemo(
    () => rankTemplates(templates, { goal: buildGoal, oversight }),
    [templates, buildGoal, oversight],
  )
  return {
    buildGoal,
    setBuildGoal,
    oversight,
    setOversight,
    matches,
    recommendationPersonalised: hasIntent({ goal: buildGoal, oversight }),
  }
}

/**
 * Fetch-on-mount + step-completion tracking + retry (extracted to keep the
 * controller hook under the line cap). Returns the retry handler, which shares
 * the fetched-once ref with the mount effect.
 */
function useTemplateLifecycle(
  templates: readonly TemplateInfoResponse[],
  templatesLoading: boolean,
  templatesError: string | null,
  selectedTemplate: string | null,
  blankSelected: boolean,
): { onRetry: () => void } {
  const hasFetchedRef = useRef(false)
  useEffect(() => {
    if (!hasFetchedRef.current && !templatesLoading && !templatesError) {
      hasFetchedRef.current = true
      void useSetupWizardStore.getState().fetchTemplates()
    }
  }, [templatesLoading, templatesError])

  // Track step completion -- validates against the full template list (not
  // filtered) so UI filters don't invalidate the selection. A blank "build it
  // yourself" choice also completes the step. Skip while loading AND while
  // not-yet-fetched (``templates`` is the slice default ``[]`` before the fetch
  // resolves): on reload ``templatesLoading`` starts false, so skipping only on
  // loading would demote a previously-selected template to incomplete every
  // mount before the list arrives.
  useEffect(() => {
    if (templatesLoading || (templates.length === 0 && !templatesError)) return
    const store = useSetupWizardStore.getState()
    const templateChosen = selectedTemplate != null && templates.some((t) => t.name === selectedTemplate)
    if (templateChosen || blankSelected) {
      store.markStepComplete('template')
    } else {
      store.markStepIncomplete('template')
    }
  }, [selectedTemplate, blankSelected, templates, templatesLoading, templatesError])

  const onRetry = useCallback(() => {
    // Mark fetched so the mount effect does not fire a second fetch when
    // the retry succeeds (templatesLoading -> false, templatesError -> null
    // would otherwise re-satisfy the effect guard when it never ran).
    hasFetchedRef.current = true
    void useSetupWizardStore.getState().fetchTemplates()
  }, [])

  return { onRetry }
}

interface TemplateComparisonState {
  comparedTemplateObjects: readonly TemplateInfoResponse[]
  handleToggleCompare: (name: string) => void
  handleRemoveFromCompare: (name: string) => void
  clearComparison: () => void
}

/** Compare-drawer handlers + the resolved compared-template objects
 * (extracted to keep the controller hook under the line cap). */
function useTemplateComparison(
  templates: readonly TemplateInfoResponse[],
  comparedTemplates: readonly string[],
): TemplateComparisonState {
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

  const clearComparison = useCallback(() => {
    useSetupWizardStore.getState().clearComparison()
  }, [])

  const comparedTemplateObjects = useMemo(
    () => templates.filter((t) => comparedTemplates.includes(t.name)),
    [templates, comparedTemplates],
  )

  return { comparedTemplateObjects, handleToggleCompare, handleRemoveFromCompare, clearComparison }
}

export function useTemplateStepController(): TemplateStepController {
  const templates = useSetupWizardStore((s) => s.templates)
  const templatesLoading = useSetupWizardStore((s) => s.templatesLoading)
  const templatesError = useSetupWizardStore((s) => s.templatesError)
  const selectedTemplate = useSetupWizardStore((s) => s.selectedTemplate)
  const blankSelected = useSetupWizardStore((s) => s.blankSelected)
  const comparedTemplates = useSetupWizardStore((s) => s.comparedTemplates)

  const [searchQuery, setSearchQuery] = useState('')
  const [categoryFilter, setCategoryFilter] = useState('all')
  const [sizeFilter, setSizeFilter] = useState<SizeFilter>('all')
  const {
    buildGoal,
    setBuildGoal,
    oversight,
    setOversight,
    matches,
    recommendationPersonalised,
  } = useRecommendationIntent(templates)

  const { onRetry } = useTemplateLifecycle(
    templates,
    templatesLoading,
    templatesError,
    selectedTemplate,
    blankSelected,
  )

  const availableCategories = useMemo(() => computeAvailableCategories(templates), [templates])
  const filteredTemplates = useMemo(
    () => filterTemplates(templates, { searchQuery, categoryFilter, sizeFilter }),
    [templates, searchQuery, categoryFilter, sizeFilter],
  )

  const hasActiveFilters =
    searchQuery.trim() !== '' || categoryFilter !== 'all' || sizeFilter !== 'all'

  const handleSelect = useCallback((name: string) => {
    useSetupWizardStore.getState().selectTemplate(name)
  }, [])
  const handleSelectBlank = useCallback(() => {
    useSetupWizardStore.getState().selectBlank()
  }, [])

  const { comparedTemplateObjects, handleToggleCompare, handleRemoveFromCompare, clearComparison } =
    useTemplateComparison(templates, comparedTemplates)

  const clearFilters = useCallback(() => {
    setSearchQuery('')
    setCategoryFilter('all')
    setSizeFilter('all')
  }, [])

  return {
    templates, templatesLoading, templatesError, selectedTemplate, blankSelected, comparedTemplates,
    availableCategories, filteredTemplates, matches,
    comparedTemplateObjects, searchQuery, setSearchQuery, categoryFilter, setCategoryFilter,
    sizeFilter, setSizeFilter, buildGoal, setBuildGoal, oversight, setOversight,
    recommendationPersonalised, hasActiveFilters, handleSelect, handleSelectBlank, handleToggleCompare,
    handleRemoveFromCompare, clearFilters, clearComparison, onRetry,
  }
}
