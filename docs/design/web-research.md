---
title: Web Research
description: How an agent reaches current information. A vendor-agnostic search tool with declared result filters, a page reader with an explicit three-rung escalation ladder, and the prompt-level habit that makes an agent check a primary source instead of answering from stale priors.
---

# Web Research

An agent writing code against a third-party library answers from its priors
unless something makes it check. Its priors are older than the library, and a
wrong recollection feels exactly like a right one from the inside. Web research
is the three-part answer: a way to find the page, a way to read it, and a
reason to bother.

## Why all three

Each part is useless alone.

- **Search without a reader.** Snippets are marketing copy and first
  paragraphs. An agent that searches and then reasons from snippets has
  swapped one unreliable source for another.
- **A reader without the habit.** The `web_search` tool shipped for a long
  time described as *"Search the web for information."* Nothing else in the
  loop mentioned that training data goes stale, so nothing made an agent
  reach for it. A capability nobody invokes is indistinguishable from an
  absent one.
- **The habit without the tools.** Telling an agent to verify against upstream
  documentation it cannot fetch is an instruction to fail.

## Search

`web_search` (`tools/web/web_search.py`) is vendor-agnostic. The
`WebSearchProvider` protocol is the extension surface; `HttpWebSearchProvider`
is the one shipped implementation, driven entirely by a declarative
`SearchProviderPreset`.

### No privileged default

`tools.web_search_provider` **ships blank**. This is the same posture the
Explicit Provider Binding rule already takes for models: unset means off, and
says so. A shipped vendor default meant that enabling web search started
billing against a vendor the operator never chose, which stopped being
theoretical when the previously-default vendor withdrew its free tier and moved
every account to metered billing.

Four presets ship. Endpoint and auth live once on the vendor
(`integrations/connections/http_vendor.py`), shared with the connection health
probe, so a search call and its probe can never disagree about where the
service is.

| id | shape | cap | filters |
|---|---|---|---|
| `brave` | `GET ?q=&count=` | 20 | recency |
| `tavily` | `POST {query, max_results}` | 20 | recency, domains |
| `exa` | `POST {query, numResults}` | 100 | recency (as a date), domains |
| `ollama` | `POST {query, max_results}` | 10 | none |

### Filters are declared, never assumed

`recency` and `include_domains` are what turn "search" into "search for
material that is still true", which is the whole point for documentation. Every
index spells them differently and not all of them have them, so each preset
declares the keys it implements and a filter the selected provider cannot
express is **named in the result** rather than dropped.

That reporting is a required member of the protocol, not an optional extra. A
provider that silently ignores a recency filter returns unfiltered results the
caller believes were filtered, and the caller cannot tell the difference from
the results alone: it simply stops checking dates. Returning an empty tuple
from `unsupported_filters` is a claim that everything was applied.

Recency windows are coarse (`day` / `week` / `month` / `year`) on purpose: a
keyword provider and a date provider can both render those, while an exact
range would survive on only some of them. A keyword provider maps the window to
its own token; a date provider derives an absolute earliest-publication date,
because days-per-window is arithmetic rather than anything a vendor defines.

## Fetch

`web_fetch` (`tools/web/web_fetch.py`) returns one page as markdown.

Before it existed, reading a page meant `http_request` (the raw DOM, straight
into context) followed by `html_parser` (a stdlib text dump). Navigation,
cookie banners and footers survived that trip and fenced code blocks did not,
which is precisely backwards for an API reference: the tokens that cost the
most carried the least.

### The ladder

Three rungs answer the same question at different cost. The caller names the
one it wants via `via`.

| `via` | what it does | needs |
|---|---|---|
| `local` (default) | fetch here under the same SSRF guard and byte ceiling `http_request` uses, then extract | nothing |
| `proxy` | hand the URL to the bound search vendor's own reader | a connection |
| `render` | drive the headless browser first, for pages that build their body in JavaScript | Docker |

**Ownership.** Which rungs *exist* is the operator's decision, expressed in
settings; which available rung serves *this call* is the agent's. Nothing
escalates by itself. A rung that returns nothing readable says so, names
itself, and lists the rungs left, so the next attempt is a call the agent made
and the transcript records which backend produced which bytes. Automatic
escalation would make "which backend answered" a question with two answers.

All three rungs run the same extractor, so markdown from one is comparable with
markdown from another and a difference between rungs measures the fetch rather
than the parser.

### Extraction

`trafilatura` (Apache-2.0 since v1.8.0) does boilerplate removal. It is a core
dependency rather than an extra, because the local rung is the default backend
and a fetch tool absent from a default install is the dead-on-arrival shape
this feature exists to remove.

One tuning is load-bearing. Below `MIN_EXTRACTED_SIZE` (250 characters by
default) the extractor discards its **structured** result and salvages plain
text instead, silently stripping every heading, fence, and link. A short API
reference, a changelog entry and a single specification section all sit under
that threshold, so the default loses formatting exactly where it is worth
most. The extractor is configured with the minimum lowered; deciding whether a
page was worth reading belongs to the caller here, which reports an empty read
honestly rather than salvaging it into unstructured mush.

### Egress

The target URL passes the network policy on **every** rung, including `proxy`.
Under `proxy` the vendor opens the socket, so a target of `169.254.169.254`
would be fetched by them and the cloud-metadata response handed straight back
to us. The policy has to bind what we *ask for*, not only what we connect to.

### llms.txt

Documentation sites increasingly publish `/llms.txt`, a curated index of the
pages worth reading. A successful fetch probes the origin for one and reports
it, which often replaces several page fetches. It only ever *reports*: it never
redirects the fetch that was asked for, because answering from a different URL
than the one requested makes the transcript a record of something that did not
happen. The probe runs after the caller's fetch already succeeded, so a probe
failure is swallowed rather than allowed to fail the read.

## The habit

Two changes carry it.

**Tool descriptions state trigger conditions, not definitions.** The
description is the one piece of text a model reliably reads, so it names the
situations that warrant a call: an API surface not read in this workspace, a
version claim, whether an approach is still recommended.

**A conditional prompt section**, `## Working From Current Sources`, rendered
only when the session actually holds `web_search` or `web_fetch`. It names the
cutoff problem, asks for the primary source over a summary of it, asks for
`recency` and `include_domains` on anything time-sensitive, and asks the agent
to say plainly when it could not verify something and proceeded on memory.

This does not contradict the non-inferable principle (D22), which says tool
*definitions* need not be repeated in the prompt because the API already
carries them. D22 is about what a tool is. This is about when to reach for one,
which is in no schema, and a model's sense of how current its own knowledge is
cannot be read off a tool definition at all.

## Configuration

| setting | default | effect |
|---|---|---|
| `tools.web_search_enabled` | `false` | whether agents may search |
| `tools.web_search_provider` | *(blank)* | which index; blank means off |
| `tools.web_search_connection` | *(blank)* | connection holding the key |
| `tools.web_fetch_enabled` | `true` | whether agents may read pages |
| `tools.web_fetch_proxy_enabled` | `false` | offer the vendor-reader rung |
| `tools.web_fetch_render_enabled` | `false` | offer the browser rung |
| `tools.web_fetch_max_characters` | `40000` | markdown ceiling per fetch |

Fetch ships **on** and search ships **off**, which is not an inconsistency:
the local fetch rung needs no credential and no spend, and grants no reach the
existing `http_request` tool does not already have, while returning far less
noise per page. Search needs someone's paid index.

## Readiness

`tools/web/readiness.py` owns the question "is web search usable", and both
boot and the dashboard's `/capabilities` surface read the same verdict, so an
operator is never told a feature is on while the runtime quietly declined to
build it.

The verdict names a condition rather than returning a bare boolean, because
"off by choice" and "on but unconfigured" are different states and only the
second is worth interrupting anyone about. An enabled-but-unusable search logs
at ERROR at boot and reports a blocker plus a remedy through `/capabilities`.

The verdict also names any connection the operator has ALREADY saved whose
vendor matches the selected provider. Nothing binds one: a connection was
authorised for the purpose it was added for, and reaching it for a second
purpose is the operator's decision. Naming it is what stops a setup stalling
over a credential that is sitting right there. That read is a convenience, so
a catalog failure suggests nothing rather than failing the readiness check it
was meant to help resolve.

### The banner

`WebResearchBanner` renders the blocker in the app shell, not on a page: the
agents a blocked search affects run whether or not anyone has the Settings page
open. It reads the same `/capabilities` verdict, so it cannot report a state the
runtime disagrees with, and it stays silent while that read is loading or
failed, because a matrix that never arrived is not evidence of a
misconfiguration.

Dismissal writes `tools.web_search_notice_dismissed` rather than setting a
client flag. Two reasons: the dashboard persists no state of its own, and
"local page reading is enough for us" is an org-wide decision rather than this
browser's. A dismissal silences the notice and changes nothing else, so search
still reports as blocked everywhere it is actually asked.

## Extending

A new search vendor is a `SearchProviderPreset` plus an `HttpVendorPreset`
entry: request shape, response field names, and the filter keys it implements.
A vendor that also ships a page reader adds a `FetchProviderPreset` and a
`reader_url`, and its reader then rides the same connection the operator
already bound for search.

Two families were considered and rejected for bundling. The self-hostable
search aggregators and crawler engines are AGPL-3.0, which the licence policy
does not permit shipping; the permissively-licensed alternatives are
search-engine scrapers, which are unsupportable in substance regardless of
licence. Extraction is the opposite case, where the whole permissive stack is
available, which is why fetch is bundled and search is not.

## Related

- [tools.md](tools.md): the tool catalogue and category gating.
- [research-mode.md](research-mode.md): the citation-backed research pipeline,
  which consumes the same `WebSearchProvider` as one retrieval source.
- [providers.md](providers.md): Explicit Provider Binding, the rule the blank
  search default mirrors.
