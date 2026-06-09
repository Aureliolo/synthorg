# module-kind: controller
"""Local-provider model lifecycle: pull (SSE), delete, reconfigure."""

import asyncio
import json as _json
from collections.abc import AsyncIterator
from typing import Annotated

from litestar import Controller, delete, post, put
from litestar.datastructures import State
from litestar.params import PathParameter
from litestar.response import ServerSentEvent
from litestar.status_codes import HTTP_204_NO_CONTENT

from synthorg.api.controllers._provider_helpers import sse_error
from synthorg.api.controllers._workflow_helpers import audit_actor_from_context
from synthorg.api.dto import ApiResponse
from synthorg.api.dto_providers import (
    ProviderModelResponse,
    PullModelRequest,
    UpdateModelConfigRequest,
    to_provider_model_response,
)
from synthorg.api.guards import require_ceo_or_manager
from synthorg.api.path_params import PathName
from synthorg.api.rate_limits import (
    per_op_concurrency_from_policy,
    per_op_rate_limit_from_policy,
)
from synthorg.api.state import AppState
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.domain_errors import (
    DomainError,
    NotFoundError,
    ValidationError,
)
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.api import (
    API_MODEL_OPERATION_FAILED,
    API_RESOURCE_NOT_FOUND,
    API_SSE_PULL_MODEL_FAILED,
    API_VALIDATION_FAILED,
)
from synthorg.providers.errors import (
    ProviderModelNotFoundError,
    ProviderNotFoundError,
    ProviderValidationError,
)
from synthorg.providers.state import provider_management_of

logger = get_logger(__name__)


class ProviderLocalModelsController(Controller):
    """Pull, delete, and reconfigure models on local providers."""

    path = "/providers"
    tags = ("providers",)

    @post(
        "/{name:str}/models/pull",
        guards=[
            require_ceo_or_manager,
            per_op_rate_limit_from_policy("providers.pull_model", key="user"),
        ],
        opt=per_op_concurrency_from_policy(
            "providers.pull_model",
            key="user",
        ),
        media_type="text/event-stream",
    )
    async def pull_model(
        self,
        state: State,
        name: PathName,
        data: PullModelRequest,
    ) -> ServerSentEvent:
        """Pull a model on a local provider (SSE streaming).

        Args:
            state: Application state.
            name: Provider name.
            data: Pull request with model name.

        Returns:
            SSE stream of pull progress events.

        Raises:
            CancelledError: Raised on the corresponding failure path.
        """
        app_state: AppState = state.app_state
        svc = provider_management_of(app_state)

        async def _event_stream() -> AsyncIterator[dict[str, str]]:
            # Carve-out: SSE responses cannot raise domain exceptions
            # to the central RFC 9457 handler because the response
            # headers (``Content-Type: text/event-stream``, etc.) are
            # already on the wire by the time the first event yields.
            # Errors emitted after stream start MUST use this in-stream
            # ``event: error`` schema; ``sse_error()`` produces the
            # documented payload shape so clients can discriminate
            # in-stream errors from connection failures.  The same
            # applies to ``except ProviderValidationError`` below.
            """Return event stream.

            Raises:
                CancelledError: Raised on the corresponding failure path.
            """
            try:
                async for event in svc.pull_model(name, data.model_name):
                    if event.done and event.error:
                        event_type = "error"
                    elif event.done:
                        event_type = "complete"
                    else:
                        event_type = "progress"
                    yield {
                        "event": event_type,
                        "data": _json.dumps(event.model_dump()),
                    }
            except ProviderNotFoundError:
                yield {
                    "event": "error",
                    "data": _json.dumps(
                        sse_error(f"Provider {name!r} not found"),
                    ),
                }
            except ProviderValidationError as exc:
                yield {
                    "event": "error",
                    "data": _json.dumps(sse_error(safe_error_description(exc))),
                }
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                reraise_critical(exc)
                logger.warning(
                    API_SSE_PULL_MODEL_FAILED,
                    provider=name,
                    model=data.model_name,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                yield {
                    "event": "error",
                    "data": _json.dumps(
                        sse_error(
                            f"Internal error: {type(exc).__name__}",
                        ),
                    ),
                }

        return ServerSentEvent(content=_event_stream())

    @delete(
        "/{name:str}/models/{model_id:path}",
        guards=[
            require_ceo_or_manager,
            per_op_rate_limit_from_policy("providers.delete_model", key="user"),
        ],
        status_code=HTTP_204_NO_CONTENT,
    )
    async def delete_model(
        self,
        state: State,
        name: PathName,
        model_id: Annotated[
            str,
            PathParameter(
                max_length=256,
                min_length=1,
                description="Local provider model id (may contain colons).",
            ),
        ],
    ) -> None:
        """Delete a model from a local provider.

        Args:
            state: Application state.
            name: Provider name.
            model_id: Model identifier (may contain colons).

        Raises:
            NotFoundError: Raised on the corresponding failure path.
            ValidationError: Raised on the corresponding failure path.
            DomainError: Raised on the corresponding failure path.
        """
        app_state: AppState = state.app_state
        actor = audit_actor_from_context()
        try:
            await provider_management_of(app_state).delete_model(
                name,
                model_id,
                actor=actor,
            )
        except ProviderNotFoundError as exc:
            msg = f"Provider {name!r} not found"
            logger.warning(
                API_RESOURCE_NOT_FOUND,
                resource="provider",
                name=name,
            )
            raise NotFoundError(msg) from exc
        except ProviderValidationError as exc:
            logger.warning(
                API_VALIDATION_FAILED,
                resource="provider",
                name=name,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise ValidationError(safe_error_description(exc)) from exc
        except ValueError as exc:
            logger.warning(
                API_RESOURCE_NOT_FOUND,
                resource="model",
                name=model_id,
                provider=name,
            )
            raise NotFoundError(str(exc)) from exc
        except RuntimeError as exc:
            logger.warning(
                API_MODEL_OPERATION_FAILED,
                resource="model",
                operation="delete",
                name=model_id,
                provider=name,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise DomainError(safe_error_description(exc)) from exc

    @put(
        "/{name:str}/models/{model_id:path}/config",
        guards=[
            require_ceo_or_manager,
            per_op_rate_limit_from_policy(
                "providers.update_model_config",
                key="user",
            ),
        ],
    )
    async def update_model_config(
        self,
        state: State,
        name: PathName,
        model_id: Annotated[
            str,
            PathParameter(
                max_length=256,
                min_length=1,
                description="Local provider model whose launch parameters to update.",
            ),
        ],
        data: UpdateModelConfigRequest,
    ) -> ApiResponse[ProviderModelResponse]:
        """Update per-model launch parameters for a local provider.

        Args:
            state: Application state.
            name: Provider name.
            model_id: Model identifier.
            data: New launch parameters.

        Returns:
            Updated model response.

        Raises:
            NotFoundError: If the provider or model does not exist.
            ValidationError: If the launch parameters are unsupported.
            DomainError: Raised on the corresponding failure path.
        """
        app_state: AppState = state.app_state
        actor = audit_actor_from_context()
        try:
            updated = await provider_management_of(app_state).update_model_config(
                name,
                model_id,
                data.local_params,
                actor=actor,
            )
        except ProviderNotFoundError as exc:
            msg = f"Provider {name!r} not found"
            logger.warning(
                API_RESOURCE_NOT_FOUND,
                resource="provider",
                name=name,
            )
            raise NotFoundError(msg) from exc
        except ProviderModelNotFoundError as exc:
            logger.warning(
                API_RESOURCE_NOT_FOUND,
                resource="model",
                name=model_id,
                provider=name,
            )
            raise NotFoundError(safe_error_description(exc)) from exc
        except ProviderValidationError as exc:
            logger.warning(
                API_VALIDATION_FAILED,
                resource="provider",
                name=name,
                model=model_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise ValidationError(safe_error_description(exc)) from exc
        model = next(
            (m for m in updated.models if m.id == model_id),
            None,
        )
        if model is None:
            msg = f"Model {model_id!r} missing from updated config"
            logger.error(
                API_MODEL_OPERATION_FAILED,
                resource="model",
                operation="config_update",
                name=model_id,
                provider=name,
                error=msg,
            )
            raise DomainError(msg)
        return ApiResponse(data=to_provider_model_response(model))
