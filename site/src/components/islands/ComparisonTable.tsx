import React, { Fragment, useState, useMemo, useCallback, useRef, useEffect } from "react";
import "./ComparisonTable.css";

/* ------------------------------------------------------------------ */
/* Types                                                               */
/* ------------------------------------------------------------------ */

export interface Dimension {
  key: string;
  label: string;
  description: string;
}

export interface Category {
  key: string;
  label: string;
}

export interface FeatureEntry {
  support: "full" | "partial" | "none" | "planned";
  note: string;
}

export interface Competitor {
  name: string;
  slug: string;
  url?: string;
  repo?: string;
  description: string;
  license: string;
  language: string;
  category: string;
  pricing?: string;
  self_hosted?: string;
  is_synthorg?: boolean;
  features: Record<string, FeatureEntry>;
}

interface Props {
  competitors: Competitor[];
  dimensions: Dimension[];
  categories: Category[];
}

/* ------------------------------------------------------------------ */
/* Constants                                                           */
/* ------------------------------------------------------------------ */

const SUPPORT_ORDER: Record<string, number> = {
  full: 0,
  partial: 1,
  planned: 2,
  none: 3,
};

const SUPPORT_ICONS: Record<string, string> = {
  full: "\u2714",
  partial: "~",
  none: "-",
  planned: "\u23f2",
};

const SUPPORT_LABELS: Record<string, string> = {
  full: "Full support",
  partial: "Partial support",
  none: "Not supported",
  planned: "Planned",
};

/* ------------------------------------------------------------------ */
/* Sub-components                                                      */
/* ------------------------------------------------------------------ */

function SupportIcon({ level, note }: { level: string; note?: string }) {
  const label = SUPPORT_LABELS[level] || level;
  return (
    <span
      className="ct-support"
      data-level={level}
      role="img"
      aria-label={note ? `${label}: ${note}` : label}
      title={note || label}
    >
      {SUPPORT_ICONS[level] || level}
    </span>
  );
}

function SortArrow({
  column,
  sortBy,
}: {
  column: string;
  sortBy: { key: string; direction: "asc" | "desc" };
}) {
  const active = sortBy.key === column;
  const arrow = active && sortBy.direction === "desc" ? "\u25BC" : "\u25B2";
  return (
    <span className="sort-arrow" data-active={active ? "true" : "false"} aria-hidden="true">
      {arrow}
    </span>
  );
}

/* ------------------------------------------------------------------ */
/* Main component                                                      */
/* ------------------------------------------------------------------ */

export default function ComparisonTable({
  competitors,
  dimensions,
  categories,
}: Props) {
  // State
  const [categoryFilter, setCategoryFilter] = useState<string | null>(null);
  const [licenseFilter, setLicenseFilter] = useState<string | null>(null);
  const [featureFilter, setFeatureFilter] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [sortBy, setSortBy] = useState<{
    key: string;
    direction: "asc" | "desc";
  }>({ key: "name", direction: "asc" });
  const [expandedRows, setExpandedRows] = useState<Set<string>>(new Set());
  const [hiddenColumns, setHiddenColumns] = useState<Set<string>>(new Set());
  const [showColumnPicker, setShowColumnPicker] = useState(false);
  const [fullWidth, setFullWidth] = useState(false);
  const [canScrollRight, setCanScrollRight] = useState(false);
  const tableWrapRef = useRef<HTMLDivElement>(null);

  // Scroll hint detection
  useEffect(() => {
    const el = tableWrapRef.current;
    if (!el) return;

    const checkScroll = () => {
      setCanScrollRight(el.scrollWidth - el.scrollLeft - el.clientWidth > 4);
    };

    checkScroll();
    el.addEventListener("scroll", checkScroll, { passive: true });
    const observer = new ResizeObserver(checkScroll);
    observer.observe(el);

    return () => {
      el.removeEventListener("scroll", checkScroll);
      observer.disconnect();
    };
  }, [hiddenColumns, fullWidth]);

  // Escape key to exit full-width mode
  useEffect(() => {
    if (!fullWidth) return;
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") setFullWidth(false);
    };
    document.addEventListener("keydown", handleKeyDown);
    return () => document.removeEventListener("keydown", handleKeyDown);
  }, [fullWidth]);

  // Close column picker on outside click
  const pickerRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!showColumnPicker) return;
    const handleClickOutside = (e: MouseEvent) => {
      if (pickerRef.current && !pickerRef.current.contains(e.target as Node)) {
        setShowColumnPicker(false);
      }
    };
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, [showColumnPicker]);

  // Visible dimensions
  const visibleDimensions = useMemo(
    () => dimensions.filter((d) => !hiddenColumns.has(d.key)),
    [dimensions, hiddenColumns],
  );

  // Category lookup
  const categoryMap = useMemo(() => {
    const map: Record<string, string> = {};
    for (const cat of categories) {
      map[cat.key] = cat.label;
    }
    return map;
  }, [categories]);

  // Filtering (SynthOrg always passes)
  const filtered = useMemo(() => {
    let result = competitors;

    if (categoryFilter) {
      result = result.filter((c) => c.is_synthorg || c.category === categoryFilter);
    }

    if (licenseFilter) {
      result = result.filter((c) => c.is_synthorg || c.license === licenseFilter);
    }

    if (featureFilter) {
      result = result.filter((c) => {
        if (c.is_synthorg) return true;
        const feat = c.features[featureFilter];
        return feat?.support === "full" || feat?.support === "partial";
      });
    }

    if (search.trim()) {
      const q = search.trim().toLowerCase();
      result = result.filter(
        (c) =>
          c.is_synthorg ||
          c.name.toLowerCase().includes(q) ||
          c.description.toLowerCase().includes(q) ||
          c.license.toLowerCase().includes(q),
      );
    }

    return result;
  }, [competitors, categoryFilter, licenseFilter, featureFilter, search]);

  // Sorting (SynthOrg always pinned to top)
  const sorted = useMemo(() => {
    const synthorg = filtered.filter((c) => c.is_synthorg);
    const rest = filtered.filter((c) => !c.is_synthorg);

    rest.sort((a, b) => {
      const dir = sortBy.direction === "asc" ? 1 : -1;

      if (sortBy.key === "name") {
        return dir * a.name.localeCompare(b.name);
      }
      if (sortBy.key === "license") {
        return dir * a.license.localeCompare(b.license);
      }
      if (sortBy.key === "category") {
        const catA = categoryMap[a.category] || a.category;
        const catB = categoryMap[b.category] || b.category;
        return dir * catA.localeCompare(catB);
      }

      // Sort by dimension support level
      const featA = a.features[sortBy.key];
      const featB = b.features[sortBy.key];
      const orderA = SUPPORT_ORDER[featA?.support || "none"] ?? 3;
      const orderB = SUPPORT_ORDER[featB?.support || "none"] ?? 3;
      if (orderA !== orderB) return dir * (orderA - orderB);
      return a.name.localeCompare(b.name);
    });

    return [...synthorg, ...rest];
  }, [filtered, sortBy, categoryMap]);

  // Whether any filter is active (for SynthOrg "pinned" badge)
  const hasActiveFilter = !!(categoryFilter || licenseFilter || featureFilter || search.trim());

  // Handlers
  const handleSort = useCallback(
    (key: string) => {
      setSortBy((prev) =>
        prev.key === key
          ? { key, direction: prev.direction === "asc" ? "desc" : "asc" }
          : { key, direction: "asc" },
      );
    },
    [],
  );

  const toggleExpanded = useCallback((slug: string) => {
    setExpandedRows((prev) => {
      const next = new Set(prev);
      if (next.has(slug)) {
        next.delete(slug);
      } else {
        next.add(slug);
      }
      return next;
    });
  }, []);

  const toggleColumn = useCallback((key: string) => {
    setHiddenColumns((prev) => {
      const next = new Set(prev);
      if (next.has(key)) {
        next.delete(key);
      } else {
        next.add(key);
      }
      return next;
    });
  }, []);

  const clearFilters = useCallback(() => {
    setCategoryFilter(null);
    setLicenseFilter(null);
    setFeatureFilter(null);
    setSearch("");
  }, []);


  // Unique categories present in data
  const availableCategories = useMemo(() => {
    const seen = new Set<string>();
    for (const c of competitors) {
      if (c.category) seen.add(c.category);
    }
    return categories.filter((cat) => seen.has(cat.key));
  }, [competitors, categories]);

  // Unique licenses present in data
  const availableLicenses = useMemo(() => {
    const seen = new Set<string>();
    for (const c of competitors) {
      if (c.license) seen.add(c.license);
    }
    return Array.from(seen).sort();
  }, [competitors]);

  return (
    <div className={`comparison-table${fullWidth ? " ct-full-width" : ""}`}>
      {/* Legend */}
      <div className="ct-legend" data-testid="comparison-legend">
        <span>
          <SupportIcon level="full" /> Full
        </span>
        <span>
          <SupportIcon level="partial" /> Partial
        </span>
        <span>
          <SupportIcon level="planned" /> Planned
        </span>
        <span>
          <SupportIcon level="none" /> None
        </span>
      </div>

      {/* Filter bar */}
      <div className="ct-filter-bar" data-testid="ct-filter-bar">
        <input
          type="text"
          className="ct-search"
          placeholder="Search frameworks..."
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          aria-label="Search frameworks"
        />
        {availableCategories.map((cat) => (
          <button
            key={cat.key}
            className="ct-filter-btn"
            data-active={categoryFilter === cat.key ? "true" : "false"}
            aria-pressed={categoryFilter === cat.key}
            onClick={() =>
              setCategoryFilter((prev) =>
                prev === cat.key ? null : cat.key,
              )
            }
          >
            {cat.label}
          </button>
        ))}
        <select
          className="ct-select"
          value={licenseFilter || ""}
          onChange={(e) => setLicenseFilter(e.target.value || null)}
          aria-label="Filter by license"
        >
          <option value="">All Licenses</option>
          {availableLicenses.map((lic) => (
            <option key={lic} value={lic}>{lic}</option>
          ))}
        </select>
        <select
          className="ct-select"
          value={featureFilter || ""}
          onChange={(e) => setFeatureFilter(e.target.value || null)}
          aria-label="Filter by feature support"
        >
          <option value="">All Features</option>
          {dimensions.map((dim) => (
            <option key={dim.key} value={dim.key}>Has {dim.label}</option>
          ))}
        </select>
        {hasActiveFilter && (
          <button className="ct-clear-btn" onClick={clearFilters}>
            Clear
          </button>
        )}
      </div>

      {/* Toolbar: column picker + full-width toggle */}
      <div className="ct-toolbar">
        <div className="ct-result-count" data-testid="result-count">
          Showing {sorted.length} of {competitors.length} frameworks
        </div>
        <div className="ct-toolbar-actions">
          <div className="ct-column-picker-wrap">
            <button
              className="ct-toolbar-btn"
              onClick={() => setShowColumnPicker((prev) => !prev)}
              aria-expanded={showColumnPicker}
              aria-label="Toggle column visibility"
            >
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <rect x="3" y="3" width="7" height="7" /><rect x="14" y="3" width="7" height="7" />
                <rect x="3" y="14" width="7" height="7" /><rect x="14" y="14" width="7" height="7" />
              </svg>
              Columns
              {hiddenColumns.size > 0 && (
                <span className="ct-badge">{dimensions.length - hiddenColumns.size}/{dimensions.length}</span>
              )}
            </button>
            {showColumnPicker && (
              <div className="ct-column-picker" role="menu" ref={pickerRef}>
                {dimensions.map((dim) => (
                  <label key={dim.key} className="ct-column-option">
                    <input
                      type="checkbox"
                      checked={!hiddenColumns.has(dim.key)}
                      onChange={() => toggleColumn(dim.key)}
                    />
                    {dim.label}
                  </label>
                ))}
                {hiddenColumns.size > 0 && (
                  <button
                    className="ct-column-reset"
                    onClick={() => setHiddenColumns(new Set())}
                  >
                    Show all
                  </button>
                )}
              </div>
            )}
          </div>
          <button
            className="ct-toolbar-btn"
            onClick={() => setFullWidth((prev) => !prev)}
            aria-label={fullWidth ? "Exit full width" : "Expand to full width"}
            title={fullWidth ? "Exit full width" : "Expand to full width"}
          >
            {fullWidth ? (
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <polyline points="4 14 10 14 10 20" /><polyline points="20 10 14 10 14 4" />
                <line x1="14" y1="10" x2="21" y2="3" /><line x1="3" y1="21" x2="10" y2="14" />
              </svg>
            ) : (
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <polyline points="15 3 21 3 21 9" /><polyline points="9 21 3 21 3 15" />
                <line x1="21" y1="3" x2="14" y2="10" /><line x1="3" y1="21" x2="10" y2="14" />
              </svg>
            )}
          </button>
        </div>
      </div>

      {/* Desktop: Table view */}
      <div className="ct-table-wrap-outer">
        <div
          className="ct-table-wrap"
          ref={tableWrapRef}
          data-testid="ct-table-wrap"
        >
          <table className="ct-table">
            <thead>
              <tr>
                <th className="ct-th-expand"></th>
                <th aria-sort={sortBy.key === "name" ? (sortBy.direction === "asc" ? "ascending" : "descending") : "none"}>
                  <button type="button" className="ct-sort-btn" onClick={() => handleSort("name")}>
                    Framework
                    <SortArrow column="name" sortBy={sortBy} />
                  </button>
                </th>
                <th aria-sort={sortBy.key === "category" ? (sortBy.direction === "asc" ? "ascending" : "descending") : "none"}>
                  <button type="button" className="ct-sort-btn" onClick={() => handleSort("category")}>
                    Category
                    <SortArrow column="category" sortBy={sortBy} />
                  </button>
                </th>
                <th aria-sort={sortBy.key === "license" ? (sortBy.direction === "asc" ? "ascending" : "descending") : "none"}>
                  <button type="button" className="ct-sort-btn" onClick={() => handleSort("license")}>
                    License
                    <SortArrow column="license" sortBy={sortBy} />
                  </button>
                </th>
                {visibleDimensions.map((dim) => (
                  <th
                    key={dim.key}
                    aria-sort={sortBy.key === dim.key ? (sortBy.direction === "asc" ? "ascending" : "descending") : "none"}
                  >
                    <button type="button" className="ct-sort-btn" onClick={() => handleSort(dim.key)} aria-describedby={`dim-desc-${dim.key}`}>
                      {dim.label}
                      <SortArrow column={dim.key} sortBy={sortBy} />
                    </button>
                    <span id={`dim-desc-${dim.key}`} className="ct-sr-only">{dim.description}</span>
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {sorted.map((comp) => {
                const isExpanded = expandedRows.has(comp.slug);
                return (
                  <Fragment key={comp.slug}>
                    <tr
                      data-synthorg={comp.is_synthorg ? "true" : "false"}
                    >
                      <td>
                        <button
                          className="ct-expand-btn"
                          data-open={isExpanded ? "true" : "false"}
                          onClick={() => toggleExpanded(comp.slug)}
                          aria-label={`${isExpanded ? "Collapse" : "Expand"} ${comp.name} details`}
                          aria-expanded={isExpanded}
                          aria-controls={`details-${comp.slug}`}
                        >
                          <svg
                            width="14"
                            height="14"
                            viewBox="0 0 24 24"
                            fill="none"
                            stroke="currentColor"
                            strokeWidth="2"
                          >
                            <path d="m6 9 6 6 6-6" />
                          </svg>
                        </button>
                      </td>
                      <th scope="row">
                        <div className="ct-name-cell">
                          {comp.url ? (
                            <a
                              href={comp.url}
                              className={
                                comp.is_synthorg
                                  ? "ct-name-link ct-name-synthorg"
                                  : "ct-name-link"
                              }
                              target="_blank"
                              rel="noopener noreferrer"
                            >
                              {comp.name}
                            </a>
                          ) : (
                            <span
                              className={
                                comp.is_synthorg
                                  ? "ct-name-synthorg"
                                  : undefined
                              }
                            >
                              {comp.name}
                            </span>
                          )}
                          {comp.is_synthorg && hasActiveFilter && (
                            <span className="ct-pinned-badge" title="Always shown for comparison">
                              pinned
                            </span>
                          )}
                        </div>
                      </th>
                      <td>
                        <span className="ct-category-badge">
                          {categoryMap[comp.category] || comp.category}
                        </span>
                      </td>
                      <td>
                        <span className="ct-license">{comp.license}</span>
                      </td>
                      {visibleDimensions.map((dim) => {
                        const feat = comp.features[dim.key];
                        const support = feat?.support || "none";
                        const note = feat?.note || "";
                        return (
                          <td key={dim.key} className="ct-support-cell">
                            <SupportIcon level={support} note={note} />
                          </td>
                        );
                      })}
                    </tr>
                    {isExpanded && (
                      <tr
                        className="ct-detail-row"
                        id={`details-${comp.slug}`}
                      >
                        <td colSpan={4 + visibleDimensions.length}>
                          <div className="ct-detail-content" data-testid={`detail-${comp.slug}`}>
                            <div className="ct-detail-item ct-detail-description">
                              <span className="ct-detail-label">Description</span>
                              <span className="ct-detail-value">
                                {comp.description}
                                {comp.repo && (
                                  <>
                                    {" "}
                                    <a
                                      href={comp.repo}
                                      target="_blank"
                                      rel="noopener noreferrer"
                                      className="ct-repo-link"
                                    >
                                      Repository
                                    </a>
                                  </>
                                )}
                              </span>
                            </div>
                            {dimensions.map((dim) => {
                              const feat = comp.features[dim.key];
                              if (!feat?.note) return null;
                              return (
                                <div key={dim.key} className="ct-detail-item">
                                  <span className="ct-detail-label">
                                    {dim.label}
                                  </span>
                                  <span className="ct-detail-value">
                                    {feat.note}
                                  </span>
                                </div>
                              );
                            })}
                          </div>
                        </td>
                      </tr>
                    )}
                  </Fragment>
                );
              })}
            </tbody>
          </table>
        </div>
        {canScrollRight && (
          <div className="ct-scroll-hint" aria-hidden="true">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <polyline points="9 18 15 12 9 6" />
            </svg>
          </div>
        )}
      </div>

      {/* Mobile: Card view */}
      <div className="ct-cards">
        {sorted.map((comp) => (
          <div
            key={comp.slug}
            className="ct-card"
            data-synthorg={comp.is_synthorg ? "true" : "false"}
          >
            <div className="ct-card-header">
              <div>
                {comp.url ? (
                  <a
                    href={comp.url}
                    className={`ct-card-name ct-card-name-link ${comp.is_synthorg ? "ct-name-synthorg" : ""}`}
                    target="_blank"
                    rel="noopener noreferrer"
                  >
                    {comp.name}
                  </a>
                ) : (
                  <span className={`ct-card-name ${comp.is_synthorg ? "ct-name-synthorg" : ""}`}>
                    {comp.name}
                  </span>
                )}
                {comp.is_synthorg && hasActiveFilter && (
                  <span className="ct-pinned-badge" title="Always shown for comparison">
                    pinned
                  </span>
                )}
              </div>
              <div className="ct-card-meta">
                <span className="ct-category-badge">
                  {categoryMap[comp.category] || comp.category}
                </span>
                <span className="ct-license">{comp.license}</span>
              </div>
            </div>
            <p className="ct-card-description">
              {comp.description}
            </p>
            <div className="ct-card-grid">
              {visibleDimensions.map((dim) => {
                const feat = comp.features[dim.key];
                const support = feat?.support || "none";
                return (
                  <div key={dim.key} className="ct-card-feature">
                    <SupportIcon level={support} note={feat?.note} />
                    <span>
                      {dim.label}
                      {feat?.note && (
                        <span className="ct-card-note">{feat.note}</span>
                      )}
                    </span>
                  </div>
                );
              })}
            </div>
            {comp.repo && (
              <div className="ct-card-repo">
                <a
                  href={comp.repo}
                  target="_blank"
                  rel="noopener noreferrer"
                >
                  Repository
                </a>
              </div>
            )}
          </div>
        ))}
      </div>

      {sorted.length === 0 && (
        <div className="ct-empty-state">
          No frameworks match your filters.{" "}
          <button className="ct-empty-clear-btn" onClick={clearFilters}>
            Clear filters
          </button>
        </div>
      )}
    </div>
  );
}
