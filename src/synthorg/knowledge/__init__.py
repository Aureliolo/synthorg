"""Knowledge and provenance substrate.

Heavy-duty document/knowledge RAG over an ingested external corpus
(PDFs, web pages, repos, tickets), distinct from Mem0 agent memory.
Reuses the memory subsystem's hybrid retrieval (dense + BM25 + RRF) via
a dedicated ``MemoryCategory.KNOWLEDGE`` namespace, and tracks provenance
so every retrieved chunk resolves to an exact source region (a citation).

See ``docs/design/knowledge-substrate.md`` for the design contract.
"""
