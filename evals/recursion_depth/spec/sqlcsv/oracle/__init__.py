"""The held-out oracle. Never copied into a workspace, never named in a brief.

Behavioural throughout: every test invokes the delivered CLI as a subprocess and
reads stdout, stderr and the exit code. Nothing imports the delivered code, so
no test constrains an implementation the spec left open, which is what lets one
oracle grade every decomposition the planner invents.
"""
