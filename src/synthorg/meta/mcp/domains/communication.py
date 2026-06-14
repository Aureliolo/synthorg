"""Communication domain MCP tools.

Covers messages, meetings, connections, webhooks, and tunnel.
"""

from typing import TYPE_CHECKING

from synthorg.communication.meeting.enums import MeetingStatus
from synthorg.meta.mcp.domains._remaining_args import (
    ConnectionsCheckHealthArgs,
    ConnectionsCreateArgs,
    ConnectionsDeleteArgs,
    ConnectionsGetArgs,
    ConnectionsListArgs,
    MeetingsCreateArgs,
    MeetingsDeleteArgs,
    MeetingsGetArgs,
    MeetingsListArgs,
    MeetingsUpdateArgs,
    MessagesDeleteArgs,
    MessagesGetArgs,
    MessagesListArgs,
    MessagesSendArgs,
    TunnelConnectArgs,
    TunnelGetStatusArgs,
    WebhooksCreateArgs,
    WebhooksDeleteArgs,
    WebhooksGetArgs,
    WebhooksListArgs,
    WebhooksUpdateArgs,
)
from synthorg.meta.mcp.tool_builder import (
    ADMIN_GUARDRAIL_PROPERTIES,
    ADMIN_GUARDRAIL_REQUIRED,
    PAGINATION_PROPERTIES,
    admin_tool,
    read_tool,
    write_tool,
)

if TYPE_CHECKING:
    from synthorg.meta.mcp.registry import MCPToolDef

COMMUNICATION_TOOLS: tuple[MCPToolDef, ...] = (
    # --- Messages ---
    read_tool(
        "messages",
        "list",
        "List messages with optional filtering.",
        {
            "channel": {"type": "string", "description": "Filter by channel"},
            **PAGINATION_PROPERTIES,
        },
        args_model=MessagesListArgs,
    ),
    read_tool(
        "messages",
        "get",
        "Get a message by channel + ID.",
        {
            "channel": {
                "type": "string",
                "description": "Channel containing the message",
            },
            "message_id": {"type": "string", "description": "Message UUID"},
        },
        required=("channel", "message_id"),
        args_model=MessagesGetArgs,
    ),
    write_tool(
        "messages",
        "send",
        "Send a new message.",
        {
            "message": {"type": "object", "description": "Message payload"},
        },
        required=("message",),
        args_model=MessagesSendArgs,
    ),
    admin_tool(
        "messages",
        "delete",
        "Delete a message (destructive; requires confirm).",
        {
            "message_id": {
                "type": "string",
                "description": "Message UUID",
                "minLength": 1,
            },
            **ADMIN_GUARDRAIL_PROPERTIES,
        },
        required=("message_id", *ADMIN_GUARDRAIL_REQUIRED),
        args_model=MessagesDeleteArgs,
    ),
    # --- Meetings ---
    read_tool(
        "meetings",
        "list",
        "List meeting records.",
        {
            "status": {
                "type": "string",
                "description": "Filter by status",
                "enum": [s.value for s in MeetingStatus],
            },
            "meeting_type": {
                "type": "string",
                "description": "Filter by meeting type",
            },
            **PAGINATION_PROPERTIES,
        },
        args_model=MeetingsListArgs,
    ),
    read_tool(
        "meetings",
        "get",
        "Get a meeting record by ID.",
        {
            "meeting_id": {"type": "string", "description": "Meeting UUID"},
        },
        required=("meeting_id",),
        args_model=MeetingsGetArgs,
    ),
    write_tool(
        "meetings",
        "create",
        "Create a meeting record.",
        {
            "title": {"type": "string", "description": "Meeting title"},
            "participants": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Participant names",
            },
        },
        required=("title",),
        args_model=MeetingsCreateArgs,
    ),
    write_tool(
        "meetings",
        "update",
        "Update a meeting record.",
        {
            "meeting_id": {"type": "string", "description": "Meeting UUID"},
            "updates": {"type": "object", "description": "Fields to update"},
        },
        required=("meeting_id", "updates"),
        args_model=MeetingsUpdateArgs,
    ),
    admin_tool(
        "meetings",
        "delete",
        "Delete a meeting record (destructive; requires confirm).",
        {
            "meeting_id": {
                "type": "string",
                "description": "Meeting UUID",
                "minLength": 1,
            },
            **ADMIN_GUARDRAIL_PROPERTIES,
        },
        required=("meeting_id", *ADMIN_GUARDRAIL_REQUIRED),
        args_model=MeetingsDeleteArgs,
    ),
    # --- Connections ---
    read_tool(
        "connections",
        "list",
        "List external connections.",
        PAGINATION_PROPERTIES,
        args_model=ConnectionsListArgs,
    ),
    read_tool(
        "connections",
        "get",
        "Get a connection by name.",
        {
            "name": {"type": "string", "description": "Connection name"},
        },
        required=("name",),
        args_model=ConnectionsGetArgs,
    ),
    admin_tool(
        "connections",
        "create",
        "Create a new external connection (admin; requires confirm).",
        {
            "name": {"type": "string", "description": "Connection name"},
            "connection_type": {"type": "string", "description": "Connection type"},
            "auth_method": {"type": "string", "description": "Authentication method"},
            "credentials": {
                "type": "object",
                "description": "Connection credentials (string values)",
                "additionalProperties": {"type": "string"},
            },
            "base_url": {"type": "string", "description": "Base URL"},
            "metadata": {
                "type": "object",
                "description": "Free-form connection metadata (string values)",
                "additionalProperties": {"type": "string"},
            },
            **ADMIN_GUARDRAIL_PROPERTIES,
        },
        required=(
            "name",
            "connection_type",
            "auth_method",
            *ADMIN_GUARDRAIL_REQUIRED,
        ),
        args_model=ConnectionsCreateArgs,
    ),
    admin_tool(
        "connections",
        "delete",
        "Delete an external connection (destructive; requires confirm).",
        {
            "name": {"type": "string", "description": "Connection name"},
            **ADMIN_GUARDRAIL_PROPERTIES,
        },
        required=("name", *ADMIN_GUARDRAIL_REQUIRED),
        args_model=ConnectionsDeleteArgs,
    ),
    read_tool(
        "connections",
        "check_health",
        "Check health of a connection.",
        {
            "name": {"type": "string", "description": "Connection name"},
        },
        required=("name",),
        args_model=ConnectionsCheckHealthArgs,
    ),
    # --- Webhooks ---
    read_tool(
        "webhooks",
        "list",
        "List registered webhooks.",
        PAGINATION_PROPERTIES,
        args_model=WebhooksListArgs,
    ),
    read_tool(
        "webhooks",
        "get",
        "Get a webhook by ID.",
        {
            "webhook_id": {"type": "string", "description": "Webhook UUID"},
        },
        required=("webhook_id",),
        args_model=WebhooksGetArgs,
    ),
    admin_tool(
        "webhooks",
        "create",
        "Create a new webhook (admin; requires confirm).",
        {
            "definition": {
                "type": "object",
                "description": "WebhookDefinition payload",
            },
            **ADMIN_GUARDRAIL_PROPERTIES,
        },
        required=("definition", *ADMIN_GUARDRAIL_REQUIRED),
        args_model=WebhooksCreateArgs,
    ),
    admin_tool(
        "webhooks",
        "update",
        "Update a webhook configuration (admin; requires confirm).",
        {
            "definition": {
                "type": "object",
                "description": "WebhookDefinition payload (including id)",
            },
            **ADMIN_GUARDRAIL_PROPERTIES,
        },
        required=("definition", *ADMIN_GUARDRAIL_REQUIRED),
        args_model=WebhooksUpdateArgs,
    ),
    admin_tool(
        "webhooks",
        "delete",
        "Delete a webhook (destructive; requires confirm).",
        {
            "webhook_id": {"type": "string", "description": "Webhook UUID"},
            **ADMIN_GUARDRAIL_PROPERTIES,
        },
        required=("webhook_id", *ADMIN_GUARDRAIL_REQUIRED),
        args_model=WebhooksDeleteArgs,
    ),
    # --- Tunnel ---
    read_tool(
        "tunnel",
        "get_status",
        "Get tunnel connection status.",
        args_model=TunnelGetStatusArgs,
    ),
    admin_tool(
        "tunnel",
        "connect",
        "Establish a tunnel connection (admin; requires confirm).",
        ADMIN_GUARDRAIL_PROPERTIES,
        required=ADMIN_GUARDRAIL_REQUIRED,
        args_model=TunnelConnectArgs,
    ),
)
