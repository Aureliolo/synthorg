"""Structural erosion metrics for quality degradation detection.

The best-of-K candidate scoring this package once housed (self-consistency
filtering, verbalized confidence scoring, trace-length scoring, PTE
efficiency, budget-guarded sampling) had no production caller and was
removed. Structural erosion detection is the surviving, live consumer
(``synthorg.engine.stagnation.quality_erosion_detector``); it is imported
directly from ``structural_erosion``, not re-exported from here.
"""
