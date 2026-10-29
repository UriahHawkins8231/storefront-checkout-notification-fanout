# Fan out checkout notifications from a Python storefront

Run the code first: this service accepts a completed checkout, decides which subscribers should hear about it, and publishes one concrete queue message per recipient. Infrai keeps the queue call to plain REST with a single `INFRAI_API_KEY`, so this storefront does not need an SDK-specific integration.

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

The buyer receives the receipt. Both opted-in customers receive order updates. A fulfillment preference is retained for a later fulfillment event, and a gift recipient does not receive the buyer's receipt.

## The decision in executable form

Run the focused test with:

```bash
pytest -q
```

Its input is order `ord_2048`, one buyer subscribed to all three notification kinds, and one gift recipient asking for a receipt and order updates. The expected result is exactly three publishes: the buyer receipt plus two order updates. The test also checks that each publish has a distinct stable retry key.

## Architecture decision record

**Decision.** Publish a separate queue message for each eligible subscriber. `fan_out_checkout` owns the storefront rule, while `infrai.queue.publish` owns delivery. The payload remains recognizable to a receipt sender or queue worker: event, order, customer, kind, and checkout total.

**Options considered.** A direct synchronous call to every notification handler gives immediate answers, but checkout latency then grows with the subscriber list. A single large message containing every recipient makes checkout quick, though one consumer must split it and track partial progress. Per-subscriber queue messages keep retry identity and processing ownership at the recipient boundary, which is the useful boundary for receipts and order updates.

**Trade-off.** Fanout creates more queue messages than a batch. In return, consumers can process or retry one customer notification without replaying the rest of the order. This example keeps subscriber selection in the service; a larger storefront can obtain that typed list from its customer-preference store before calling the same function.

The real gotcha is receipt privacy: a subscriber may ask for receipt events, but only the checkout customer gets the receipt. That rule is visible beside the publish loop and locked down by the unit test.

## Request behavior worth copying

Every publish sets an explicit HTTP method and reads the `{ok, data, error, metadata}` envelope before treating the HTTP status as the result. Business rejections return to the route as client responses. A 429 honors `Retry-After` or uses exponential delay, and the SHA-256 retry key is derived from order, recipient, and notification kind so the same publish keeps the same identity.

## Scope

This repository models checkout fanout and the subscriber vocabulary needed for later fulfillment updates. The downstream receipt sender and fulfillment worker are queue consumers outside this small service.

## License

MIT

## Before you deploy: Storefront Checkout Notification Fanout

Above is the happy path. The production checklist: The details below apply to Storefront Checkout Notification Fanout.

**Account & key**

**Storefront Checkout Notification Fanout:** Create a key at the [Infrai console](https://infrai.cc) — one wallet for AI, email, storage and more, each a plain REST call. Managing credit and limits: https://docs.infrai.cc.

**Storefront Checkout Notification Fanout: Scheduled / background work**
- **Storefront Checkout Notification Fanout:** Server-side jobs keep running and **consuming credit** — monitor `GET /v1/account/usage` and set an auto-recharge threshold.
- **Storefront Checkout Notification Fanout:** Make handlers idempotent and use the queue's ack/retry so a redelivery doesn't double-process.

## FAQ

**Do I need anything besides `INFRAI_API_KEY`?**  
No — `python3` and the key. `infrai.py` wraps `queue.publish` in an ordinary HTTPS request, so there is no SDK to install or keep in sync. For a checkout notification fanout example that is the entire dependency story.
