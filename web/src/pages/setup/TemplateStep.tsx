import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { EmptyState } from '@/components/ui/empty-state'
import { InputField } from '@/components/ui/input-field'
import { SelectField } from '@/components/ui/select-field'
import { StaggerGroup, StaggerItem } from '@/components/ui/stagger-group'
import { useSetupWizardStore } from '@/stores/setup-wizard'
import { useToastStore } from '@/stores/toast'
import { TemplateCard } from './TemplateCard'
import { TemplateCompareDrawer } from './TemplateCompareDrawer'
import { LayoutGrid, Search, X } from 'lucide-react'
import type { TemplateInfoResponse } from '@/api/types/setup'
import {
  CATEGORY_ORDER,
  deriveCategoryFromTags,
  getCategoryLabel,
} from '@/utils/template-categories'

const MAX_COMPARE = 3

/** Template size tags used for recommendation heuristics. */
const TAG_SOLO = 'solo'
const TAG_SMALL_TEAM = 'small-team'

/** Agent-count filter buckets. */
type SizeFilter = 'all' | 'small' | 'medium' | 'large'

const SIZE_OPTIONS: readonly { value: SizeFilter; label: string }[] = [
  { value: 'all', label: 'Any size' },
  { value: 'small', label: '1-3 agents' },
  { value: 'medium', label: '4-8 agents' },
  { value: 'large', label: '9+ agents' },
]

function matchesSize(template: TemplateInfoResponse, size: SizeFilter): boolean {
  if (size === 'all') return true
  const count = template.agent_count
  if (size === 'small') return count >= 1 && count <= 3
  if (size === 'medium') return count >= 4 && count <= 8
  return count >= 9
}

interface TemplateGridItemProps {
  template: TemplateInfoResponse
  selected: boolean
  compared: boolean
  recommended: boolean
  onSelect: () => void
  onToggleCompare: () => void
  compareDisabled: boolean
}

function TemplateGridItem({ template, selected, compared, recommended, onSelect, onToggleCompare, compareDisabled }: TemplateGridItemProps) {
  return (
    <StaggerItem>
      <TemplateCard
        template={template}
        selected={selected}
        compared={compared}
        recommended={recommended}
        onSelect={onSelect}
        onToggleCompare={onToggleCompare}
        compareDisabled={compareDisabled}
      />
    </StaggerItem>
  )
}

function TemplateGrid({
  templates,
  selectedTemplate,
  comparedTemplates,
  recommendedTemplates,
  onSelect,
  onToggleCompare,
}: {
  templates: readonly TemplateInfoResponse[]
  selectedTemplate: string | null
  comparedTemplates: readonly string[]
  recommendedTemplates: ReadonlySet<string>
  onSelect: (name: string) => void
  onToggleCompare: (name: string) => void
}) {
  return (
    <StaggerGroup className="grid grid-cols-3 gap-grid-gap max-[1023px]:grid-cols-2 max-[639px]:grid-cols-1">
      {templates.map((template) => (
        <TemplateGridItem
          key={template.name}
          template={template}
          selected={selectedTemplate === template.name}
          compared={comparedTemplates.includes(template.name)}
          recommended={recommendedTemplates.has(template.name)}
          onSelect={() => onSelect(template.name)}
          onToggleCompare={() => onToggleCompare(template.name)}
          compareDisabled={comparedTemplates.length >= MAX_COMPARE}
        />
      ))}
    </StaggerGroup>
  )
}

export function TemplateStep() {
  const templates = useSetupWizardStore((s) => s.templates)
  const templatesLoading = useSetupWizardStore((s) => s.templatesLoading)
  const templatesError = useSetupWizardStore((s) => s.templatesError)
  const selectedTemplate = useSetupWizardStore((s) => s.selectedTemplate)
  const comparedTemplates = useSetupWizardStore((s) => s.comparedTemplates)
  const fetchTemplates = useSetupWizardStore((s) => s.fetchTemplates)
  const selectTemplate = useSetupWizardStore((s) => s.selectTemplate)
  const toggleCompare = useSetupWizardStore((s) => s.toggleCompare)
  const clearComparison = useSetupWizardStore((s) => s.clearComparison)
  const markStepComplete = useSetupWizardStore((s) => s.markStepComplete)
  const markStepIncomplete = useSetupWizardStore((s) => s.markStepIncomplete)

  // Filter state
  const [searchQuery, setSearchQuery] = useState('')
  const [categoryFilter, setCategoryFilter] = useState('all')
  const [sizeFilter, setSizeFilter] = useState<SizeFilter>('all')

  const hasFetchedRef = useRef(false)
  useEffect(() => {
    if (!hasFetchedRef.current && !templatesLoading && !templatesError) {
      hasFetchedRef.current = true
      void fetchTemplates()
    }
  }, [templatesLoading, templatesError, fetchTemplates])

  // Recommended templates are derived from tags alone. The recommendation
  // surfaces approachable starting points (solo / small-team / startup / mvp)
  // so first-time users see a manageable shape before scrolling the full
  // grid.
  const recommendedTemplates = useMemo(() => {
    const recommended = new Set<string>()
    const smallTags = new Set([TAG_SOLO, TAG_SMALL_TEAM, 'startup', 'mvp'])

    for (const template of templates) {
      if (template.tags.some((tag) => smallTags.has(tag))) {
        recommended.add(template.name)
      }
    }
    return recommended
  }, [templates])

  // Available categories (only those present in templates)
  const availableCategories = useMemo(() => {
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
  }, [templates])

  // Filtered templates
  const filteredTemplates = useMemo(() => {
    const query = searchQuery.trim().toLowerCase()
    return templates.filter((t) => {
      if (categoryFilter !== 'all' && deriveCategoryFromTags(t.tags) !== categoryFilter) {
        return false
      }
      if (!matchesSize(t, sizeFilter)) return false
      if (query) {
        const keywords = query.split(' ').filter(Boolean)
        if (keywords.length > 0) {
          const haystack = `${t.display_name} ${t.description} ${t.tags.join(' ')} ${t.workflow} ${t.autonomy_level}`.toLowerCase()
          if (!keywords.every((kw) => haystack.includes(kw))) return false
        }
      }
      return true
    })
  }, [templates, searchQuery, categoryFilter, sizeFilter])

  // Track step completion -- validates against the full template list (not
  // filtered) so UI filters don't invalidate the selection. Skip while
  // loading to avoid false negatives from an empty templates array.
  useEffect(() => {
    if (templatesLoading) return
    if (selectedTemplate && templates.some((t) => t.name === selectedTemplate)) {
      markStepComplete('template')
    } else {
      markStepIncomplete('template')
    }
  }, [selectedTemplate, templates, templatesLoading, markStepComplete, markStepIncomplete])

  // Split into recommended and others
  const { recommended, others } = useMemo(() => {
    const rec: TemplateInfoResponse[] = []
    const oth: TemplateInfoResponse[] = []
    for (const t of filteredTemplates) {
      if (recommendedTemplates.has(t.name)) {
        rec.push(t)
      } else {
        oth.push(t)
      }
    }
    return { recommended: rec, others: oth }
  }, [filteredTemplates, recommendedTemplates])

  const handleSelect = useCallback(selectTemplate, [selectTemplate])

  const handleToggleCompare = useCallback(
    (name: string) => {
      const added = toggleCompare(name)
      if (!added) {
        useToastStore.getState().add({
          variant: 'warning',
          title: 'Compare limit reached',
          description: `You can compare up to ${MAX_COMPARE} templates at a time.`,
        })
      }
    },
    [toggleCompare],
  )

  const handleRemoveFromCompare = useCallback(toggleCompare, [toggleCompare])

  const comparedTemplateObjects = useMemo(
    () => templates.filter((t) => comparedTemplates.includes(t.name)),
    [templates, comparedTemplates],
  )

  const hasActiveFilters = searchQuery.trim() !== '' || categoryFilter !== 'all' || sizeFilter !== 'all'

  if (templatesLoading) {
    return (
      <div className="space-y-section-gap">
        <Skeleton className="h-6 w-48" />
        <div className="grid grid-cols-3 gap-grid-gap">
          {Array.from({ length: 6 }, (_, i) => (
            <Skeleton key={i} className="h-48 rounded-lg" />
          ))}
        </div>
      </div>
    )
  }

  if (templatesError) {
    return (
      <EmptyState
        title="Failed to load templates"
        description={templatesError}
        action={{ label: 'Retry', onClick: () => void fetchTemplates() }}
      />
    )
  }

  if (templates.length === 0) {
    return (
      <EmptyState
        icon={LayoutGrid}
        title="No templates available"
        description="No company templates found. Check your template directory."
      />
    )
  }

  return (
    <div className="space-y-section-gap">
      <div className="space-y-2">
        <h2 className="text-lg font-semibold text-foreground">Choose a Template</h2>
        <p className="text-sm text-muted-foreground">
          Select a template to start building your organization.
        </p>
      </div>

      {/* Filter bar */}
      <div className="flex flex-wrap items-end gap-3">
        <div className="flex-1 min-w-52 max-w-xs">
          <InputField
            label="Search"
            value={searchQuery}
            onValueChange={setSearchQuery}
            placeholder="Search templates..."
            leadingIcon={<Search className="size-3.5" />}
            trailingElement={
              searchQuery ? (
                <Button
                  type="button"
                  variant="ghost"
                  size="icon-xs"
                  onClick={() => setSearchQuery('')}
                  aria-label="Clear search"
                >
                  <X className="size-3.5" aria-hidden="true" />
                </Button>
              ) : undefined
            }
          />
        </div>

        {/* Category filter */}
        <SelectField
          label="Category"
          options={availableCategories}
          value={categoryFilter}
          onChange={setCategoryFilter}
        />

        {/* Size filter */}
        <SelectField
          label="Size"
          options={SIZE_OPTIONS}
          value={sizeFilter}
          onChange={(v) => setSizeFilter(v as SizeFilter)}
        />

        {/* Clear filters */}
        {hasActiveFilters && (
          <button
            type="button"
            onClick={() => {
              setSearchQuery('')
              setCategoryFilter('all')
              setSizeFilter('all')
            }}
            className="self-end pb-1 text-xs text-accent hover:underline"
          >
            Clear filters
          </button>
        )}
      </div>

      {/* No results after filtering */}
      {filteredTemplates.length === 0 && (
        <EmptyState
          icon={LayoutGrid}
          title="No templates match"
          description="Try adjusting your filters or search query."
          action={{
            label: 'Clear filters',
            onClick: () => {
              setSearchQuery('')
              setCategoryFilter('all')
              setSizeFilter('all')
            },
          }}
        />
      )}

      {/* Recommended section */}
      {recommended.length > 0 && (
        <div className="space-y-section-gap">
          <div className="flex items-center gap-2">
            <h3 className="text-sm font-semibold text-foreground">Recommended</h3>
            <span className="rounded-full bg-accent/10 px-2 py-0.5 text-compact font-medium text-accent">
              {recommended.length}
            </span>
          </div>
          <TemplateGrid
            templates={recommended}
            selectedTemplate={selectedTemplate}
            comparedTemplates={comparedTemplates}
            recommendedTemplates={recommendedTemplates}
            onSelect={handleSelect}
            onToggleCompare={handleToggleCompare}
          />
        </div>
      )}

      {/* Others section */}
      {others.length > 0 && (
        <div className="space-y-section-gap">
          {recommended.length > 0 && (
            <h3 className="text-sm font-semibold text-muted-foreground">Other Templates</h3>
          )}
          <TemplateGrid
            templates={others}
            selectedTemplate={selectedTemplate}
            comparedTemplates={comparedTemplates}
            recommendedTemplates={recommendedTemplates}
            onSelect={handleSelect}
            onToggleCompare={handleToggleCompare}
          />
        </div>
      )}

      <TemplateCompareDrawer
        open={comparedTemplates.length >= 2}
        onClose={clearComparison}
        templates={comparedTemplateObjects}
        onSelect={(name) => {
          handleSelect(name)
          clearComparison()
        }}
        onRemove={handleRemoveFromCompare}
      />
    </div>
  )
}
