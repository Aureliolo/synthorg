import { describe, it, expect, afterEach } from "vitest";
import { render, screen, fireEvent, within, cleanup } from "@testing-library/react";
import ComparisonTable from "./ComparisonTable";
import type { Dimension, Category, Competitor } from "./ComparisonTable";

const dimensions: Dimension[] = [
  { key: "memory", label: "Memory", description: "Long-term memory" },
  { key: "tool_use", label: "Tool Use", description: "Function calling" },
];

const categories: Category[] = [
  { key: "framework", label: "Multi-Agent Framework" },
  { key: "platform", label: "Commercial Platform" },
];

const competitors: Competitor[] = [
  {
    name: "SynthOrg",
    slug: "synthorg",
    url: "https://synthorg.io",
    description: "Virtual org framework",
    license: "BUSL-1.1",
    language: "Python",
    category: "framework",
    is_synthorg: true,
    features: {
      memory: { support: "full", note: "5 memory types" },
      tool_use: { support: "full", note: "MCP protocol" },
    },
  },
  {
    name: "TestAI",
    slug: "testai",
    url: "https://example.com",
    description: "A test framework",
    license: "MIT",
    language: "Python",
    category: "framework",
    features: {
      memory: { support: "partial", note: "Basic memory" },
      tool_use: { support: "none", note: "" },
    },
  },
  {
    name: "PlatformX",
    slug: "platformx",
    description: "A commercial platform",
    license: "Proprietary",
    language: "Java",
    category: "platform",
    features: {
      memory: { support: "full", note: "Enterprise memory" },
      tool_use: { support: "full", note: "Full tool suite" },
    },
  },
];

function renderTable() {
  render(
    <ComparisonTable
      competitors={competitors}
      dimensions={dimensions}
      categories={categories}
    />,
  );
}

describe("ComparisonTable", () => {
  afterEach(cleanup);

  it("renders all competitors in both desktop and mobile views", () => {
    renderTable();
    expect(screen.getAllByText("SynthOrg")).toHaveLength(2);
    expect(screen.getAllByText("TestAI")).toHaveLength(2);
    expect(screen.getAllByText("PlatformX")).toHaveLength(2);
  });

  it("shows correct result count", () => {
    renderTable();
    expect(screen.getByTestId("result-count").textContent).toBe(
      "Showing 3 of 3 frameworks",
    );
  });

  it("filters by category", () => {
    renderTable();
    const filterBar = screen.getByTestId("ct-filter-bar");
    const catBtn = within(filterBar).getByText("Commercial Platform");
    fireEvent.click(catBtn);
    // SynthOrg is always pinned through every filter (see ComparisonTable.tsx:188).
    expect(screen.getByTestId("result-count").textContent).toBe(
      "Showing 2 of 3 frameworks",
    );
    expect(screen.getAllByText("PlatformX")).toHaveLength(2);
    expect(screen.queryAllByText("TestAI")).toHaveLength(0);
  });

  it("filters by search text", () => {
    renderTable();
    const searchInput = screen.getByPlaceholderText("Search frameworks...");
    fireEvent.change(searchInput, { target: { value: "TestAI" } });
    expect(screen.getByTestId("result-count").textContent).toBe(
      "Showing 2 of 3 frameworks",
    );
  });

  it("filters by license", () => {
    renderTable();
    const licenseSelect = screen.getByLabelText("Filter by license");
    fireEvent.change(licenseSelect, { target: { value: "MIT" } });
    expect(screen.getByTestId("result-count").textContent).toBe(
      "Showing 2 of 3 frameworks",
    );
    expect(screen.getAllByText("TestAI")).toHaveLength(2);
  });

  it("filters by feature support", () => {
    renderTable();
    const featureSelect = screen.getByLabelText("Filter by feature support");
    fireEvent.change(featureSelect, { target: { value: "tool_use" } });
    expect(screen.getByTestId("result-count").textContent).toBe(
      "Showing 2 of 3 frameworks",
    );
    expect(screen.queryAllByText("TestAI")).toHaveLength(0);
  });

  it("clears all filters", () => {
    renderTable();
    const filterBar = screen.getByTestId("ct-filter-bar");
    const catBtn = within(filterBar).getByText("Commercial Platform");
    fireEvent.click(catBtn);
    expect(screen.getByTestId("result-count").textContent).toBe(
      "Showing 2 of 3 frameworks",
    );
    const clearBtn = within(filterBar).getByText("Clear");
    fireEvent.click(clearBtn);
    expect(screen.getByTestId("result-count").textContent).toBe(
      "Showing 3 of 3 frameworks",
    );
  });

  it("renders support icons with aria-labels", () => {
    renderTable();
    const fullIcons = screen.getAllByRole("img", { name: /Full support/i });
    expect(fullIcons.length).toBeGreaterThan(0);
  });

  it("renders sortable headers with aria-sort", () => {
    renderTable();
    const table = screen.getByTestId("ct-table-wrap");
    const header = within(table).getByText("Framework").closest("th");
    expect(header).not.toBeNull();
    expect(header).toHaveAttribute("aria-sort", "ascending");
  });

  it("toggles sort direction on header button click", () => {
    renderTable();
    const table = screen.getByTestId("ct-table-wrap");
    const sortBtn = within(table).getByText("Framework");
    const header = sortBtn.closest("th");
    expect(header).not.toBeNull();
    expect(header).toHaveAttribute("aria-sort", "ascending");
    fireEvent.click(sortBtn);
    expect(header).toHaveAttribute("aria-sort", "descending");
  });

  it("expands row details on chevron click", () => {
    renderTable();
    const expandBtn = screen.getByLabelText("Expand SynthOrg details");
    fireEvent.click(expandBtn);
    const detail = screen.getByTestId("detail-synthorg");
    expect(detail.textContent).toContain("Virtual org framework");
    expect(screen.getByLabelText("Collapse SynthOrg details")).toBeInTheDocument();
  });

  it("category filter buttons have aria-pressed", () => {
    renderTable();
    const filterBar = screen.getByTestId("ct-filter-bar");
    const catBtn = within(filterBar).getByText("Multi-Agent Framework");
    expect(catBtn).toHaveAttribute("aria-pressed", "false");
    fireEvent.click(catBtn);
    expect(catBtn).toHaveAttribute("aria-pressed", "true");
  });

  it("shows legend with support levels", () => {
    renderTable();
    const legend = screen.getByTestId("comparison-legend");
    expect(legend.textContent).toContain("Full");
    expect(legend.textContent).toContain("Partial");
    expect(legend.textContent).toContain("Planned");
    expect(legend.textContent).toContain("None");
  });
});
