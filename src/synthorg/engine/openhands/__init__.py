# module-kind: code
"""OpenHands alternative inner ExecutionLoop.

Wraps the OpenHands coding agent as a second ``ExecutionLoop`` so it can be
A/B'd against the native one. The adapter drives the harness through a
minimal conversation Protocol, maps its event stream to ``TurnRecord``s,
and consults the budget / shutdown / cancellation checkers at event
boundaries, so all loop logic is testable without the SDK. The harness's
LLM is pointed at the in-process gateway and its credentialed tools at the
credentialed-MCP server; both governance boundaries are the load-bearing
controls, with the docker-per-agent container as the sandbox.
"""
