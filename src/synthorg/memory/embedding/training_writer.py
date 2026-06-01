# module-kind: code
"""Train/validation split + JSONL writing for the finetune pipeline.

Both the directory scan (``generate_training_data``) and the real-trajectory
harvest land their ``{query, positive_passage}`` records here so they share one
split rule and one on-disk format (the contrastive trainer reads
``training.jsonl`` / ``validation.jsonl``).
"""

import asyncio
import json
from pathlib import Path
from typing import Final

# A train/validation split needs at least one record on each side.
_MIN_TRAINING_PAIRS: Final[int] = 2


def _write_jsonl(path: Path, records: list[dict[str, str]]) -> None:
    """Write *records* as one JSON object per line."""
    path.write_text(
        "".join(f"{json.dumps(record)}\n" for record in records),
        encoding="utf-8",
    )


async def split_and_write_pairs(
    pairs: list[dict[str, str]],
    output_dir: str,
    *,
    validation_split: float,
) -> tuple[Path, Path]:
    """Split *pairs* into train/validation JSONL files under *output_dir*.

    Args:
        pairs: ``{query, positive_passage}`` records to write.
        output_dir: Directory the JSONL files are written into (created if
            absent).
        validation_split: Fraction held out for evaluation (0 < x < 1).

    Returns:
        ``(training_path, validation_path)``.

    Raises:
        ValueError: If ``validation_split`` is out of range or there are too
            few pairs to split.
    """
    if validation_split <= 0.0 or validation_split >= 1.0:
        msg = (
            f"validation_split must be between 0 and 1 exclusive, "
            f"got {validation_split}"
        )
        raise ValueError(msg)
    if len(pairs) < _MIN_TRAINING_PAIRS:
        msg = (
            f"Need at least {_MIN_TRAINING_PAIRS} query-passage pairs for a "
            f"train/validation split, got {len(pairs)}"
        )
        raise ValueError(msg)

    out = Path(output_dir)
    await asyncio.to_thread(lambda: out.mkdir(parents=True, exist_ok=True))

    raw_split = int(len(pairs) * (1 - validation_split))
    split_idx = max(1, min(len(pairs) - 1, raw_split))
    train_path = out / "training.jsonl"
    val_path = out / "validation.jsonl"
    await asyncio.to_thread(_write_jsonl, train_path, pairs[:split_idx])
    await asyncio.to_thread(_write_jsonl, val_path, pairs[split_idx:])
    return train_path, val_path
