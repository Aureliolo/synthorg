"""Load brief YAML files into frozen :class:`Brief` instances.

The loader is the file-system boundary: every YAML payload is validated
against :class:`evals.models.brief.Brief` before it leaves this module.
Downstream code never sees a raw dict.
"""

from typing import TYPE_CHECKING, cast

import yaml

from evals.errors import BriefSuiteDuplicateIdError, BriefSuiteEmptyError
from evals.models.brief import Brief
from synthorg.api.boundary import parse_typed
from synthorg.observability import get_logger

if TYPE_CHECKING:
    from pathlib import Path

logger = get_logger(__name__)


def load_brief_suite(briefs_dir: Path) -> tuple[Brief, ...]:
    """Load every ``*.yaml`` file under *briefs_dir* into a :class:`Brief`.

    Files whose name begins with an underscore (e.g. ``_schema.md``,
    ``_drafts.yaml``) are skipped so the directory can carry schema
    documentation and in-progress drafts without polluting the suite.

    Args:
        briefs_dir: Directory containing brief YAML files.

    Returns:
        Tuple of validated :class:`Brief` instances, sorted by
        ``brief_id`` for deterministic suite ordering.

    Raises:
        BriefSuiteEmptyError: If no eligible YAML files were found.
        BriefSuiteDuplicateIdError: If two briefs share a ``brief_id``.
        pydantic.ValidationError: If a YAML file's payload does not
            match the :class:`Brief` schema.
    """
    files = sorted(p for p in briefs_dir.glob("*.yaml") if not p.name.startswith("_"))
    briefs: list[Brief] = []
    for path in files:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            msg = (
                f"Brief file {path.name!r}: top-level YAML must be a mapping "
                f"(got {type(raw).__name__})"
            )
            raise TypeError(msg)
        brief = parse_typed("evals.brief", cast("dict[str, object]", raw), Brief)
        briefs.append(brief)

    if not briefs:
        msg = f"No brief files under {briefs_dir}"
        raise BriefSuiteEmptyError(msg)

    seen: dict[str, Path] = {}
    for brief, path in zip(briefs, files, strict=True):
        if brief.brief_id in seen:
            msg = (
                f"Duplicate brief_id {brief.brief_id!r}: "
                f"{seen[brief.brief_id].name} and {path.name}"
            )
            raise BriefSuiteDuplicateIdError(msg)
        seen[brief.brief_id] = path

    return tuple(sorted(briefs, key=lambda b: b.brief_id))


__all__ = ["load_brief_suite"]
