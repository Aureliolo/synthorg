"""API-layer transport controllers for the A2A federation feature.

The JSON-RPC gateway and the well-known Agent Card controller live here
(distinct from the REST controllers under ``api/controllers/``): the
gateway speaks JSON-RPC 2.0 and maps domain errors to JSON-RPC error
envelopes rather than the RFC 9457 envelope the REST controllers raise
through the centralised handler. They consume the a2a domain package
(``synthorg.a2a``) plus the shared api transport utilities.
"""
