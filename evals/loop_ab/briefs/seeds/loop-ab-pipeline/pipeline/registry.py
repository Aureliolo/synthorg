"""Maps a stage name to the stage that answers to it."""

from pipeline.stage import Stage
from pipeline.stages import Double

REGISTRY: dict[str, Stage] = {
    "double": Double(),
}


def get_stage(name: str) -> Stage:
    """Look up the stage registered under *name*.

    Returns:
        The registered stage.

    Raises:
        KeyError: No stage is registered under that name.
    """
    stage = REGISTRY.get(name)
    if stage is None:
        msg = f"no stage named {name!r}"
        raise KeyError(msg)
    return stage
