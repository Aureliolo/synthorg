import React, { useId } from "react";

interface Props {
  tick: number;
}

// Widths and bar colours live in DashboardPreview.css keyed by `data-agent`
// (the strict site CSP blocks inline styles, so per-bar colour cannot be set
// via a style attribute here).
const agentSpend = [
  { name: "CTO", pct: 40 },
  { name: "Engineer", pct: 35 },
  { name: "QA", pct: 15 },
  { name: "Design", pct: 10 },
];

type Tone = "primary" | "success" | "warning" | "accent";

export default function BudgetMini({ tick }: Props) {
  const sparkGradId = useId();
  // Slowly decreasing gauge
  const basePct = 68;
  const gaugeValue = Math.max(30, basePct - (tick % 40) * 0.3);
  const spent = (500 * (1 - gaugeValue / 100)).toFixed(2);
  const remaining = (500 * (gaugeValue / 100)).toFixed(2);
  const forecast = (500 * (1 - gaugeValue / 100) * 1.5).toFixed(2);

  // SVG arc math for the gauge
  const radius = 50; // matches SVG arc: A 50 50
  const circumference = Math.PI * radius; // half circle
  const dashOffset = circumference * (1 - gaugeValue / 100);

  const forecastTone: Tone = parseFloat(forecast) <= 500 ? "success" : "warning";

  return (
    <div className="w-full px-2">
      <div className="flex items-start gap-4 mb-3">
        {/* Gauge */}
        <div className="relative flex-shrink-0">
          <svg viewBox="0 0 120 80" width="120" height="80" aria-hidden="true">
            {/* Background arc */}
            <path
              d="M 10 60 A 50 50 0 0 1 110 60"
              fill="none"
              stroke="var(--dp-border)"
              strokeWidth="7"
              strokeLinecap="round"
            />
            {/* Filled arc */}
            <path
              d="M 10 60 A 50 50 0 0 1 110 60"
              fill="none"
              stroke="var(--dp-accent)"
              strokeWidth="7"
              strokeLinecap="round"
              strokeDasharray={`${circumference}`}
              strokeDashoffset={dashOffset}
              className="dp-gauge-fill"
            />
            <text x="60" y="48" textAnchor="middle" fill="var(--dp-text-primary)" fontSize="16" fontWeight="700" fontFamily="var(--dp-font-mono)">
              {Math.round(gaugeValue)}%
            </text>
            <text x="60" y="72" textAnchor="middle" fill="var(--dp-text-muted)" fontSize="9" fontFamily="var(--dp-font-sans)">
              budget remaining
            </text>
          </svg>
        </div>

        {/* Metric cards */}
        <div className="grid grid-cols-2 gap-1.5 flex-1">
          {[
            { label: "Monthly Budget", value: "EUR 500.00", tone: "primary" as Tone },
            { label: "Spent", value: `EUR ${spent}`, tone: "warning" as Tone },
            { label: "Remaining", value: `EUR ${remaining}`, tone: "success" as Tone },
            { label: "Forecast", value: `EUR ${forecast}`, tone: forecastTone },
          ].map((m) => (
            <div key={m.label} className="rounded p-1.5 border dp-bg-card dp-bd">
              <div className="text-[9px] dp-fg-muted">{m.label}</div>
              <div className="text-xs font-semibold dp-mono dp-tone" data-tone={m.tone}>
                {m.value}
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* Agent spend breakdown */}
      <div className="rounded-md p-2 border dp-bg-card dp-bd">
        <div className="text-xs font-semibold mb-2 dp-fg-secondary">Spend by Agent</div>
        <div className="space-y-1.5">
          {agentSpend.map((a) => (
            <div key={a.name} className="flex items-center gap-2">
              <span className="text-xs w-12 dp-fg-secondary">{a.name}</span>
              <div className="flex-1 h-2 rounded-full overflow-hidden dp-track">
                <div
                  className="h-full rounded-full transition-all duration-1000 dp-spend"
                  data-agent={a.name}
                />
              </div>
              <span className="text-xs w-6 text-right dp-fg-muted">{a.pct}%</span>
            </div>
          ))}
        </div>

        {/* Mini sparkline */}
        <div className="mt-2 pt-2 border-t dp-bd">
          <div className="flex items-center justify-between mb-1">
            <span className="text-[9px] dp-fg-muted">7-day trend</span>
            <span className="text-[9px] dp-fg-success">-3.2%</span>
          </div>
          <svg viewBox="0 0 200 24" className="w-full h-5" aria-hidden="true">
            <defs>
              <linearGradient id={sparkGradId} x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor="var(--dp-accent)" stopOpacity="0.15" />
                <stop offset="100%" stopColor="var(--dp-accent)" stopOpacity="0" />
              </linearGradient>
            </defs>
            <polyline
              points="0,18 30,15 60,12 90,16 120,10 150,8 170,11 200,6"
              fill="none"
              stroke="var(--dp-accent)"
              strokeWidth="1.5"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
            <polyline
              points="0,18 30,15 60,12 90,16 120,10 150,8 170,11 200,6 200,24 0,24"
              fill={`url(#${sparkGradId})`}
              stroke="none"
            />
          </svg>
        </div>
      </div>

      <div className="mt-2 text-center">
        <span className="text-xs px-2 py-0.5 rounded-full border inline-block dp-pill">
          Per-token cost tracking with hierarchical budget cascades
        </span>
      </div>
    </div>
  );
}
