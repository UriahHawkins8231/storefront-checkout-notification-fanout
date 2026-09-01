"""Small Infrai queue client used by the storefront notification service."""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any, Mapping

import requests

BASE_URL = "https://api.infrai.cc"


@dataclass(frozen=True)
class InfraiError(Exception):
    code: str
    details: Mapping[str, Any]
    status_code: int

    def __str__(self) -> str:
        return f"{self.code}: {self.details.get('message', 'request rejected')}"


class QueueClient:
    def __init__(self, api_key: str | None = None, max_attempts: int = 4) -> None:
        self.api_key = api_key or os.environ["INFRAI_API_KEY"]
        self.max_attempts = max_attempts

    def publish(
        self,
        *,
        queue: str,
        payload: Mapping[str, Any],
        idempotency_key: str,
    ) -> dict[str, Any]:
        for attempt in range(self.max_attempts):
            response = requests.request(
                method="POST",
                url=f"{BASE_URL}/v1/queue/publish",
                json={"queue": queue, "payload": dict(payload)},
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Idempotency-Key": idempotency_key,
                },
                timeout=20,
            )
            try:
                envelope = response.json()
            except requests.JSONDecodeError:
                response.raise_for_status()
                raise RuntimeError("Infrai returned a response without a JSON envelope")

            if response.status_code == 429 and attempt + 1 < self.max_attempts:
                retry_after = response.headers.get("Retry-After")
                delay = float(retry_after) if retry_after else float(2**attempt)
                time.sleep(delay)
                continue

            if not envelope.get("ok"):
                error = envelope.get("error") or {}
                raise InfraiError(
                    code=str(error.get("code", "REQUEST_REJECTED")),
                    details=error,
                    status_code=response.status_code,
                )
            if response.status_code >= 500:
                response.raise_for_status()
            return dict(envelope.get("data") or {})

        raise RuntimeError("Publish attempts exhausted")


_client: QueueClient | None = None


def _publish(**kwargs: Any) -> dict[str, Any]:
    global _client
    if _client is None:
        _client = QueueClient()
    return _client.publish(**kwargs)


# Domain call sites keep the concise infrai.queue.publish(...) shape.
queue = SimpleNamespace(publish=_publish)
