"""JetStream KV bucket operations for channel persistence.

Handles reading, writing, and scanning the KV bucket that stores
channel definitions so they are discoverable across processes.
"""

import asyncio
import json
from typing import TYPE_CHECKING

from synthorg.communication.bus._nats_state import _NatsState
from synthorg.communication.bus._nats_utils import decode_token, encode_token
from synthorg.communication.bus.errors import BusStreamError
from synthorg.communication.channel import Channel
from synthorg.communication.errors import ChannelAlreadyExistsError
from synthorg.core.critical_errors import reraise_critical
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.communication import (
    COMM_BUS_KV_READ_FAILED,
    COMM_BUS_KV_WRITE_FAILED,
)

if TYPE_CHECKING:
    # Entry is a nested class on KeyValue (not a module-level export), so it
    # cannot be imported directly.
    from nats.js.kv import KeyValue

    KvEntry = KeyValue.Entry
else:
    # nats-py is the optional ``[distributed]`` extra: present in dev/CI (so
    # typeguard checks the real types), ``object`` fallback otherwise so this
    # module still imports without the extra.
    try:
        from nats.js.kv import KeyValue

        KvEntry = KeyValue.Entry
    except ImportError:
        KeyValue = object
        KvEntry = object

logger = get_logger(__name__)


async def create_channel_in_kv(
    state: _NatsState,
    channel: Channel,
) -> None:
    """Atomically create a channel entry in KV (fails if it exists).

    Uses the KV ``create`` API which is an atomic create-if-not-exists.

    Raises:
        ChannelAlreadyExistsError: If the key already exists.
        BusStreamError: If a transport error prevents the write.
    """
    from nats.js.errors import (  # noqa: PLC0415
        KeyWrongLastSequenceError,
    )

    if state.kv is None:
        msg = "KV store unavailable -- cannot create channel"
        logger.error(
            COMM_BUS_KV_WRITE_FAILED,
            channel=channel.name,
            error=msg,
        )
        raise BusStreamError(msg, context={"channel": channel.name})
    key = encode_token(channel.name)
    value = channel.model_dump_json().encode("utf-8")
    try:
        await state.kv.create(key, value)
    except KeyWrongLastSequenceError:
        msg = f"Channel already exists in KV: {channel.name}"
        logger.warning(
            COMM_BUS_KV_WRITE_FAILED,
            channel=channel.name,
            error=msg,
            phase="atomic_create",
        )
        raise ChannelAlreadyExistsError(
            msg, context={"channel": channel.name}
        ) from None
    except Exception as exc:
        reraise_critical(exc)
        logger.warning(
            COMM_BUS_KV_WRITE_FAILED,
            channel=channel.name,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        msg = f"KV create failed for channel {channel.name!r}: {safe_error_description(exc)}"  # noqa: E501
        raise BusStreamError(msg, context={"channel": channel.name}) from exc


async def write_channel_to_kv(state: _NatsState, channel: Channel) -> None:
    """Persist a Channel definition to the KV bucket (best-effort update).

    Used for subscription changes and direct channel updates where
    local state is authoritative and KV persistence is secondary.
    Logs failures but does not raise.
    """
    if state.kv is None:
        return
    key = encode_token(channel.name)
    value = channel.model_dump_json().encode("utf-8")
    try:
        await state.kv.put(key, value)
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        reraise_critical(exc)
        logger.warning(
            COMM_BUS_KV_WRITE_FAILED,
            channel=channel.name,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )


async def load_channel_from_kv(
    state: _NatsState,
    channel_name: str,
) -> Channel | None:
    """Load a Channel definition from the KV bucket, if present.

    Returns:
        The decoded ``Channel``, or ``None`` if the key is absent.
    """
    entry = await fetch_kv_entry(state, channel_name)
    if entry is None:
        return None
    return decode_kv_channel(channel_name, entry)


async def fetch_kv_entry(
    state: _NatsState,
    channel_name: str,
) -> KvEntry | None:
    """Fetch a raw KV entry.

    Returns:
        The raw KV entry, or ``None`` for a genuine miss
        (``KeyNotFoundError`` or absent value).

    Raises:
        BusStreamError: On transport/connection errors.
    """
    from nats.js.errors import KeyNotFoundError  # noqa: PLC0415

    if state.kv is None:
        return None
    key = encode_token(channel_name)
    try:
        entry = await state.kv.get(key)
    except KeyNotFoundError:
        return None
    except Exception as exc:
        reraise_critical(exc)
        logger.warning(
            COMM_BUS_KV_READ_FAILED,
            channel=channel_name,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        msg = f"KV transport error for channel {channel_name!r}: {safe_error_description(exc)}"  # noqa: E501
        raise BusStreamError(msg, context={"channel": channel_name}) from exc
    if entry is None or entry.value is None:
        return None
    return entry


def decode_kv_channel(
    channel_name: str,
    entry: KvEntry,
) -> Channel:
    """Decode a KV entry into a Channel.

    Returns:
        The decoded ``Channel``.

    Raises:
        BusStreamError: If the entry has no value, contains invalid
            JSON, fails schema validation, or has a mismatched channel
            name.
    """
    if entry.value is None:
        msg = f"KV entry for channel {channel_name!r} has no value"
        logger.warning(
            COMM_BUS_KV_READ_FAILED,
            channel=channel_name,
            reason="entry_has_no_value",
            error_type=BusStreamError.__name__,
        )
        raise BusStreamError(msg, context={"channel": channel_name})
    try:
        data = json.loads(entry.value.decode("utf-8"))
        channel = Channel.model_validate(data)
    except json.JSONDecodeError as exc:
        logger.warning(
            COMM_BUS_KV_READ_FAILED,
            channel=channel_name,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        msg = f"Corrupt KV entry for channel {channel_name!r}: {safe_error_description(exc)}"  # noqa: E501
        raise BusStreamError(msg, context={"channel": channel_name}) from exc
    except ValueError as exc:
        logger.warning(
            COMM_BUS_KV_READ_FAILED,
            channel=channel_name,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        msg = f"Invalid KV data for channel {channel_name!r}: {safe_error_description(exc)}"  # noqa: E501
        raise BusStreamError(msg, context={"channel": channel_name}) from exc
    if channel.name != channel_name:
        mismatch = (
            f"KV entry name mismatch: expected {channel_name!r}, got {channel.name!r}"
        )
        logger.warning(
            COMM_BUS_KV_READ_FAILED,
            channel=channel_name,
            error=mismatch,
        )
        raise BusStreamError(mismatch, context={"channel": channel_name})
    return channel


async def scan_kv_channels(state: _NatsState) -> list[Channel]:
    """Scan the KV bucket for all persisted channels.

    Returns:
        The decoded channels persisted in the KV bucket.

    Raises:
        BusStreamError: If the KV bucket cannot be listed.
    """
    if state.kv is None:
        return []
    from nats.js.errors import NoKeysError  # noqa: PLC0415

    try:
        keys = await state.kv.keys()
    except NoKeysError:
        return []
    except Exception as exc:
        reraise_critical(exc)
        logger.warning(
            COMM_BUS_KV_READ_FAILED,
            channel="*",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
            phase="list_channels_scan",
        )
        msg = f"KV scan failed: {safe_error_description(exc)}"
        raise BusStreamError(msg, context={"phase": "list_channels_scan"}) from exc

    decoded_keys: list[tuple[str, str]] = []
    for key in keys:
        try:
            decoded_keys.append((key, decode_token(key)))
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            logger.warning(
                COMM_BUS_KV_READ_FAILED,
                channel=key,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
                phase="decode_token",
            )

    entries = await asyncio.gather(
        *(fetch_kv_entry(state, name) for _, name in decoded_keys),
        return_exceptions=True,
    )
    channels: list[Channel] = []
    for (_, decoded_name), entry in zip(decoded_keys, entries, strict=True):
        if isinstance(entry, BaseException) and not isinstance(entry, Exception):
            # CancelledError (and KeyboardInterrupt / SystemExit) must propagate,
            # not be swallowed: gather(return_exceptions=True) captures
            # cancellation as a result entry.
            raise entry
        if isinstance(entry, Exception) or entry is None:
            continue  # Transport/decode errors already logged inside helpers.
        try:
            ch = decode_kv_channel(decoded_name, entry)
        except BusStreamError:
            continue  # Already logged inside decode_kv_channel.
        channels.append(ch)
    return channels
