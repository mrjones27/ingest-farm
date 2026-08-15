from __future__ import annotations

import json
from typing import Any

import redis

from ingest_farm.config import get_settings

CHANNEL_START_QUEUE = "ingest:jobs:start"
CHANNEL_STOP_QUEUE = "ingest:jobs:stop"
RECORDING_COMPLETE_QUEUE = "ingest:events:recording_complete"
POSTPROCESS_QUEUE = "ingest:jobs:postprocess"


def get_redis() -> redis.Redis:
    return redis.from_url(get_settings().redis_url, decode_responses=True)


def publish_event(queue: str, payload: dict[str, Any]) -> None:
    get_redis().lpush(queue, json.dumps(payload))


def blocking_pop(queue: str, timeout: int = 5) -> dict[str, Any] | None:
    client = get_redis()
    result = client.brpop(queue, timeout=timeout)
    if result is None:
        return None
    _, raw = result
    return json.loads(raw)


def try_pop(queue: str) -> dict[str, Any] | None:
    raw = get_redis().rpop(queue)
    if raw is None:
        return None
    return json.loads(raw)
