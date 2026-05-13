"""Concrete mutator implementations for the rollback executor.

Each mutator satisfies one of the four protocols declared in
``synthorg.meta.rollout.inverse_dispatch`` (``ConfigMutator``,
``PromptMutator``, ``ArchitectureMutator``, ``CodeMutator``) and
routes writes through a real backing store. The rollback executor
constructs the ``MappingProxyType[NotBlankStr, RollbackHandler]`` from
these collaborators via ``build_rollback_executor`` in
``synthorg.meta.factory``.
"""

from synthorg.meta.rollout.mutators.code_mutator import WorkspaceCodeMutator
from synthorg.meta.rollout.mutators.config_mutator import (
    SettingsServiceConfigMutator,
)

__all__ = [
    "SettingsServiceConfigMutator",
    "WorkspaceCodeMutator",
]
