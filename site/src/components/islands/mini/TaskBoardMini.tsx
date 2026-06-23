import React, { useMemo } from "react";

interface Props {
  tick: number;
}

interface TaskCard {
  id: number;
  title: string;
  priority: "critical" | "high" | "medium" | "low";
  assignee: string;
}

// All tasks in the system; they cycle through columns based on tick
const allTasks: TaskCard[] = [
  { id: 1, title: "Init project", priority: "medium", assignee: "CTO" },
  { id: 2, title: "Setup CI pipeline", priority: "high", assignee: "CTO" },
  { id: 3, title: "Build auth module", priority: "high", assignee: "E1" },
  { id: 4, title: "Design landing page", priority: "medium", assignee: "UX" },
  { id: 5, title: "Write API docs", priority: "low", assignee: "E2" },
  { id: 6, title: "Perf benchmarks", priority: "medium", assignee: "QA" },
  { id: 7, title: "Add logging", priority: "low", assignee: "E1" },
  { id: 8, title: "Schema validation", priority: "high", assignee: "E2" },
];

// Column definitions: each task progresses through columns over time
const columnNames = ["Backlog", "In Progress", "In Review", "Done"];
const wipLimits: Record<string, number> = { "In Progress": 3 };

// Memoised: the board recomputes every tick, but each task object keeps a
// stable identity, so an unchanged card skips re-rendering.
const MiniCard = React.memo(function MiniCard({ task }: { task: TaskCard }) {
  return (
    <div className="rounded-md p-2 border dp-bg-card dp-bd">
      <div className="flex items-center gap-1.5 mb-1">
        <span
          className="w-1.5 h-1.5 rounded-full shrink-0 dp-prio"
          data-priority={task.priority}
        />
        <span className="text-xs truncate dp-fg-primary">{task.title}</span>
      </div>
      <div className="flex items-center gap-1">
        <span className="w-3.5 h-3.5 rounded-full text-[6px] flex items-center justify-center font-bold dp-avatar">
          {task.assignee[0]}
        </span>
        <span className="text-[8px] dp-fg-muted">{task.assignee}</span>
      </div>
    </div>
  );
});

export default function TaskBoardMini({ tick }: Props) {
  // Each task progresses through columns at different rates based on its id
  // This creates a natural-looking board where tasks move at different speeds
  const columns = useMemo(() => {
    const cols: Record<string, TaskCard[]> = {
      Backlog: [],
      "In Progress": [],
      "In Review": [],
      Done: [],
    };

    for (const task of allTasks) {
      // Each task has an offset based on id, so they don't all move together
      const progress = Math.floor((tick + task.id * 3) / 4) % 8;
      // Map progress 0-7 to column index: spend varying time in each column
      let colIdx: number;
      if (progress < 2) colIdx = 0;      // Backlog
      else if (progress < 4) colIdx = 1;  // In Progress
      else if (progress < 5) colIdx = 2;  // In Review
      else colIdx = 3;                     // Done

      cols[columnNames[colIdx]].push(task);
    }

    return cols;
  }, [tick]);

  return (
    <div className="w-full">
      <div className="grid grid-cols-4 gap-2 px-1">
        {columnNames.map((name) => {
          const tasks = columns[name];
          const wip = wipLimits[name];
          return (
            <div key={name}>
              <div className="flex items-center justify-between mb-2 px-1">
                <span className="text-xs font-semibold dp-fg-secondary">{name}</span>
                <span className="flex items-center gap-1">
                  <span className="text-[10px] px-1 rounded dp-chip">{tasks.length}</span>
                  {wip && (
                    <span className="text-[8px] px-1 rounded dp-wip">WIP {wip}</span>
                  )}
                </span>
              </div>
              <div className="space-y-1.5">
                {tasks.map((task) => (
                  <MiniCard key={task.id} task={task} />
                ))}
              </div>
            </div>
          );
        })}
      </div>
      <div className="mt-3 text-center">
        <span className="text-xs px-2 py-0.5 rounded-full border inline-block dp-pill">
          Kanban, Agile sprints, or sequential pipelines
        </span>
      </div>
    </div>
  );
}
