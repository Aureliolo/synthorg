import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { EmptyState } from '@/components/ui/empty-state'
import { InputField } from '@/components/ui/input-field'
import { SelectField } from '@/components/ui/select-field'
import { StaggerGroup, StaggerItem } from '@/components/ui/stagger-group'
import { TemplateCard } from './TemplateCard'
import { TemplateCompareDrawer } from './TemplateCompareDrawer'
import { LayoutGrid, Search, X } from 'lucide-react'
import type { TemplateInfoResponse } from '@/api/types/setup'
import {
  MAX_COMPARE,
  SIZE_OPTIONS,
  parseSizeFilter,
  useTemplateStepController,
  type SizeFilter,
} from './template-step-data'

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
    <StaggerGroup className="grid grid-cols-3 gap-grid-gap max-lg:grid-cols-2 max-sm:grid-cols-1">
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

interface TemplateFilterBarProps {
  searchQuery: string
  setSearchQuery: (value: string) => void
  categoryFilter: string
  setCategoryFilter: (value: string) => void
  sizeFilter: SizeFilter
  setSizeFilter: (value: SizeFilter) => void
  availableCategories: { value: string; label: string }[]
  hasActiveFilters: boolean
  onClearFilters: () => void
}

function TemplateFilterBar({
  searchQuery,
  setSearchQuery,
  categoryFilter,
  setCategoryFilter,
  sizeFilter,
  setSizeFilter,
  availableCategories,
  hasActiveFilters,
  onClearFilters,
}: TemplateFilterBarProps) {
  return (
    <div className="flex flex-wrap items-end gap-grid-gap">
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
        onChange={(v) => {
          const size = parseSizeFilter(v)
          if (size) setSizeFilter(size)
        }}
      />

      {/* Clear filters */}
      {hasActiveFilters && (
        <Button
          type="button"
          variant="ghost"
          size="sm"
          onClick={onClearFilters}
          className="self-end text-accent"
        >
          Clear filters
        </Button>
      )}
    </div>
  )
}

interface TemplateResultsProps {
  filteredTemplates: readonly TemplateInfoResponse[]
  recommended: readonly TemplateInfoResponse[]
  others: readonly TemplateInfoResponse[]
  selectedTemplate: string | null
  comparedTemplates: readonly string[]
  recommendedTemplates: ReadonlySet<string>
  onSelect: (name: string) => void
  onToggleCompare: (name: string) => void
  onClearFilters: () => void
}

function TemplateResults({
  filteredTemplates,
  recommended,
  others,
  selectedTemplate,
  comparedTemplates,
  recommendedTemplates,
  onSelect,
  onToggleCompare,
  onClearFilters,
}: TemplateResultsProps) {
  return (
    <>
      {filteredTemplates.length === 0 && (
        <EmptyState
          icon={LayoutGrid}
          title="No templates match"
          description="Try adjusting your filters or search query."
          action={{ label: 'Clear filters', onClick: onClearFilters }}
        />
      )}

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
            onSelect={onSelect}
            onToggleCompare={onToggleCompare}
          />
        </div>
      )}

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
            onSelect={onSelect}
            onToggleCompare={onToggleCompare}
          />
        </div>
      )}
    </>
  )
}

function TemplateStepFallback({
  templatesLoading,
  templatesError,
  onRetry,
}: {
  templatesLoading: boolean
  templatesError: string | null
  onRetry: () => void
}) {
  if (templatesLoading) {
    return (
      <div className="space-y-section-gap">
        <Skeleton className="h-6 w-48" />
        <div className="grid grid-cols-3 gap-grid-gap max-lg:grid-cols-2 max-sm:grid-cols-1">
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
        action={{ label: 'Retry', onClick: onRetry }}
      />
    )
  }

  return (
    <EmptyState
      icon={LayoutGrid}
      title="No templates available"
      description="No company templates found. Check your template directory."
    />
  )
}

export function TemplateStep() {
  const c = useTemplateStepController()

  if (c.templatesLoading || c.templatesError || c.templates.length === 0) {
    return (
      <TemplateStepFallback
        templatesLoading={c.templatesLoading}
        templatesError={c.templatesError}
        onRetry={c.onRetry}
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

      <TemplateFilterBar
        searchQuery={c.searchQuery}
        setSearchQuery={c.setSearchQuery}
        categoryFilter={c.categoryFilter}
        setCategoryFilter={c.setCategoryFilter}
        sizeFilter={c.sizeFilter}
        setSizeFilter={c.setSizeFilter}
        availableCategories={c.availableCategories}
        hasActiveFilters={c.hasActiveFilters}
        onClearFilters={c.clearFilters}
      />

      <TemplateResults
        filteredTemplates={c.filteredTemplates}
        recommended={c.recommended}
        others={c.others}
        selectedTemplate={c.selectedTemplate}
        comparedTemplates={c.comparedTemplates}
        recommendedTemplates={c.recommendedTemplates}
        onSelect={c.handleSelect}
        onToggleCompare={c.handleToggleCompare}
        onClearFilters={c.clearFilters}
      />

      <TemplateCompareDrawer
        open={c.comparedTemplates.length >= 2}
        onClose={c.clearComparison}
        templates={c.comparedTemplateObjects}
        onSelect={(name) => {
          c.handleSelect(name)
          c.clearComparison()
        }}
        onRemove={c.handleRemoveFromCompare}
      />
    </div>
  )
}
