# module-kind: code
"""Conditional construction of the code-modification applier.

Extracted from :mod:`synthorg.meta.factory` so the factory stays within
its module-size budget. The applier is built only when GitHub credentials
and an absolute project root are configured; every other case fails closed
(logs a skip reason and leaves the ``CODE_MODIFICATION`` altitude unmapped)
so a misconfigured checkout never validates the wrong tree.
"""

from pathlib import Path

from synthorg.meta.config import CodeModificationConfig
from synthorg.meta.models import ProposalAltitude
from synthorg.meta.protocol import ProposalApplier
from synthorg.meta.validation.scope_validator import ScopeValidator
from synthorg.observability import get_logger
from synthorg.observability.events.meta import META_STRATEGY_REGISTERED

logger = get_logger(__name__)


def maybe_install_code_applier(
    appliers: dict[ProposalAltitude, ProposalApplier],
    code_cfg: CodeModificationConfig,
) -> None:
    """Install the code-modification applier into *appliers* when usable.

    Fails closed: missing GitHub credentials, an unset project root, or a
    non-absolute project root each log a skip reason and leave the
    ``CODE_MODIFICATION`` altitude unmapped rather than building an applier
    that could validate the wrong checkout.

    Args:
        appliers: Mutable altitude-to-applier map, extended in place.
        code_cfg: Code-modification configuration block.
    """
    if code_cfg.github_token is None or code_cfg.github_repo is None:
        logger.warning(
            META_STRATEGY_REGISTERED,
            altitude="code_modification_applier",
            reason="skipped_no_github_credentials",
        )
        return
    if code_cfg.project_root is None:
        # Fail closed: the CI validator must run against an explicit,
        # absolute checkout. Defaulting to the process CWD would point
        # ruff / mypy / pytest at whatever tree the worker happened to
        # start in, so an unset project_root disables the applier
        # rather than silently validating the wrong files.
        logger.warning(
            META_STRATEGY_REGISTERED,
            altitude="code_modification_applier",
            reason="skipped_no_project_root",
        )
        return
    project_root = Path(str(code_cfg.project_root))
    if not project_root.is_absolute():
        # Fail closed: a relative project_root resolves against the process
        # CWD, pointing ruff / mypy / pytest at whatever tree the worker
        # started in rather than the declared checkout. Disable the applier
        # instead of silently validating the wrong files.
        logger.warning(
            META_STRATEGY_REGISTERED,
            altitude="code_modification_applier",
            reason="skipped_non_absolute_project_root",
        )
        return

    from synthorg.meta.appliers.code_applier import CodeApplier  # noqa: PLC0415
    from synthorg.meta.appliers.github_client import (  # noqa: PLC0415
        HttpGitHubClient,
    )
    from synthorg.meta.validation.ci_validator import (  # noqa: PLC0415
        LocalCIValidator,
    )

    ci_validator = LocalCIValidator(
        project_root=project_root.resolve(),
        scope_validator=ScopeValidator(
            allowed_paths=tuple(code_cfg.allowed_paths),
            forbidden_paths=tuple(code_cfg.forbidden_paths),
        ),
        timeout_seconds=code_cfg.ci_timeout_seconds,
    )
    github_client = HttpGitHubClient(
        token=str(code_cfg.github_token),
        repo=str(code_cfg.github_repo),
        api_base_url=str(code_cfg.github_api_url),
        base_branch=str(code_cfg.base_branch),
        timeout=code_cfg.api_timeout_seconds,
    )
    appliers[ProposalAltitude.CODE_MODIFICATION] = CodeApplier(
        ci_validator=ci_validator,
        github_client=github_client,
        code_modification_config=code_cfg,
    )
