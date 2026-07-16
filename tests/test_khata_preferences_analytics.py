from __future__ import annotations

from datetime import datetime

import pytest

from kirana_agent.domain.errors import DomainError


def _customer(store, *, name: str = "Ramesh", source: str = "customer-ramesh"):
    return store.service.create_customer(
        owner_id="owner-test",
        source_event_id=source,
        name=name,
        phone=f"90000{abs(hash(source)) % 100000:05d}",
        state_code="29",
    )


def test_khata_charge_partial_payment_and_duplicate_payment(store) -> None:
    customer = _customer(store)
    charge = store.service.record_khata_charge(
        owner_id="owner-test",
        source_event_id="khata-charge-500",
        customer_id=customer["id"],
        amount_rupees="500.00",
        note="Monthly groceries",
    )
    payment_arguments = {
        "owner_id": "owner-test",
        "source_event_id": "khata-payment-300",
        "customer_id": customer["id"],
        "amount_rupees": "300.00",
        "payment_mode": "UPI",
        "payment_reference": "UPI-RAMESH-300",
    }
    payment = store.service.record_khata_payment(**payment_arguments)
    replay = store.service.record_khata_payment(**payment_arguments)

    assert charge["balance_paise"] == 50_000
    assert payment["balance_paise"] == 20_000
    assert replay == payment
    balance = store.service.get_khata_balance(customer["id"])
    assert balance["balance_paise"] == 20_000
    assert balance["entry_count"] == 2


def test_khata_payment_requires_an_existing_ledger(store) -> None:
    customer = _customer(store, name="No Ledger", source="customer-no-ledger")

    with pytest.raises(DomainError) as error:
        store.service.record_khata_payment(
            owner_id="owner-test",
            source_event_id="missing-ledger-payment",
            customer_id=customer["id"],
            amount_rupees="10.00",
            payment_mode="CASH",
        )

    assert error.value.code == "KHATA_NOT_FOUND"
    assert store.service.get_khata_balance(customer["id"])["balance_paise"] == 0


def test_khata_overpayment_is_atomic(store) -> None:
    customer = _customer(store, name="Over Pay", source="customer-overpay")
    store.service.record_khata_charge(
        owner_id="owner-test",
        source_event_id="overpay-charge",
        customer_id=customer["id"],
        amount_rupees="200.00",
        note="Groceries",
    )

    with pytest.raises(DomainError) as error:
        store.service.record_khata_payment(
            owner_id="owner-test",
            source_event_id="overpay-attempt",
            customer_id=customer["id"],
            amount_rupees="200.01",
            payment_mode="CASH",
        )

    assert error.value.code == "KHATA_OVERPAYMENT"
    balance = store.service.get_khata_balance(customer["id"])
    assert balance["balance_paise"] == 20_000
    assert balance["entry_count"] == 1


def test_preferences_survive_session_rotation_and_seed_new_draft(store) -> None:
    stored = store.service.set_preference(
        owner_id="owner-test",
        source_event_id="set-default-upi",
        key="default_payment_mode",
        value="upi",
    )
    first_session = store.service.get_agent_session_id("memory-chat")
    rotated = store.service.rotate_agent_session("memory-chat")
    second_session = store.service.get_agent_session_id("memory-chat")

    assert stored["value"] == "UPI"
    assert first_session.endswith(":g1")
    assert rotated["session_id"].endswith(":g2")
    assert second_session.endswith(":g2")
    assert store.service.get_preferences("owner-test")["default_payment_mode"] == "UPI"

    draft = store.service.start_bill_draft(
        owner_id="owner-test",
        chat_id="memory-chat",
        source_event_id="memory-new-draft",
    )
    assert draft["payment_mode"] == "UPI"


def test_daily_analytics_reconcile_sales_tender_tax_and_top_products(store) -> None:
    five_percent = store.create_stocked_product(
        gst_rate_bps=500,
        stock_units=10,
        sell_price="105.00",
        label="analytics-five",
    )
    eighteen_percent = store.create_stocked_product(
        gst_rate_bps=1800,
        stock_units=10,
        sell_price="118.00",
        label="analytics-eighteen",
    )
    cash_bill = store.finalize_sale(
        product_id=five_percent["id"],
        quantity=2,
        chat_id="analytics-cash",
        payment_mode="CASH",
    )
    upi_bill = store.finalize_sale(
        product_id=eighteen_percent["id"],
        quantity=1,
        chat_id="analytics-upi",
        payment_mode="UPI",
        payment_reference="UPI-ANALYTICS",
    )
    today = datetime.now(store.service.timezone).date().isoformat()

    summary = store.service.daily_summary(today)

    assert summary["totals"]["bill_count"] == 2
    assert summary["totals"]["gross_paise"] == cash_bill["gross_paise"] + upi_bill["gross_paise"]
    assert summary["totals"]["taxable_paise"] + summary["totals"]["gst_paise"] == summary["totals"]["gross_paise"]
    payment_mix = {row["payment_mode"]: row for row in summary["payment_mix"]}
    assert payment_mix["CASH"]["bill_count"] == 1
    assert payment_mix["CASH"]["gross_paise"] == cash_bill["gross_paise"]
    assert payment_mix["UPI"]["bill_count"] == 1
    assert payment_mix["UPI"]["gross_paise"] == upi_bill["gross_paise"]
    assert {row["gst_rate_bps"] for row in summary["gst_by_slab"]} == {500, 1800}
    assert {row["product_id"] for row in summary["top_products"]} == {
        five_percent["id"],
        eighteen_percent["id"],
    }

    first_close = store.service.close_day(
        owner_id="owner-test",
        source_event_id="close-day-first",
        business_date=today,
    )
    replay_close = store.service.close_day(
        owner_id="owner-test",
        source_event_id="close-day-second",
        business_date=today,
    )
    assert first_close["already_closed"] is False
    assert replay_close["already_closed"] is True
    assert replay_close["summary"]["totals"] == first_close["summary"]["totals"]
