"""Golden-company benchmark (eval spine) for SynthOrg.

This top-level package implements issue #1980: a fixed brief suite the
studio sits as a scored exam at every release. The result is a
deterministic, per-release scorecard (JSON + Markdown) that downstream
issues (#1983 learning curve, #1990, #1995, #1998) consume.

Out-of-package on purpose: eval code does not ship with the wheel; the
suite is run from the repository as a benchmark harness, not as a
library users import.
"""

from typing import Final

SCORECARD_SCHEMA_VERSION: Final[int] = 1


__all__ = ["SCORECARD_SCHEMA_VERSION"]
