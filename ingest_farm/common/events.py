from __future__ import annotations

import json
from typing import Any

import redis

from ingest_farm.config import get_settings

CHANNEL_CONNECT_QUEUE = "ingest:jobs:connect"
CHANNEL_DISCONNECT_QUEUE = "ingest:jobs:disconnect"
CHANNEL_RECORD_START_QUEUE = "ingest:jobs:record_start"
CHANNEL_RECORD_STOP_QUEUE = "ingest:jobs:record_stop"
# Legacy aliases kept for older scripts during transition.
CHANNEL_START_QUEUE = CHANNEL_RECORD_START_QUEUE
CHANNEL_STOP_QUEUE = CHANNEL_RECORD_STOP_QUEUE
RECORDING_COMPLETE_QUEUE = "ingest:events:recording_complete"
POSTPROCESS_QUEUE = "ingest:jobs:postprocess"


def channel_stats_key(channel_id: str) -> str:
    return f"ingest:channel:{channel_id}:stats"


def set_channel_stats(channel_id: str, stats: dict[str, Any], *, ttl_sec: int = 10) -> None:
    client = get_redis(socket_timeout=5)
    client.set(channel_stats_key(channel_id), json.dumps(stats), ex=ttl_sec)


def get_channel_stats(channel_id: str) -> dict[str, Any] | None:
    raw = get_redis(socket_timeout=5).get(channel_stats_key(channel_id))
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def clear_channel_stats(channel_id: str) -> None:
    get_redis(socket_timeout=5).delete(channel_stats_key(channel_id))


def get_redis(*, socket_timeout: float | None = None) -> redis.Redis:
    # Blocking pops need socket_timeout=None (or > BRPOP timeout).
    # Health checks should pass a short timeout to avoid hanging.
    return redis.from_url(
        get_settings().redis_url,
        decode_responses=True,
        socket_timeout=socket_timeout,
        socket_connect_timeout=5,
    )


def publish_event(queue: str, payload: dict[str, Any]) -> None:
    get_redis(socket_timeout=5).lpush(queue, json.dumps(payload))


def blocking_pop(queue: str, timeout: int = 5) -> dict[str, Any] | None:
    client = get_redis(socket_timeout=None)
    try:
        result = client.brpop(queue, timeout=timeout)
    except redis.TimeoutError:
        return None
    if result is None:
        return None
    _, raw = result
    return json.loads(raw)


def try_pop(queue: str) -> dict[str, Any] | None:
    raw = get_redis(socket_timeout=5).rpop(queue)
    if raw is None:
        return None
    return json.loads(raw)
