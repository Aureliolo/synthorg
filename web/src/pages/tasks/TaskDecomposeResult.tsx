import { SectionCard } from '@/components/ui/section-card'
import { StatPill } from '@/components/ui/stat-pill'
import type { DecompositionResult, SubtaskDefinition } from '@/api/types'

function MetaTag({ children }: { children: string }) {
  return (
    <span className="rounded-md border border-border px-2 py-0.5 text-xs text-muted-foreground">
      {children}
    </span>
  )
}

function SubtaskResultItem({ subtask }: { subtask: SubtaskDefinition }) {
  return (
    <li className="rounded-md border border-border p-card text-sm">
      <div className="font-medium text-foreground">{subtask.title}</div>
      <div className="text-muted-foreground">{subtask.description}</div>
      <div className="mt-2 flex flex-wrap gap-2">
        <MetaTag>{subtask.estimated_complexity}</MetaTag>
        <MetaTag>{subtask.stakes}</MetaTag>
        {subtask.dependencies.length > 0 && (
          <MetaTag>{`${String(subtask.dependencies.length)} deps`}</MetaTag>
        )}
      </div>
    </li>
  )
}

export interface TaskDecomposeResultProps {
  result: DecompositionResult
}

export function TaskDecomposeResult({ result }: TaskDecomposeResultProps) {
  const { plan, created_tasks, dependency_edges } = result
  return (
    <SectionCard title="Decomposition result">
      <div className="space-y-section-gap">
        <div className="flex flex-wrap gap-3">
          <StatPill label="Structure" value={plan.task_structure} />
          <StatPill label="Subtasks" value={created_tasks.length} />
          <StatPill label="Edges" value={dependency_edges.length} />
        </div>
        <ul className="space-y-2" aria-label="Planned subtasks">
          {plan.subtasks.map((subtask) => (
            <SubtaskResultItem key={subtask.id} subtask={subtask} />
          ))}
        </ul>
      </div>
    </SectionCard>
  )
}
