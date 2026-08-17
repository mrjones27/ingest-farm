from __future__ import annotations

import json
from collections.abc import Sequence
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

_redis_clients: dict[tuple[str, float | None], redis.Redis] = {}


def channel_stats_key(channel_id: str) -> str:
    return f"ingest:channel:{channel_id}:stats"


def set_channel_stats(channel_id: str, stats: dict[str, Any], *, ttl_sec: int = 10) -> None:
    client = get_redis(socket_timeout=5)
    client.set(channel_stats_key(channel_id), json.dumps(stats), ex=ttl_sec)


def _decode_stats(raw: str | None) -> dict[str, Any] | None:
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def get_channel_stats(channel_id: str) -> dict[str, Any] | None:
    return _decode_stats(get_redis(socket_timeout=5).get(channel_stats_key(channel_id)))


def get_channel_stats_many(channel_ids: Sequence[str]) -> dict[str, dict[str, Any] | None]:
    """Read many channels' stats in one round trip.

    Listing channels one GET at a time multiplies any Redis latency by the
    number of channels.
    """
    ids = list(channel_ids)
    if not ids:
        return {}
    raws = get_redis(socket_timeout=5).mget([channel_stats_key(cid) for cid in ids])
    return {cid: _decode_stats(raw) for cid, raw in zip(ids, raws, strict=True)}


def clear_channel_stats(channel_id: str) -> None:
    get_redis(socket_timeout=5).delete(channel_stats_key(channel_id))


def get_redis(*, socket_timeout: float | None = None) -> redis.Redis:
    # Blocking pops need socket_timeout=None (or > BRPOP timeout).
    # Health checks should pass a short timeout to avoid hanging.
    settings = get_settings()
    key = (settings.redis_url, socket_timeout)
    client = _redis_clients.get(key)
    if client is None:
        client = redis.from_url(
            settings.redis_url,
            decode_responses=True,
            socket_timeout=socket_timeout,
            socket_connect_timeout=5,
        )
        _redis_clients[key] = client
    return client


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
