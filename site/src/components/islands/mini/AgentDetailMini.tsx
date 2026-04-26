import React from "react";

interface Props {
  tick: number;
}

const tools = ["file_system", "git", "code_runner", "web_search", "database"];

const activities = [
  { time: "2m ago", action: "Completed task #12: Schema validation", color: "var(--dp-success)" },
  { time: "5m ago", action: "Delegated subtask to Engineer", color: "var(--dp-accent)" },
  { time: "8m ago", action: "Approved PR #47 (quality: 94%)", color: "var(--dp-success)" },
  { time: "12m ago", action: "Started task #11: API design", color: "var(--dp-accent)" },
  { time: "15m ago", action: "Meeting: sprint planning (chair)", color: "var(--dp-success)" },
  { time: "20m ago", action: "Budget alert: 72% daily used", color: "var(--dp-warning)" },
];

export default function AgentDetailMini({ tick }: Props) {
  const cycle = tick % 20;
  const tasksCompleted = 47 + Math.floor(cycle / 4);
  const costToday = (12.4 + cycle * 0.15).toFixed(2);

  return (
    <div className="w-full px-2">
      {/* Agent header */}
      <div className="flex items-center gap-3 mb-4">
        <div
          className="w-10 h-10 rounded-full flex items-center justify-center text-sm font-bold dp-pulse"
          style={{ background: "var(--dp-border-bright)", color: "var(--dp-accent)" }}
        >
          SC
        </div>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2">
            <span className="text-sm font-semibold" style={{ color: "var(--dp-text-primary)" }}>
              Sarah Chen
            </span>
            <span
              className="text-xs px-1.5 py-0.5 rounded-full font-medium"
              style={{ background: "color-mix(in srgb, var(--dp-success) 15%, transparent)", color: "var(--dp-success)" }}
            >
              Active
            </span>
          </div>
          <div className="flex items-center gap-2">
            <span className="text-xs" style={{ color: "var(--dp-text-secondary)" }}>
              CTO &middot; C-Suite
            </span>
            <span className="text-xs" style={{ color: "var(--dp-text-muted)" }}>
              Semi-Autonomous
            </span>
          </div>
        </div>
      </div>

      {/* Metrics grid */}
      <div className="grid grid-cols-4 gap-2 mb-3">
        {[
          { label: "Tasks Done", value: String(tasksCompleted), color: "var(--dp-accent)" },
          { label: "Quality", value: "94%", color: "var(--dp-success)" },
          { label: "Cost Today", value: `EUR ${costToday}`, color: "var(--dp-warning)" },
          { label: "Trust", value: "Semi-Auto", color: "var(--dp-accent)" },
        ].map((m) => (
          <div key={m.label} className="rounded-md p-2 text-center" style={{ background: "var(--dp-bg-card)", border: "1px solid var(--dp-border)" }}>
            <div className="text-xs mb-0.5" style={{ color: "var(--dp-text-muted)" }}>
              {m.label}
            </div>
            <div className="text-sm font-semibold" style={{ color: m.color, fontFamily: "var(--dp-font-mono)" }}>
              {m.value}
            </div>
          </div>
        ))}
      </div>

      {/* Tool badges */}
      <div className="flex flex-wrap gap-1 mb-3">
        {tools.map((t) => (
          <span
            key={t}
            className="text-xs px-1.5 py-0.5 rounded"
            style={{ background: "var(--dp-border)", color: "var(--dp-text-secondary)" }}
          >
            {t}
          </span>
        ))}
      </div>

      {/* Activity log */}
      <div className="rounded-md overflow-hidden" style={{ background: "var(--dp-bg-card)", border: "1px solid var(--dp-border)" }}>
        <div className="px-2 py-1.5 border-b" style={{ borderColor: "var(--dp-border)" }}>
          <span className="text-xs font-semibold" style={{ color: "var(--dp-text-secondary)" }}>
            Recent Activity
          </span>
        </div>
        <div className="h-[72px] overflow-hidden relative">
          <div className="dp-activity-scroll">
            {/* Duplicated array for infinite scroll effect; index key is intentional (static data) */}
            {[...activities, ...activities].map((a, i) => (
              <div key={i} className="flex items-start gap-2 px-2 py-1">
                <span className="w-1 h-1 rounded-full mt-1.5 shrink-0" style={{ background: a.color }} />
                <div className="min-w-0">
                  <span className="text-xs block truncate" style={{ color: "var(--dp-text-primary)" }}>
                    {a.action}
                  </span>
                  <span className="text-[9px]" style={{ color: "var(--dp-text-muted)" }}>
                    {a.time}
                  </span>
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>

      <div className="mt-2 text-center">
        <span
          className="text-xs px-2 py-0.5 rounded-full border inline-block"
          style={{ color: "var(--dp-accent)", borderColor: "var(--dp-border-bright)", background: "var(--dp-bg-surface)" }}
        >
          Personality-driven teams with career progression
        </span>
      </div>
    </div>
  );
}
