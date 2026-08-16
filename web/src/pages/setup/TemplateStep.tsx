import { useState } from 'react'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { EmptyState } from '@/components/ui/empty-state'
import { InputField } from '@/components/ui/input-field'
import { SelectField } from '@/components/ui/select-field'
import { StaggerGroup, StaggerItem } from '@/components/ui/stagger-group'
import { TemplateCard } from './TemplateCard'
import { TemplateCompareDialog } from './TemplateCompareDialog'
import { IntentChips } from './RecommendIntentChips'
import { MatchesGrid } from './TemplateMatches'
import { Check, LayoutGrid, Plus, Search, X } from 'lucide-react'
import { cn } from '@/lib/utils'
import type { TemplateInfoResponse } from '@/api/types/setup'
import {
  MAX_COMPARE,
  SIZE_OPTIONS,
  parseSizeFilter,
  useTemplateStepController,
} from './template-step-data'

type Controller = ReturnType<typeof useTemplateStepController>

function TemplateGrid({
  templates,
  selectedTemplate,
  comparedTemplates,
  onSelect,
  onToggleCompare,
}: {
  templates: readonly TemplateInfoResponse[]
  selectedTemplate: string | null
  comparedTemplates: readonly string[]
  onSelect: (name: string) => void
  onToggleCompare: (name: string) => void
}) {
  return (
    <StaggerGroup className="grid grid-cols-1 gap-grid-gap sm:grid-cols-2 lg:grid-cols-3">
      {templates.map((template) => (
        <StaggerItem key={template.name}>
          <TemplateCard
            template={template}
            selected={selectedTemplate === template.name}
            compared={comparedTemplates.includes(template.name)}
            onSelect={() => onSelect(template.name)}
            onToggleCompare={() => onToggleCompare(template.name)}
            compareDisabled={comparedTemplates.length >= MAX_COMPARE}
          />
        </StaggerItem>
      ))}
    </StaggerGroup>
  )
}

function TemplateFilterBar(c: Controller) {
  return (
    <div className="flex flex-wrap items-end gap-grid-gap">
      <div className="min-w-52 max-w-xs flex-1">
        <InputField
          label="Search"
          value={c.searchQuery}
          onValueChange={c.setSearchQuery}
          placeholder="Search templates..."
          leadingIcon={<Search className="size-3.5" />}
          trailingElement={
            c.searchQuery ? (
              <Button
                type="button"
                variant="ghost"
                size="icon-xs"
                onClick={() => c.setSearchQuery('')}
                aria-label="Clear search"
              >
                <X className="size-3.5" aria-hidden="true" />
              </Button>
            ) : undefined
          }
        />
      </div>
      <SelectField
        label="Category"
        options={c.availableCategories}
        value={c.categoryFilter}
        onChange={c.setCategoryFilter}
      />
      <SelectField
        label="Size"
        options={SIZE_OPTIONS}
        value={c.sizeFilter}
        onChange={(v) => {
          const size = parseSizeFilter(v)
          if (size) c.setSizeFilter(size)
        }}
      />
      {c.hasActiveFilters && (
        <Button
          type="button"
          variant="ghost"
          size="sm"
          onClick={c.clearFilters}
          className="self-end text-accent"
        >
          Clear filters
        </Button>
      )}
    </div>
  )
}

/**
 * Quiet secondary-action row: browse the full catalogue, or start from a blank
 * organisation. Consistent button language (no boxes) so it reads as one
 * footer rather than competing cards.
 */
function SecondaryActions({
  count,
  onBrowse,
  blankSelected,
  onSelectBlank,
}: {
  count: number
  onBrowse: () => void
  blankSelected: boolean
  onSelectBlank: () => void
}) {
  return (
    <div className="flex flex-wrap items-center gap-2">
      <Button variant="outline" size="sm" onClick={onBrowse}>
        Browse all {count} templates
      </Button>
      <Button
        variant={blankSelected ? 'outline' : 'ghost'}
        size="sm"
        onClick={onSelectBlank}
        aria-pressed={blankSelected}
        className={cn('gap-1.5', blankSelected && 'border-accent text-accent')}
      >
        {blankSelected ? (
          <Check className="size-3.5" aria-hidden="true" />
        ) : (
          <Plus className="size-3.5" aria-hidden="true" />
        )}
        {blankSelected ? 'Blank organisation' : 'Start from scratch'}
      </Button>
    </div>
  )
}

/**
 * Persistent selection summary for comparison. Lets the user pick up to `max`
 * templates (via each card's Compare button) and open the comparison modal
 * deliberately, rather than it auto-opening at two.
 */
function CompareTray({
  templates,
  max,
  onRemove,
  onClear,
  onCompare,
}: {
  templates: readonly TemplateInfoResponse[]
  max: number
  onRemove: (name: string) => void
  onClear: () => void
  onCompare: () => void
}) {
  return (
    <div className="flex flex-wrap items-center gap-2 rounded-lg border border-border bg-card/60 p-card">
      <span className="text-xs font-medium text-muted-foreground">Comparing</span>
      {templates.map((t) => (
        <span
          key={t.name}
          className="inline-flex items-center gap-0.5 rounded-full border border-border bg-surface py-0.5 pl-2.5 pr-1 text-xs text-foreground"
        >
          {t.display_name}
          <Button
            type="button"
            variant="ghost"
            size="icon-xs"
            onClick={() => onRemove(t.name)}
            aria-label={`Remove ${t.display_name} from comparison`}
          >
            <X className="size-3" aria-hidden="true" />
          </Button>
        </span>
      ))}
      <span className="text-xs text-muted-foreground">
        {templates.length}/{max}
      </span>
      <div className="ml-auto flex items-center gap-2">
        <Button variant="ghost" size="sm" onClick={onClear}>
          Clear
        </Button>
        <Button size="sm" disabled={templates.length < 2} onClick={onCompare}>
          Compare {templates.length}
        </Button>
      </div>
    </div>
  )
}

function BrowseSection({ c, onClose }: { c: Controller; onClose: () => void }) {
  return (
    <div className="space-y-section-gap">
      <div className="flex items-center justify-between gap-2">
        <h3 className="text-sm font-semibold text-foreground">All templates</h3>
        <Button variant="ghost" size="sm" onClick={onClose}>
          Hide
        </Button>
      </div>
      <TemplateFilterBar {...c} />
      {c.filteredTemplates.length === 0 ? (
        <EmptyState
          icon={LayoutGrid}
          title="No templates match"
          description="Try adjusting your filters or search query."
          action={{ label: 'Clear filters', onClick: c.clearFilters }}
        />
      ) : (
        <TemplateGrid
          templates={c.filteredTemplates}
          selectedTemplate={c.selectedTemplate}
          comparedTemplates={c.comparedTemplates}
          onSelect={c.handleSelect}
          onToggleCompare={c.handleToggleCompare}
        />
      )}
    </div>
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
        <div className="grid grid-cols-1 gap-grid-gap sm:grid-cols-2 lg:grid-cols-3">
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

/** Body below the intent chips: browse catalogue, matches, or a slim hint --
 *  followed by the shared secondary-action row. */
function TemplateStepBody({ c, browseOpen, setBrowseOpen }: {
  c: Controller
  browseOpen: boolean
  setBrowseOpen: (open: boolean) => void
}) {
  if (browseOpen) {
    return <BrowseSection c={c} onClose={() => setBrowseOpen(false)} />
  }
  return (
    <div className="space-y-section-gap">
      {c.recommendationPersonalised ? (
        <MatchesGrid
          matches={c.matches}
          selectedTemplate={c.selectedTemplate}
          comparedTemplates={c.comparedTemplates}
          onSelect={c.handleSelect}
          onToggleCompare={c.handleToggleCompare}
        />
      ) : (
        <p className="text-sm text-muted-foreground">
          Pick an option above to see your matches, or browse the full catalogue.
        </p>
      )}
      <SecondaryActions
        count={c.templates.length}
        onBrowse={() => setBrowseOpen(true)}
        blankSelected={c.blankSelected}
        onSelectBlank={c.handleSelectBlank}
      />
    </div>
  )
}

export function TemplateStep() {
  const c = useTemplateStepController()
  const [browseOpen, setBrowseOpen] = useState(false)
  const [compareOpen, setCompareOpen] = useState(false)

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
      <div className="max-w-2xl space-y-2">
        <h2 className="text-lg font-semibold text-foreground">Choose a Template</h2>
        <p className="text-sm text-muted-foreground">
          Select a template to start building your organisation.
        </p>
      </div>

      <IntentChips
        buildGoal={c.buildGoal}
        setBuildGoal={c.setBuildGoal}
        oversight={c.oversight}
        setOversight={c.setOversight}
      />

      {c.comparedTemplates.length > 0 && (
        <CompareTray
          templates={c.comparedTemplateObjects}
          max={MAX_COMPARE}
          onRemove={c.handleRemoveFromCompare}
          onClear={c.clearComparison}
          onCompare={() => setCompareOpen(true)}
        />
      )}

      <TemplateStepBody c={c} browseOpen={browseOpen} setBrowseOpen={setBrowseOpen} />

      <TemplateCompareDialog
        open={compareOpen}
        onClose={() => setCompareOpen(false)}
        templates={c.comparedTemplateObjects}
        onSelect={(name) => {
          c.handleSelect(name)
          c.clearComparison()
          setCompareOpen(false)
        }}
        onRemove={c.handleRemoveFromCompare}
      />
    </div>
  )
}
