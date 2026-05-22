"""Research mode: a real research subsystem for synthetic organisations.

Given a research brief, the subsystem plans queries, fans out across
multiple retrieval sources (the internal knowledge substrate plus
vendor-agnostic web, academic, and code search providers), triages each
source for credibility, deduplicates, and synthesises a citation-backed
:class:`~synthorg.research.models.ResearchReport` whose every claim
resolves to a retrievable source.

Every run is recorded as a :class:`~synthorg.research.models.ResearchRun`.
LLM calls replay through the cassette provider and retrieval results are
served back from the persisted run, so a whole run is deterministically
replayable under the evaluation harness.
"""
