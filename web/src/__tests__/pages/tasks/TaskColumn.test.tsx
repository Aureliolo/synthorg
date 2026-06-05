import { render, screen } from '@testing-library/react'
import { DndContext } from '@dnd-kit/core'
import { TaskColumn } from '@/pages/tasks/TaskColumn'
import { KANBAN_COLUMNS, type KanbanColumn } from '@/utils/tasks'
import { makeTask } from '../../helpers/factories'
import type { Task } from '@/api/types/tasks'

const foundColumn = KANBAN_COLUMNS.find((c) => c.id === 'in_progress')
if (!foundColumn) throw new Error('Missing in_progress column in KANBAN_COLUMNS')
const inProgressColumn: KanbanColumn = foundColumn

function renderColumn(tasks: Task[] = [], onSelectTask = vi.fn()) {
  return render(
    <DndContext>
      <TaskColumn column={inProgressColumn} tasks={tasks} onSelectTask={onSelectTask} />
    </DndContext>,
  )
}

describe('TaskColumn', () => {
  it('renders column header with label', () => {
    renderColumn()
    expect(screen.getByText('In Progress')).toBeInTheDocument()
  })

  it('renders task count badge', () => {
    const tasks = [
      makeTask('t1', 'Task 1'),
      makeTask('t2', 'Task 2'),
    ]
    renderColumn(tasks)
    expect(screen.getByText('2')).toBeInTheDocument()
  })

  it('renders zero count when no tasks', () => {
    renderColumn([])
    expect(screen.getByText('0')).toBeInTheDocument()
  })

  it('renders task cards', () => {
    const tasks = [
      makeTask('t1', 'First task'),
      makeTask('t2', 'Second task'),
    ]
    renderColumn(tasks)
    expect(screen.getByText('First task')).toBeInTheDocument()
    expect(screen.getByText('Second task')).toBeInTheDocument()
  })

  it('renders empty state when no tasks', () => {
    renderColumn([])
    expect(screen.getByText('No tasks')).toBeInTheDocument()
  })

  it('renders data-column-id attribute', () => {
    const { container } = renderColumn()
    expect(container.querySelector('[data-column-id="in_progress"]')).toBeInTheDocument()
  })
})
