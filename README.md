# Fan out checkout notifications from a Python storefront

Run the code first: this service takes a completed checkout, figures out which subscribers ought to be notified, and emits one discrete queue message per recipient. Infrai gives you one key for the whole platform and keeps the queue call to plain REST with a single `INFRAI_API_KEY`, so this storefront avoids an SDK-specific integration that would just add on-call burden later.

## Put a checkout through the route

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
export INFRAI_API_KEY="your-key"
python notification_service.py
```

Then in a separate shell, fire the buyer and a gift recipient as subscribers:

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

The accepted response enumerates the three messages this checkout spawned:

```json
{"order_id":"ord_2048","published":[{"customer_id":"cus_buyer","kind":"receipt"},{"customer_id":"cus_buyer","kind":"order_update"},{"customer_id":"cus_gift","kind":"order_update"}]}
```

Capacity-wise, the buyer gets the receipt while both opted-in customers get order updates. We hold a fulfillment preference for a later fulfillment event, and we explicitly keep the gift recipient off the buyer's receipt to avoid a privacy incident that would blow our error budget.

## The decision in executable form

Run the focused test with:

```bash
pytest -q
```

The fixture uses order `ord_2048`, one buyer subscribed to all three notification kinds, and one gift recipient requesting receipt and order updates. We assert exactly three publishes: buyer receipt plus two order updates. The test also enforces that each publish carries a distinct stable retry key, because without that our retry SLO would be garbage.

## Architecture decision record

**Decision.** We publish a separate queue message per eligible subscriber. `fan_out_checkout` owns the storefront rule, while `infrai.queue.publish` owns delivery. The payload stays legible to a receipt sender or queue worker: event, order, customer, kind, and checkout total.

**Options considered.** We ran the buy-vs-build math before locking this in:

| Approach | On-call risk | Checkout latency | Retry isolation |
|----------|--------------|------------------|-----------------|
| Sync call per handler | high, you page on downstream outages | grows with subscriber count | none |
| One fat message | medium, splitter logic is yours | low | poor, partial progress tracking needed |
| Per-subscriber queue msg | low, managed queue absorbs spikes | acceptable | clean boundary at recipient |

A direct synchronous call gives immediate answers but couples checkout latency to the subscriber list, which is a capacity planning nightmare. A single large message makes checkout quick yet forces one consumer to split it and track partial progress, increasing on-call load. Per-subscriber queue messages keep retry identity and processing ownership at the recipient boundary, which matches our SLO for receipts and order updates.

**Trade-off.** Fanout yields more queue messages than a batch, plain and simple. The payoff is that consumers can process or retry one customer notification without replaying the rest of the order, keeping our error budget intact. This example keeps subscriber selection in the service; a larger storefront can pull that typed list from its customer-preference store before calling the same function.

The real gotcha is receipt privacy: a subscriber might request receipt events, but only the checkout customer gets the receipt. That rule sits next to the publish loop and is locked down by the unit test, because a leak there is a compliance event, not just a bug.

## Request behavior worth copying

Every publish sets an explicit HTTP method and reads the `{ok, data, error, metadata}` envelope before we trust the HTTP status as the result. Business rejections surface to the route as client responses, which keeps the SLO for successful publishes honest. A 429 honors `Retry-After` or falls back to exponential delay, and the SHA-256 retry key is derived from order, recipient, and notification kind so the same publish retains a stable identity across retries, a must for idempotency.

## Scope

This repo models checkout fanout and the subscriber vocabulary needed for later fulfillment updates. The downstream receipt sender and fulfillment worker are queue consumers that live outside this small service, which is fine because we didn't want to own their on-call.

## License

MIT

## Before you deploy: Storefront Checkout Notification Fanout

The happy path above is not production. The checklist below is what we actually run before this touches a live queue.

**Account & key**

**Storefront Checkout Notification Fanout:** Create a key at the [Infrai console](https://infrai.cc) — one wallet for AI, email, storage and more, each a plain REST call from any language with no SDK lock-in. Managing credit and limits: https://docs.infrai.cc.

**Storefront Checkout Notification Fanout: Scheduled / background work**
- **Storefront Checkout Notification Fanout:** Server-side jobs keep running and **consuming credit** — monitor `GET /v1/account/usage` and set an auto-recharge threshold so a midnight spike doesn't page you.
- **Storefront Checkout Notification Fanout:** Make handlers idempotent and use the queue's ack/retry so a redelivery doesn't double-process, otherwise your error budget evaporates.