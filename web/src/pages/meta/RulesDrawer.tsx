import { Plus } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { ConfirmDialog } from '@/components/ui/confirm-dialog'
import { Drawer } from '@/components/ui/drawer'
import { EmptyState } from '@/components/ui/empty-state'
import { StaggerGroup, StaggerItem } from '@/components/ui/stagger-group'
import type {
  MetricDescriptor,
  RuleListItem as RuleListItemType,
} from '@/api/endpoints/custom-rules'

import { RuleBuilderForm } from './RuleBuilderForm'
import { RuleListItem } from './RuleListItem'
import { useRulesDrawerState, type RulesDrawerState } from './useRulesDrawerState'

interface RulesDrawerProps {
  open: boolean
  onClose: () => void
  allRules: readonly RuleListItemType[]
  metrics: readonly MetricDescriptor[]
  onRefresh: () => Promise<void>
}

export function RulesDrawer({
  open,
  onClose,
  allRules,
  metrics,
  onRefresh,
}: RulesDrawerProps) {
  const state = useRulesDrawerState({ onClose, onRefresh })
  const builtinRules = allRules.filter((r) => r.type === 'builtin')
  const customRulesList = allRules.filter((r) => r.type === 'custom')

  return (
    <>
      <Drawer open={open} onClose={state.handleDrawerClose} title="Signal Rules">
        {state.view === 'builder' ? (
          <RuleBuilderForm
            editRule={state.editRule}
            metrics={metrics}
            onClose={state.handleBuilderClose}
          />
        ) : (
          <RulesDrawerListView
            state={state}
            allRulesCount={allRules.length}
            builtinRules={builtinRules}
            customRulesList={customRulesList}
          />
        )}
      </Drawer>

      <ConfirmDialog
        open={state.deleteTarget !== null}
        onOpenChange={(next) => {
          if (!next) state.handleDeleteCancel()
        }}
        onConfirm={state.handleDeleteConfirm}
        title="Delete Custom Rule"
        description="This rule will be permanently deleted. This action cannot be undone."
        variant="destructive"
        confirmLabel="Delete"
        loading={state.deleting}
      />
    </>
  )
}

interface RulesDrawerListViewProps {
  state: RulesDrawerState
  allRulesCount: number
  builtinRules: readonly RuleListItemType[]
  customRulesList: readonly RuleListItemType[]
}

function RulesDrawerListView({
  state,
  allRulesCount,
  builtinRules,
  customRulesList,
}: RulesDrawerListViewProps) {
  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center justify-between">
        <p className="text-body-sm text-muted-foreground">
          {allRulesCount} rules configured
        </p>
        <Button size="sm" onClick={state.handleCreateClick}>
          <Plus className="mr-1 size-3.5" />
          Create Rule
        </Button>
      </div>

      {allRulesCount === 0 && (
        <EmptyState
          title="No rules"
          description="Create a custom rule to monitor your org signals."
        />
      )}

      {builtinRules.length > 0 && (
        <RuleSection title={`Built-in (${builtinRules.length})`}>
          {builtinRules.map((rule) => (
            <StaggerItem key={rule.name}>
              <RuleListItem rule={rule} />
            </StaggerItem>
          ))}
        </RuleSection>
      )}

      {customRulesList.length > 0 && (
        <RuleSection title={`Custom (${customRulesList.length})`}>
          {customRulesList.map((rule) => (
            <StaggerItem key={rule.id ?? rule.name}>
              <RuleListItem
                rule={rule}
                onToggle={state.handleToggle}
                onEdit={state.handleEditClick}
                onDelete={state.handleDeleteRequest}
              />
            </StaggerItem>
          ))}
        </RuleSection>
      )}
    </div>
  )
}

interface RuleSectionProps {
  title: string
  children: React.ReactNode
}

function RuleSection({ title, children }: RuleSectionProps) {
  return (
    <section>
      <h4 className="mb-2 text-body-sm font-medium text-muted-foreground">{title}</h4>
      <StaggerGroup className="flex flex-col gap-2">{children}</StaggerGroup>
    </section>
  )
}
