"""Generative-RAG synthesis for the knowledge substrate.

Turns retrieved, cited chunks into a grounded answer whose every claim cites
at least one chunk. A :class:`KnowledgeCitationBinder` validates each cited
reference resolves to a retrieved chunk before the answer is emitted, so a
synthesised answer is always verifiable against the corpus.
"""
