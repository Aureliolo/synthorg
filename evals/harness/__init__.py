"""The recording spine every real-spend eval harness runs on.

A harness that measures anything against real providers needs the same four
things, none of which is about what it measures: a process that serves its own
LLM gateway (because the gateway verifies bearers only its own in-memory signer
minted, so borrowing someone else's backend cannot work), a per-run bearer and a
sandboxed tool registry bound to one workspace, a workspace recreated from a
committed fixture so runs stay comparable, and a stall watch plus a transcript
so an hours-long recording is observable while it happens.

This package holds those. What is measured, how it is scored, and what artifact
comes out belong to the harness that imports them.
"""
