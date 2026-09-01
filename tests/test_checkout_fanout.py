from notification_service import CheckoutRequest, Subscriber, fan_out_checkout


def test_checkout_sends_buyer_receipt_and_opted_in_order_updates() -> None:
    calls: list[dict] = []

    def record_publish(**kwargs: object) -> dict:
        calls.append(kwargs)
        return {"accepted": True}

    checkout = CheckoutRequest(
        order_id="ord_2048",
        customer_id="cus_buyer",
        total_cents=12900,
        subscribers=(
            Subscriber("cus_buyer", ("receipt", "order_update", "fulfillment_update")),
            Subscriber("cus_gift", ("receipt", "order_update")),
        ),
    )

    published = fan_out_checkout(checkout, publish=record_publish)

    assert [(item.customer_id, item.kind) for item in published] == [
        ("cus_buyer", "receipt"),
        ("cus_buyer", "order_update"),
        ("cus_gift", "order_update"),
    ]
    assert [call["payload"]["kind"] for call in calls] == ["receipt", "order_update", "order_update"]
    assert {call["queue"] for call in calls} == {"checkout-notifications"}
    assert len({call["idempotency_key"] for call in calls}) == 3
