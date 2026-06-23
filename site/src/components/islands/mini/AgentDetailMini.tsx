import React from "react";

interface Props {
  tick: number;
}

const tools = ["file_system", "git", "code_runner", "web_search", "database"];

type Tone = "primary" | "success" | "warning" | "accent";

const activities: { time: string; action: string; tone: Tone }[] = [
  { time: "2m ago", action: "Completed task #12: Schema validation", tone: "success" },
  { time: "5m ago", action: "Delegated subtask to Engineer", tone: "accent" },
  { time: "8m ago", action: "Approved PR #47 (quality: 94%)", tone: "success" },
  { time: "12m ago", action: "Started task #11: API design", tone: "accent" },
  { time: "15m ago", action: "Meeting: sprint planning (chair)", tone: "success" },
  { time: "20m ago", action: "Budget alert: 72% daily used", tone: "warning" },
];

export default function AgentDetailMini({ tick }: Props) {
  const cycle = tick % 20;
  const tasksCompleted = 47 + Math.floor(cycle / 4);
  const costToday = (12.4 + cycle * 0.15).toFixed(2);

  return (
    <div className="w-full px-2">
      {/* Agent header */}
      <div className="flex items-center gap-3 mb-4">
        <div className="w-10 h-10 rounded-full flex items-center justify-center text-sm font-bold dp-pulse dp-avatar-lg">
          SC
        </div>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2">
            <span className="text-sm font-semibold dp-fg-primary">Sarah Chen</span>
            <span className="text-xs px-1.5 py-0.5 rounded-full font-medium dp-badge-success">
              Active
            </span>
          </div>
          <div className="flex items-center gap-2">
            <span className="text-xs dp-fg-secondary">CTO &middot; C-Suite</span>
            <span className="text-xs dp-fg-muted">Semi-Autonomous</span>
          </div>
        </div>
      </div>

      {/* Metrics grid */}
      <div className="grid grid-cols-4 gap-2 mb-3">
        {[
          { label: "Tasks Done", value: String(tasksCompleted), tone: "accent" as Tone },
          { label: "Quality", value: "94%", tone: "success" as Tone },
          { label: "Cost Today", value: `EUR ${costToday}`, tone: "warning" as Tone },
          { label: "Trust", value: "Semi-Auto", tone: "accent" as Tone },
        ].map((m) => (
          <div key={m.label} className="rounded-md p-2 text-center border dp-bg-card dp-bd">
            <div className="text-xs mb-0.5 dp-fg-muted">{m.label}</div>
            <div className="text-sm font-semibold dp-mono dp-tone" data-tone={m.tone}>
              {m.value}
            </div>
          </div>
        ))}
      </div>

      {/* Tool badges */}
      <div className="flex flex-wrap gap-1 mb-3">
        {tools.map((t) => (
          <span key={t} className="text-xs px-1.5 py-0.5 rounded dp-tag">
            {t}
          </span>
        ))}
      </div>

      {/* Activity log */}
      <div className="rounded-md overflow-hidden border dp-bg-card dp-bd">
        <div className="px-2 py-1.5 border-b dp-bd">
          <span className="text-xs font-semibold dp-fg-secondary">Recent Activity</span>
        </div>
        <div className="h-[72px] overflow-hidden relative">
          <div className="dp-activity-scroll">
            {/* Duplicated once for a seamless infinite-scroll loop; key by the
                copy half plus the within-half index so duplicate ``time``
                strings cannot collide. */}
            {[...activities, ...activities].map((a, i) => (
              <div key={`${i < activities.length ? "a" : "b"}-${i % activities.length}`} className="flex items-start gap-2 px-2 py-1">
                <span className="w-1 h-1 rounded-full mt-1.5 shrink-0 dp-dot" data-tone={a.tone} />
                <div className="min-w-0">
                  <span className="text-xs block truncate dp-fg-primary">{a.action}</span>
                  <span className="text-[9px] dp-fg-muted">{a.time}</span>
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>

      <div className="mt-2 text-center">
        <span className="text-xs px-2 py-0.5 rounded-full border inline-block dp-pill">
          Personality-driven teams with career progression
        </span>
      </div>
    </div>
  );
}
