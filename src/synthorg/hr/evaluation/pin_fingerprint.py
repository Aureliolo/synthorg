# module-kind: code
"""Fingerprint + committed golden artifact for pin-validation drift.

A pin fingerprint is a stable digest of the contract a prompt class's
model pin asserts: the tier model id, the sampling parameters, and the
output a deterministic provider produces for the canonical probe. The
:mod:`synthorg.hr.evaluation.pin_validation_benchmark` recomputes a live
fingerprint per prompt class and compares it against ``pin_golden.json``,
a committed snapshot regenerated only by a deliberate human run of
``scripts/refresh_model_pin_golden.py``. A mismatch (a tier change, a
sampling change, or a probe-pipeline change) is drift.

The golden is an *independent* snapshot, so the check is "live equals
snapshot", not "pin equals pin": the benchmark is a genuine regression
gate, not a tautology. The artifact is committed and packaged alongside
this module so it is readable at runtime regardless of the working
directory.
"""

import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Final

from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.model_pins import (
    MODEL_PIN_GOLDEN_ABSENT,
    MODEL_PIN_GOLDEN_MALFORMED,
)

logger = get_logger(__name__)

GOLDEN_PATH: Final[Path] = Path(__file__).with_name("pin_golden.json")


def pin_fingerprint(
    *,
    model_id: str,
    temperature: float,
    top_p: float,
    max_tokens: int,
    output: str,
) -> str:
    """Compute the stable drift fingerprint for a pinned prompt class.

    The sampling floats are serialised via their exact ``float.hex()``
    representation, not a rounded presentation format: every distinct
    ``temperature`` / ``top_p`` value hashes differently (so a real pin
    change cannot be masked) while staying bit-reproducible across runs
    and platforms.

    Returns:
        The hex SHA-256 digest of the canonical pin-plus-output string.
    """
    canonical = f"{model_id}|{temperature.hex()}|{top_p.hex()}|{max_tokens:d}|{output}"
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def load_pin_golden(path: Path | None = None) -> Mapping[str, str]:
    """Load the committed golden fingerprint map.

    Args:
        path: Override for the golden-artifact path (tests). Defaults to
            the packaged ``pin_golden.json``.

    Returns:
        A map of ``prompt_class_id`` to expected fingerprint; an empty
        map when the artifact is absent.

    Raises:
        ValueError: When the artifact is present but malformed (a
            committed golden is expected to be valid, so a parse failure
            is surfaced rather than silently swallowed).
    """
    golden_path = path if path is not None else GOLDEN_PATH
    if not golden_path.exists():
        logger.warning(
            MODEL_PIN_GOLDEN_ABSENT,
            path=str(golden_path),
            consequence="every prompt class will report pin drift",
            action="run scripts/refresh_model_pin_golden.py to regenerate",
        )
        return {}
    raw = golden_path.read_text(encoding="utf-8")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        logger.warning(
            MODEL_PIN_GOLDEN_MALFORMED,
            path=str(golden_path),
            reason="not valid JSON",
            error=safe_error_description(exc),
        )
        msg = f"pin golden artifact {golden_path} is not valid JSON"
        raise ValueError(msg) from exc
    if not isinstance(payload, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in payload.items()
    ):
        logger.warning(
            MODEL_PIN_GOLDEN_MALFORMED,
            path=str(golden_path),
            reason="top-level JSON is not a str-to-str object",
        )
        msg = (
            f"pin golden artifact {golden_path} must be a JSON object "
            f"mapping prompt_class_id to fingerprint"
        )
        raise ValueError(msg)
    return dict(payload)


def golden_diff(
    live: Mapping[str, str],
    golden: Mapping[str, str],
) -> tuple[str, ...]:
    """Return the prompt-class ids whose live fingerprint drifted.

    A class is drifted when it is absent from the golden or its live
    fingerprint differs. The regen script reports it so a maintainer sees
    exactly which pins changed before the golden is overwritten.

    Returns:
        Sorted tuple of drifted ``prompt_class_id`` values.
    """
    drifted = [
        class_id
        for class_id, fingerprint in live.items()
        if golden.get(class_id) != fingerprint
    ]
    return tuple(sorted(drifted))


__all__ = ["GOLDEN_PATH", "golden_diff", "load_pin_golden", "pin_fingerprint"]
