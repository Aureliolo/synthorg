---
description: "Research any link, article, tool, or concept and evaluate what it means for this project"
argument-hint: "<url, tool name, concept, or pasted content> [optional: specific angle to evaluate]"
allowed-tools:
  - WebFetch
  - WebSearch
  - Read
  - Write
  - Edit
  - Grep
  - Glob
  - Agent
  - AskUserQuestion
  - Bash
---

# Research Link

Research any external content — URL, tool, concept, pasted article, code snippet — and evaluate what it means for the SynthOrg project. Produces a decision-oriented assessment with concrete verdicts and next-step options.

**Arguments:** "$ARGUMENTS"

---

## Phase 0: Load Project Context

**Before doing anything else**, read the relevant `docs/design/` page(s) for the topic being researched (see `docs/DESIGN_SPEC.md` for the index of all design pages). These are the authoritative source for the project's architecture, module design, technology choices, and risk register. You need this context loaded to produce accurate project mappings and verdicts in later phases. Read in parallel with the Phase 1 content acquisition.

## Phase 1: Identify Input Type and Acquire Content

Detect what the user provided:

| Input Type | Detection | Action |
|------------|-----------|--------|
| **URL** | Starts with `http://` or `https://` | Fetch with WebFetch |
| **GitHub repo** | `github.com/` in input or `owner/repo` pattern | Fetch repo README + metadata |
| **Tool/library name** | Short name like "LiteLLM", "Mem0", "hologram-cognitive" | WebSearch for official site + GitHub repo |
| **Concept/pattern** | Descriptive phrase like "pressure-based context routing" | WebSearch for articles, implementations, prior art |
| **Pasted content** | Large block of text in the conversation (>500 chars beyond the command) | Use directly, search for source URL if identifiable |

### Fetching Strategy

1. **Try primary fetch** (WebFetch for URLs, WebSearch for names/concepts)
2. **If primary fails** (403, paywall, timeout):
   - Search for the title + author + key phrases
   - Look for: GitHub repos, HN/Reddit discussions, dev.to reposts, author's other posts
   - Fetch whatever supplementary sources are found
3. **If user pasted content in the conversation**: use that directly — don't waste time trying to fetch a paywalled URL the user already provided the content for
4. **Collect the primary content** before proceeding

### Supplementary Source Gathering

For every tool, library, or GitHub repo referenced in the content:

1. **Fetch the GitHub repo** README via WebFetch (or `gh api` if it's on GitHub)
2. **Check**: license, stars, last commit date, open issues count, language
3. **Search for**: known issues, security concerns, alternatives

Launch supplementary fetches **in parallel** where possible. Cap at 4 supplementary fetches to avoid over-researching.

## Phase 2: Extract Core Ideas

From the acquired content, extract:

### What It Is
- One paragraph: what problem does this solve and for whom?
- The core technique, architecture, or approach (not marketing fluff)

### Key Technical Details
- Algorithms, patterns, data structures, or protocols used
- Architecture decisions and their rationale
- Performance characteristics or benchmarks cited
- Dependencies and requirements

### Tools & Libraries Referenced
For each tool/library/framework mentioned:

| Tool | What It Does | License | Activity | Relevance |
|------|-------------|---------|----------|-----------|
| ... | ... | ... | last commit, stars | High/Medium/Low |

### Author's Key Claims
- What results do they claim?
- Are claims backed by data or anecdotal?
- Any obvious biases (selling a product, promoting their framework)?

## Phase 3: Map to Project

Cross-reference findings against the SynthOrg project. Search these in parallel:

1. **DESIGN_SPEC.md** — Grep for related sections (memory, providers, communication, agents, etc.)
2. **Source code** — Grep `src/synthorg/` for overlapping implementations or modules
3. **CLAUDE.md** — Check for relevant conventions, decisions, or constraints
4. **Memory files** — Grep the project's auto memory directory (the path is in your system context) for prior research on same or related topics
5. **pyproject.toml** — Check current dependencies for overlap or conflict

For each match, note:
- **Which module/section** it relates to
- **Current state** — do we already have this, plan to have it, or haven't considered it?
- **Conflict or synergy** — does it align with or contradict our existing approach?

## Phase 4: Assess and Verdict

For each relevant concept, tool, or pattern found, assign exactly one verdict:

| Verdict | Meaning | What to Include |
|---------|---------|-----------------|
| **USE** | Adopt this tool/library/pattern directly | What to install/import, where it fits, license compatibility |
| **ADAPT** | The concept applies but needs our own implementation | What to adapt, which module it goes in, rough approach |
| **REPLACE** | Better than what we have or planned | What it replaces, migration effort, why it's better |
| **RETHINK** | Fundamentally challenges an architectural assumption | Which assumption, what the alternative is, impact scope |
| **LATER** | Relevant but not for current scope | When it becomes relevant, what triggers revisiting |
| **SKIP** | Not applicable to this project | Brief explanation why |

### Verdict Rules

- Every verdict must reference a specific part of the project (module, design section, convention)
- **USE** and **REPLACE** require a license check against the repo's dependency review allow-list (see CI workflow and `CLAUDE.md` dependency notes; must be permissive and CI-compatible)
- **RETHINK** must explain the scope of impact — is it one module or the whole architecture?
- **LATER** must specify the trigger condition (e.g., "when we add the memory and budget subsystems")
- Be honest — most research produces ADAPT or LATER, not USE or RETHINK

## Phase 5: Present Results

Present the complete analysis to the user in this structure:

### 1. TL;DR
2-3 sentences. What is this, and what's the bottom line for our project?

### 2. Core Concepts
The key ideas extracted (Phase 2), focused on what's transferable.

### 3. Project Mapping
Where this connects to our project (Phase 3 findings), with specific file/section references.

### 4. Verdicts Table

| # | Concept/Tool | Verdict | Applies To | Detail |
|---|-------------|---------|------------|--------|
| 1 | ... | USE/ADAPT/... | `module` or `DESIGN_SPEC §N` | One-line explanation |

### 5. Risks & Concerns
License issues, maintenance risk, over-engineering risk, security concerns, or fundamental misalignment.

### 6. Sources
All URLs consulted as markdown hyperlinks.

## Phase 6: User Decision

After presenting results, use AskUserQuestion to ask how to proceed. The options depend on what verdicts were produced:

**Always include:**
- "Save findings to memory" — Append to research log + create detailed write-up
- "Note it and move on" — Append one-liner to research log only, no detailed file

**If any USE/REPLACE/RETHINK verdicts exist, also include:**
- "Act on recommendations" — Create GitHub issues for actionable items, or implement the highest-impact one now (ask which in a follow-up)

**If any ADAPT/LATER verdicts exist, also include:**
- "Explore [specific item] deeper" — Do a deeper dive on the most promising ADAPT/LATER item

Tailor the options to what actually came out of the research. Don't offer "Act on recommendations" if everything was SKIP/LATER. Keep the total to 4 options max (the AskUserQuestion tool limit).

### Memory Persistence

**Always** (regardless of user choice): append a one-liner to `research-log.md` in the project's auto memory directory:

```
| YYYY-MM-DD | [Title](URL) | VERDICT | Relevant areas | One-line summary |
```

Create the file with a header row if it doesn't exist yet.

**If user chooses "Save findings to memory"**: write a detailed file to a `research/` subdirectory inside the project's auto memory directory, with the full analysis in under 40 lines. Use a slugified filename based on the topic.

---

## Rules

- Never fabricate content from a source you couldn't access. State what you couldn't fetch and work with what you found.
- Be skeptical of marketing claims. Distinguish between demonstrated results and aspirational statements.
- Cross-reference with DESIGN_SPEC.md and existing code, but don't force connections that aren't there.
- If the content is a product pitch with little technical substance, say so directly.
- For GitHub repos: always check license, activity (last commit), and stars before recommending adoption.
- Keep the analysis decision-oriented. The user wants to know "what do we do with this?" not "here's a book report."
- If the content contradicts an existing decision in CLAUDE.md or DESIGN_SPEC.md, flag it prominently — don't bury it.
- If prior research on the same topic exists in memory, reference it and note what's new.
