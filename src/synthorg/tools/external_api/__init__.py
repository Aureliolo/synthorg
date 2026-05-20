"""Governed external API/data access tool.

A first-class, governed wrapper over existing infrastructure (connection
catalog + secret backends for credential brokering, the bus-coordinated
sliding-window rate limiter, the SSRF ``NetworkPolicy`` + DNS-pinning egress
guard, and the approval gate). It replaces ad hoc curl-in-sandbox with a
single tool that brokers credentials, enforces rate limits, constrains egress,
and routes sensitive calls to human approval.
"""
