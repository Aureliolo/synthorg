"""Stakeholder plan-review panel.

A greenlit plan is reviewed by a bounded panel of the leads whose lens it most
needs (technical, budget, and the department heads for the domains its owners
touch) plus a senior peer, before it reaches the human approver. Each panellist
runs a bounded, read-only persona review session that raises structured
findings and a verdict; the panel is consolidated into one
:class:`~synthorg.core.plan_review.PlanReview` attached to the durable plan.
"""
