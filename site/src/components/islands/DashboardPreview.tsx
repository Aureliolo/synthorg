import React, { useState, useEffect, useCallback } from "react";
import "./DashboardPreview.css";
import OrgChartMini from "./mini/OrgChartMini";
import TaskBoardMini from "./mini/TaskBoardMini";
import AgentDetailMini from "./mini/AgentDetailMini";
import BudgetMini from "./mini/BudgetMini";

const pages = [
  { label: "Org Chart", icon: "M17 20h5v-2a3 3 0 00-5.356-1.857M17 20H7m10 0v-2c0-.656-.126-1.283-.356-1.857M7 20H2v-2a3 3 0 015.356-1.857M7 20v-2c0-.656.126-1.283.356-1.857m0 0a5.002 5.002 0 019.288 0M15 7a3 3 0 11-6 0 3 3 0 016 0z" },
  { label: "Tasks", icon: "M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2m-6 9l2 2 4-4" },
  { label: "Agent", icon: "M16 7a4 4 0 11-8 0 4 4 0 018 0zM12 14a7 7 0 00-7 7h14a7 7 0 00-7-7z" },
  { label: "Budget", icon: "M12 8c-1.657 0-3 .895-3 2s1.343 2 3 2 3 .895 3 2-1.343 2-3 2m0-8c1.11 0 2.08.402 2.599 1M12 8V7m0 1v8m0 0v1m0-1c-1.11 0-2.08-.402-2.599-1M21 12a9 9 0 11-18 0 9 9 0 0118 0z" },
];

const sidebarIcons = [
  "M3 12l2-2m0 0l7-7 7 7M5 10v10a1 1 0 001 1h3m10-11l2 2m-2-2v10a1 1 0 01-1 1h-3m-6 0a1 1 0 001-1v-4a1 1 0 011-1h2a1 1 0 011 1v4a1 1 0 001 1m-6 0h6",
  pages[0].icon,
  pages[1].icon,
  pages[2].icon,
  pages[3].icon,
  "M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.066 2.573c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.573 1.066c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.066-2.573c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z",
];

export default function DashboardPreview() {
  const [activeTab, setActiveTab] = useState(0);
  const [isPaused, setIsPaused] = useState(false);
  const [tick, setTick] = useState(0);
  const [pageKey, setPageKey] = useState(0);

  // Auto-cycle tabs
  useEffect(() => {
    if (isPaused) return;
    const timer = setInterval(() => {
      setActiveTab((t) => (t + 1) % pages.length);
      setPageKey((k) => k + 1);
    }, 6000);
    return () => clearInterval(timer);
  }, [isPaused]);

  // Animation tick (1Hz)
  useEffect(() => {
    const timer = setInterval(() => setTick((t) => t + 1), 1000);
    return () => clearInterval(timer);
  }, []);

  const selectTab = useCallback((i: number) => {
    const clamped = Math.max(0, Math.min(i, pages.length - 1));
    setActiveTab(clamped);
    setPageKey((k) => k + 1);
    setIsPaused(true);
  }, []);

  const PageComponent = [OrgChartMini, TaskBoardMini, AgentDetailMini, BudgetMini][activeTab];

  return (
    <div
      className="dashboard-preview rounded-xl overflow-hidden border max-w-4xl mx-auto"
      style={{ background: "var(--dp-bg-base)", borderColor: "var(--dp-border)" }}
      onMouseEnter={() => setIsPaused(true)}
      onMouseLeave={(e) => {
        if (!e.currentTarget.contains(document.activeElement)) {
          setIsPaused(false);
        }
      }}
      onFocus={() => setIsPaused(true)}
      onBlur={(e) => {
        if (!e.currentTarget.contains(e.relatedTarget as Node)) {
          setIsPaused(false);
        }
      }}
    >
      <div className="flex">
        {/* Mock sidebar */}
        <div
          className="hidden sm:flex flex-col items-center py-4 px-2 gap-3 shrink-0"
          style={{ background: "var(--dp-bg-surface)", borderRight: "1px solid var(--dp-border)" }}
        >
          {sidebarIcons.map((d, i) => (
            <div
              key={i}
              className="w-8 h-8 flex items-center justify-center rounded"
              style={{
                color: i === activeTab + 1 ? "var(--dp-accent)" : "var(--dp-text-muted)",
                background: i === activeTab + 1 ? "rgba(56, 189, 248, 0.1)" : "transparent",
              }}
            >
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
                <path d={d} />
              </svg>
            </div>
          ))}
        </div>

        {/* Main content */}
        <div className="flex-1 min-w-0">
          {/* Header */}
          <div
            className="flex items-center justify-between px-4 py-2.5 border-b"
            style={{ borderColor: "var(--dp-border)", background: "var(--dp-bg-surface)" }}
          >
            <div className="flex items-center gap-2">
              <span className="text-sm font-semibold" style={{ color: "var(--dp-text-primary)" }}>
                Acme AI Lab
              </span>
              <span
                className="w-2 h-2 rounded-full"
                style={{ background: "var(--dp-success)" }}
              />
              <span className="text-xs" style={{ color: "var(--dp-success)" }}>
                Running
              </span>
            </div>
            <div className="flex items-center gap-2">
              <span className="text-xs px-2 py-0.5 rounded" style={{ background: "var(--dp-border)", color: "var(--dp-text-muted)" }}>
                7 agents
              </span>
              <span className="text-xs px-2 py-0.5 rounded" style={{ background: "var(--dp-border)", color: "var(--dp-text-muted)" }}>
                12 tasks
              </span>
            </div>
          </div>

          {/* Tab navigation */}
          <div
            className="flex border-b"
            style={{ borderColor: "var(--dp-border)" }}
            role="tablist"
            aria-label="Dashboard pages"
          >
            {pages.map((page, i) => (
              <button
                key={page.label}
                id={`dp-tab-${i}`}
                role="tab"
                aria-selected={i === activeTab}
                aria-controls="dp-tabpanel"
                onClick={() => selectTab(i)}
                className="flex items-center gap-1.5 px-4 py-2 text-sm transition-colors border-b-2 cursor-pointer"
                style={{
                  color: i === activeTab ? "var(--dp-accent)" : "var(--dp-text-muted)",
                  borderBottomColor: i === activeTab ? "var(--dp-accent)" : "transparent",
                  background: "transparent",
                }}
              >
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <path d={page.icon} />
                </svg>
                {page.label}
              </button>
            ))}
          </div>

          {/* Page content: fixed height to prevent layout shift */}
          <div
            id="dp-tabpanel"
            className="p-4 h-[360px] sm:h-[420px] overflow-hidden flex items-start"
            role="tabpanel"
            aria-labelledby={`dp-tab-${activeTab}`}
          >
            <div key={pageKey} className="w-full dp-page-enter">
              <PageComponent tick={tick} />
            </div>
          </div>
        </div>
      </div>

      {/* Page navigation */}
      <div
        className="flex items-center justify-between px-2 py-1.5 border-t"
        style={{ borderColor: "var(--dp-border)", background: "var(--dp-bg-surface)" }}
      >
        {/* Prev arrow */}
        <button
          className="w-6 h-6 flex items-center justify-center rounded transition-colors cursor-pointer focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-sky-400"
          style={{ color: activeTab > 0 ? "var(--dp-text-secondary)" : "var(--dp-border-bright)" }}
          onClick={() => selectTab(activeTab - 1)}
          aria-label="Previous page"
          disabled={activeTab === 0}
        >
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <path d="M15 19l-7-7 7-7" />
          </svg>
        </button>

        {/* Page indicators */}
        <div className="flex gap-1">
          {pages.map((page, i) => (
            <button
              key={i}
              className="px-3 py-1 rounded text-xs transition-colors cursor-pointer focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-sky-400"
              style={{
                background: i === activeTab ? "rgba(56, 189, 248, 0.15)" : "transparent",
                color: i === activeTab ? "var(--dp-accent)" : "var(--dp-text-muted)",
                border: "none",
              }}
              onClick={() => selectTab(i)}
              aria-label={`Go to ${page.label}`}
            >
              {page.label}
            </button>
          ))}
        </div>

        {/* Next arrow */}
        <button
          className="w-6 h-6 flex items-center justify-center rounded transition-colors cursor-pointer focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-sky-400"
          style={{ color: activeTab < pages.length - 1 ? "var(--dp-text-secondary)" : "var(--dp-border-bright)" }}
          onClick={() => selectTab(activeTab + 1)}
          aria-label="Next page"
          disabled={activeTab === pages.length - 1}
        >
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <path d="M9 5l7 7-7 7" />
          </svg>
        </button>
      </div>
    </div>
  );
}
