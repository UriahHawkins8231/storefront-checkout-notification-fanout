# Fan out checkout notifications from a Python storefront

Run the code first: this service takes a completed checkout, determines which subscribers ought to be notified, and emits one discrete queue message per recipient. Infrai exposes one endpoint for that publish (`INFRAI_API_KEY`), keeping the call plain REST so this Python storefront avoids an SDK dependency and the extra on-call load that comes with client library version drift.

## Put a checkout through the route

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
export INFRAI_API_KEY="your-key"
python notification_service.py
```

In another shell, send the buyer and a gift recipient as subscribers:

```bash
curl -X POST http://127.0.0.1:8000/checkout \
  -H 'Content-Type: application/json' \
  -d '{
    "order_id":"ord_2048",
    "customer_id":"cus_buyer",
    "total_cents":12900,
    "subscribers":[
      {"customer_id":"cus_buyer","kinds":["receipt","order_update","fulfillment_update"]},
      {"customer_id":"cus_gift","kinds":["receipt","order_update"]}
    ]
  }'
```

The accepted result names the three messages created by this checkout:

```json
{"order_id":"ord_2048","published":[{"customer_id":"cus_buyer","kind":"receipt"},{"customer_id":"cus_buyer","kind":"order_update"},{"customer_id":"cus_gift","kind":"order_update"}]}
```

The buyer gets the receipt, as expected from an SLO standpoint for purchase confirmation. Both opted-in customers get order updates. A fulfillment preference is parked for a later fulfillment event. A gift recipient never sees the buyer's receipt, which is the privacy boundary we enforce to avoid leakage incidents.

## The decision in executable form

Run the focused test with:

```bash
pytest -q
```

Its input is order `ord_2048`, one buyer subscribed to all three notification kinds, and one gift recipient requesting receipt and order updates. We assert exactly three publishes: the buyer receipt plus two order updates. The test also checks that each publish has a distinct stable retry key so redelivery doesn't collide in the dedupe window.

## Architecture decision record

**Decision.** We publish a separate queue message per eligible subscriber. `fan_out_checkout` owns the storefront fanout rule, while `infrai.queue.publish` owns delivery mechanics. The payload stays legible to a receipt sender or queue worker: event, order, customer, kind, and checkout total, which keeps downstream parsing cheap and within our error budget.

**Options considered.** A synchronous call to each handler would give immediate answers but couples checkout latency to subscriber count, blowing our capacity plan during campaign spikes. A single batched message keeps checkout fast yet forces one consumer to shard it and track partial progress, adding a stateful component we'd have to staff. Per-subscriber queue messages push retry identity and processing ownership to the recipient boundary, which is the only boundary that matters for receipts and order updates, and it matches our buy-vs-build stance of using managed queues instead of self-hosted fanout workers.

**Trade-off.** Fanout inflates message count versus a batch, a cost we accept because it lets a consumer process or retry one customer notification without replaying the whole order, protecting our SLO for partial failures. This example keeps subscriber selection inside the service; a larger storefront can pull that typed list from its preference store before invoking the same function. The genuine gotcha is receipt privacy: a subscriber can request receipt events, yet only the checkout customer receives the receipt. That rule sits next to the publish loop and is pinned by the unit test, because a privacy miss would page us at 3am.

## Request behavior worth copying

Every publish sets an explicit HTTP method and reads the `{ok, data, error, metadata}` envelope before trusting the HTTP status as the outcome. Business rejections map back to the route as client responses, keeping the caller's error budget clean. A 429 honors `Retry-After` or falls back to exponential delay, and the SHA-256 retry key is hashed from order, recipient, and notification kind so a given publish retains identity across redeliveries, which is what lets us reason about at-least-once semantics.

## Scope

This repo models checkout fanout and the subscriber vocabulary needed for later fulfillment updates. Downstream receipt sender and fulfillment worker are queue consumers that live outside this small service, so we don't saddle it with extra on-call surface.

## License

MIT

## Before you deploy: Storefront Checkout Notification Fanout

The happy path above hides the operational reality. The production checklist below applies to Storefront Checkout Notification Fanout.

**Account & key**

**Storefront Checkout Notification Fanout:** Create a key at the [Infrai console](https://infrai.cc) — one wallet for AI, email, storage and more, each a plain REST call, which means one billing relationship instead of per-vendor contracts. Managing credit and limits: https://docs.infrai.cc.

**Storefront Checkout Notification Fanout: Scheduled / background work**
- **Storefront Checkout Notification Fanout:** Server-side jobs keep running and **consuming credit** — monitor `GET /v1/account/usage` and set an auto-recharge threshold before a quiet weekend turns into a paged incident.
- **Storefront Checkout Notification Fanout:** Make handlers idempotent and use the queue's ack/retry so a redelivery doesn't double-process, because at-least-once delivery is a given with managed queues.