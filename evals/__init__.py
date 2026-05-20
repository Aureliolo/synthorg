"""Golden-company benchmark (eval spine) for SynthOrg.

A fixed brief suite that runs as a scored exam at every release,
producing a deterministic, per-release scorecard (JSON + Markdown).
The scorecard is a stable reference for measuring studio quality
against a calibrated grading rubric, run-to-run.

Out-of-package on purpose: eval code does not ship with the wheel.
The suite is a benchmark harness, not a library users import.
"""

from evals.models.scorecard import SCORECARD_SCHEMA_VERSION

__all__ = ["SCORECARD_SCHEMA_VERSION"]
