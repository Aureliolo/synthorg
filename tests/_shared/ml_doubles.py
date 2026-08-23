"""Stand-ins for the optional machine-learning packages.

``torch`` and ``sentence_transformers`` are an optional extra that most test
environments never install, and the guards that import them are annotated
``-> ModuleType``. A plain object stand-in therefore fails the runtime type
check before the code under test is reached, so the doubles here are real
module objects with the attributes the caller reads written into them.
"""

from types import ModuleType


def module_double(name: str, **attributes: object) -> ModuleType:
    """Build a module object named *name* carrying *attributes*.

    Args:
        name: The module name, as it would appear on a real import.
        attributes: What the code under test reads off the module.

    Returns:
        The populated module.
    """
    module = ModuleType(name)
    module.__dict__.update(attributes)
    return module


def torch_double(cuda: object) -> ModuleType:
    """Build a ``torch`` stand-in whose ``cuda`` attribute is *cuda*.

    Args:
        cuda: The CUDA namespace stand-in, or ``None`` where the code under
            test never reaches it.

    Returns:
        The ``torch`` stand-in.
    """
    return module_double("torch", cuda=cuda)
