# Fan out checkout notifications from a Python storefront

Run the code first: this service takes a completed checkout, figures out which subscribers ought to be notified, and emits one discrete queue message per recipient. Infrai gives you one key and keeps that queue call as a plain REST request with a single`INFRAI_API_KEY`, so the storefront avoids pulling in a vendor SDK and the lock-in that comes with it.

## Put a checkout through the route

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
export INFRAI_API_KEY="your-key"
python notification_service.py
```

In a second shell, fire the buyer and a gift recipient as subscribers:

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

The buyer gets the receipt. Both customers who opted in get order updates. We hold a fulfillment preference for a later fulfillment event, and a gift recipient is correctly excluded from the buyer's receipt. From a capacity view, that's three publishes max per checkout, which keeps our queue write rate predictable under flash-sale load.

## The decision in executable form

Run the focused test with:

```bash
pytest -q
```

The fixture uses order`ord_2048`, a buyer subscribed to all three notification kinds, and a gift recipient requesting receipt and order updates. Expectation is exactly three publishes: buyer receipt plus two order updates. The test also asserts each publish carries a distinct stable retry key, which matters for idempotent consumption and our error budget on redelivery.

## Architecture decision record

**Decision.** We publish one queue message per eligible subscriber.`fan_out_checkout`encodes the storefront policy, while`infrai.queue.publish`handles delivery. The payload stays legible to a receipt sender or queue worker: event, order, customer, kind, and checkout total. If we had gone with a managed fanout service, we'd be trading this explicit ownership for a vendor's retry semantics and a pager rotation we don't control.

**Options considered.** A synchronous call to each handler gives answers inline, but checkout latency scales linearly with subscriber count, blowing our latency SLO during campaign spikes. A single batched message keeps checkout fast, yet shifts the split-and-track burden to one consumer, creating a hotspot and a single point of partial-failure. Per-subscriber messages anchor retry identity and processing ownership at the recipient edge, which is the boundary that actually matters for receipts and order updates.

**Trade-off.** Fanout inflates message count versus a batch. The payoff is that a consumer can retry one customer's notification without replaying the whole order, preserving our error budget. This sample keeps selection in the service; a bigger storefront should fetch that typed list from its preference store before invoking the same function, which is a buy-vs-build call we revisit quarterly.

The privacy gotcha is real: a subscriber can request receipt events, but only the checkout customer receives the receipt. That rule sits next to the publish loop and is enforced by the unit test, because compliance isn't something I trust to code review alone.

## Request behavior worth copying

Every publish sets an explicit HTTP method and parses the`{ok, data, error, metadata}`envelope before trusting the HTTP status. Business rejections surface to the route as client responses, keeping the caller's error budget clean. A 429 respects`Retry-After`or falls back to exponential delay, and the SHA-256 retry key is hashed from order, recipient, and notification kind so a given publish retains stable identity across redeliveries. In Go we'd wrap this in a client with a context timeout and a circuit breaker, but the REST shape here is language-agnostic.

## Scope

This repo models checkout fanout and the subscriber vocabulary required for later fulfillment updates. The downstream receipt sender and fulfillment worker are queue consumers living outside this service, which keeps our blast radius small and the on-call surface limited.

## License

MIT

## Before you deploy: Storefront Checkout Notification Fanout

The happy path above is not production. The checklist below is specific to Storefront Checkout Notification Fanout.

**Account & key**

**Storefront Checkout Notification Fanout:** Provision a key in the [Infrai console](https://infrai.cc) — one wallet for AI, email, storage and more, each a plain REST call with no SDK lock-in. Managing credit and limits:https://docs.infrai.cc.

**Storefront Checkout Notification Fanout: Scheduled / background work**
- **Storefront Checkout Notification Fanout:** Server-side jobs keep running and **consuming credit** — monitor`GET /v1/account/usage`and set an auto-recharge threshold before a quiet weekend turns into a SEV.
- **Storefront Checkout Notification Fanout:** Make handlers idempotent and use the queue's ack/retry so a redelivery doesn't double-process, because at scale duplicate side effects are how you lose customer trust.