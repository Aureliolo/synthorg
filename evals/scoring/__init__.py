"""Scoring primitives for the eval spine.

Four concerns split into modules:

* :mod:`evals.scoring.penalties` -- per-event deduction constants
  + the penalty table.
* :mod:`evals.scoring.executable` -- subprocess-driven binary grading
  for ``kind=executable`` briefs.
* :mod:`evals.scoring.judged` -- calibrated-judge grading with an
  ordinal Spearman gate for ``kind=judged`` briefs.
* :mod:`evals.scoring.aggregate` -- combine raw grade with process-fact
  penalties to produce the final BriefResult.
"""
