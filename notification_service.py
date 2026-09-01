"""Checkout route and notification fanout for a small storefront."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, Literal, Mapping, Sequence

import infrai
from infrai import InfraiError

NotificationKind = Literal["receipt", "order_update", "fulfillment_update"]
CHECKOUT_QUEUE = "checkout-notifications"


@dataclass(frozen=True)
class Subscriber:
    customer_id: str
    kinds: tuple[NotificationKind, ...]

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "Subscriber":
        allowed = {"receipt", "order_update", "fulfillment_update"}
        kinds = tuple(str(kind) for kind in value["kinds"])
        if not kinds or any(kind not in allowed for kind in kinds):
            raise ValueError("subscriber kinds must contain supported notification kinds")
        return cls(customer_id=str(value["customer_id"]), kinds=kinds)  # type: ignore[arg-type]


@dataclass(frozen=True)
class CheckoutRequest:
    order_id: str
    customer_id: str
    total_cents: int
    subscribers: tuple[Subscriber, ...]

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "CheckoutRequest":
        total_cents = int(value["total_cents"])
        if total_cents < 0:
            raise ValueError("total_cents must be non-negative")
        return cls(
            order_id=str(value["order_id"]),
            customer_id=str(value["customer_id"]),
            total_cents=total_cents,
            subscribers=tuple(Subscriber.from_dict(item) for item in value["subscribers"]),
        )


@dataclass(frozen=True)
class PublishedNotification:
    customer_id: str
    kind: NotificationKind


Publisher = Callable[..., dict[str, Any]]


def fan_out_checkout(request: CheckoutRequest, publish: Publisher = infrai.queue.publish) -> list[PublishedNotification]:
    """Publish a receipt to the buyer and order updates to opted-in subscribers."""
    planned: list[PublishedNotification] = []
    for subscriber in request.subscribers:
        for kind in subscriber.kinds:
            if kind == "fulfillment_update":
                continue
            if kind == "receipt" and subscriber.customer_id != request.customer_id:
                continue
            notification = PublishedNotification(subscriber.customer_id, kind)
            payload = {
                "event": "checkout.completed",
                "order_id": request.order_id,
                "customer_id": notification.customer_id,
                "kind": notification.kind,
                "total_cents": request.total_cents,
            }
            identity = f"{request.order_id}:{notification.customer_id}:{notification.kind}"
            key = hashlib.sha256(identity.encode("utf-8")).hexdigest()
            publish(queue=CHECKOUT_QUEUE, payload=payload, idempotency_key=key)
            planned.append(notification)
    return planned


class CheckoutHandler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        if self.path != "/checkout":
            self._json(404, {"error": "route not found"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            raw = json.loads(self.rfile.read(length))
            request = CheckoutRequest.from_dict(raw)
            published = fan_out_checkout(request)
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            self._json(400, {"error": str(error)})
            return
        except InfraiError as error:
            status = error.status_code if 400 <= error.status_code < 500 else 502
            self._json(status, {"error": error.code, "details": dict(error.details)})
            return
        self._json(
            202,
            {"order_id": request.order_id, "published": [item.__dict__ for item in published]},
        )

    def _json(self, status: int, body: Mapping[str, Any]) -> None:
        encoded = json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, format: str, *args: object) -> None:
        return


def serve(port: int = 8000) -> None:
    print(f"Checkout notification service listening on http://127.0.0.1:{port}")
    ThreadingHTTPServer(("127.0.0.1", port), CheckoutHandler).serve_forever()


if __name__ == "__main__":
    serve()
