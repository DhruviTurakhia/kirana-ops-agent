from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from kirana_agent.domain.errors import DomainError
from kirana_agent.domain.money import inclusive_tax_breakdown


def _start_and_add(store, product_id: int, *, chat_id: str, quantity: int = 1):
    draft = store.service.start_bill_draft(
        owner_id="owner-test",
        chat_id=chat_id,
        source_event_id=f"start-{chat_id}",
        payment_mode="CASH",
    )
    return store.service.patch_bill_draft(
        owner_id="owner-test",
        source_event_id=f"patch-{chat_id}",
        draft_id=draft["id"],
        expected_revision=draft["revision"],
        operations=[
            {
                "action": "add",
                "product_id": product_id,
                "quantity": quantity,
                "unit": "packet",
            }
        ],
    )


def test_stock_receipt_is_idempotent_and_conflicting_replay_is_rejected(store) -> None:
    product = store.create_product(label="receipt")
    arguments = {
        "owner_id": "owner-test",
        "source_event_id": "receive-update-42",
        "product_id": product["id"],
        "quantity": 10,
        "unit": "packet",
        "unit_cost_rupees": "70.00",
        "new_mrp_rupees": "120.00",
        "new_sell_price_rupees": "100.00",
        "supplier_reference": "SUP-42",
    }

    first = store.service.receive_stock(**arguments)
    replay = store.service.receive_stock(**arguments)

    assert replay == first
    assert first["stock_atomic"] == 10_000
    assert store.stock_atomic(product["id"]) == 10_000
    assert store.scalar(
        "SELECT COUNT(*) FROM stock_movements WHERE product_id = ? AND movement_type = 'RECEIPT'",
        (product["id"],),
    ) == 1

    with pytest.raises(DomainError) as error:
        store.service.receive_stock(**{**arguments, "quantity": 11})

    assert error.value.code == "IDEMPOTENCY_CONFLICT"
    assert store.stock_atomic(product["id"]) == 10_000


def test_draft_edits_do_not_decrement_stock(store) -> None:
    first = store.create_stocked_product(label="draft-a", stock_units=10)
    second = store.create_stocked_product(label="draft-b", stock_units=10)
    draft = store.service.start_bill_draft(
        owner_id="owner-test",
        chat_id="draft-edits",
        source_event_id="draft-edits-start",
        payment_mode="CASH",
    )

    draft = store.service.patch_bill_draft(
        owner_id="owner-test",
        source_event_id="draft-edits-add",
        draft_id=draft["id"],
        expected_revision=draft["revision"],
        operations=[
            {"action": "add", "product_id": first["id"], "quantity": 2, "unit": "packet"},
            {"action": "add", "product_id": second["id"], "quantity": 1, "unit": "packet"},
        ],
    )
    assert draft["revision"] == 2
    assert draft["line_count"] == 2
    assert store.stock_atomic(first["id"]) == 10_000
    assert store.stock_atomic(second["id"]) == 10_000

    draft = store.service.patch_bill_draft(
        owner_id="owner-test",
        source_event_id="draft-edits-change",
        draft_id=draft["id"],
        expected_revision=draft["revision"],
        operations=[
            {"action": "set", "product_id": first["id"], "quantity": 3, "unit": "packet"},
            {"action": "remove", "product_id": second["id"]},
        ],
    )

    assert draft["revision"] == 3
    assert draft["line_count"] == 1
    assert draft["lines"][0]["quantity_atomic"] == 3_000
    assert store.stock_atomic(first["id"]) == 10_000
    assert store.stock_atomic(second["id"]) == 10_000
    assert store.scalar("SELECT COUNT(*) FROM stock_movements WHERE movement_type = 'SALE'") == 0


def test_mixed_rate_preview_and_finalize_reconcile_line_tax_and_stock(store) -> None:
    products = [
        store.create_stocked_product(
            gst_rate_bps=rate,
            stock_units=5,
            sell_price="100.00",
            label=f"mixed-{rate}",
        )
        for rate in (0, 500, 1200, 1800)
    ]
    draft = store.service.start_bill_draft(
        owner_id="owner-test",
        chat_id="mixed-rate-bill",
        source_event_id="mixed-start",
        payment_mode="CASH",
    )
    draft = store.service.patch_bill_draft(
        owner_id="owner-test",
        source_event_id="mixed-lines",
        draft_id=draft["id"],
        expected_revision=draft["revision"],
        operations=[
            {"action": "add", "product_id": product["id"], "quantity": 1, "unit": "packet"}
            for product in products
        ],
    )

    preview = store.service.preview_bill(
        draft_id=draft["id"], expected_revision=draft["revision"]
    )

    assert {line["gst_rate_bps"] for line in preview["lines"]} == {0, 500, 1200, 1800}
    assert preview["totals"]["gross_paise"] == 40_000
    for line in preview["lines"]:
        expected = inclusive_tax_breakdown(10_000, line["gst_rate_bps"])
        assert line["taxable_paise"] == expected.taxable_paise
        assert line["cgst_paise"] == expected.cgst_paise
        assert line["sgst_paise"] == expected.sgst_paise
        assert line["gst_paise"] == expected.gst_paise
    assert preview["totals"]["taxable_paise"] + preview["totals"]["gst_paise"] == 40_000

    bill = store.service.finalize_bill(
        owner_id="owner-test",
        source_event_id="mixed-finalize",
        draft_id=draft["id"],
        expected_revision=draft["revision"],
        preview_hash=preview["preview_hash"],
    )

    assert bill["gross_paise"] == 40_000
    assert bill["taxable_paise"] + bill["gst_paise"] == bill["gross_paise"]
    assert store.scalar("SELECT COUNT(*) FROM bill_items WHERE bill_id = ?", (bill["id"],)) == 4
    assert store.scalar(
        "SELECT COUNT(*) FROM stock_movements WHERE reference_id = ? AND movement_type = 'SALE'",
        (bill["id"],),
    ) == 4
    for product in products:
        assert store.stock_atomic(product["id"]) == 4_000


def test_oversell_is_refused_before_any_sale_side_effect(store) -> None:
    product = store.create_stocked_product(label="oversell", stock_units=2)
    draft = _start_and_add(store, product["id"], chat_id="oversell", quantity=3)

    with pytest.raises(DomainError) as error:
        store.service.preview_bill(
            draft_id=draft["id"], expected_revision=draft["revision"]
        )

    assert error.value.code == "INSUFFICIENT_STOCK"
    assert store.stock_atomic(product["id"]) == 2_000
    assert store.scalar("SELECT COUNT(*) FROM bills") == 0
    assert store.scalar("SELECT COUNT(*) FROM stock_movements WHERE movement_type = 'SALE'") == 0


def test_duplicate_finalize_returns_the_original_bill_without_second_decrement(store) -> None:
    product = store.create_stocked_product(label="duplicate-finalize", stock_units=5)
    draft = _start_and_add(store, product["id"], chat_id="duplicate-finalize", quantity=2)
    preview = store.service.preview_bill(
        draft_id=draft["id"], expected_revision=draft["revision"]
    )
    arguments = {
        "owner_id": "owner-test",
        "source_event_id": "duplicate-finalize-update",
        "draft_id": draft["id"],
        "expected_revision": draft["revision"],
        "preview_hash": preview["preview_hash"],
    }

    first = store.service.finalize_bill(**arguments)
    replay = store.service.finalize_bill(**arguments)

    assert replay["id"] == first["id"]
    assert replay["invoice_number"] == first["invoice_number"]
    assert store.stock_atomic(product["id"]) == 3_000
    assert store.scalar("SELECT COUNT(*) FROM bills WHERE draft_id = ?", (draft["id"],)) == 1
    assert store.scalar(
        "SELECT COUNT(*) FROM stock_movements WHERE reference_id = ? AND movement_type = 'SALE'",
        (first["id"],),
    ) == 1


def test_edit_after_preview_makes_old_revision_unfinalizable(store) -> None:
    product = store.create_stocked_product(label="stale-revision", stock_units=5)
    draft = _start_and_add(store, product["id"], chat_id="stale-revision", quantity=1)
    preview = store.service.preview_bill(
        draft_id=draft["id"], expected_revision=draft["revision"]
    )
    store.service.patch_bill_draft(
        owner_id="owner-test",
        source_event_id="stale-revision-edit",
        draft_id=draft["id"],
        expected_revision=draft["revision"],
        operations=[
            {"action": "set", "product_id": product["id"], "quantity": 2, "unit": "packet"}
        ],
    )

    with pytest.raises(DomainError) as error:
        store.service.finalize_bill(
            owner_id="owner-test",
            source_event_id="stale-revision-finalize",
            draft_id=draft["id"],
            expected_revision=draft["revision"],
            preview_hash=preview["preview_hash"],
        )

    assert error.value.code == "STALE_DRAFT"
    assert store.stock_atomic(product["id"]) == 5_000


def test_catalog_change_requires_refresh_and_new_preview(store) -> None:
    product = store.create_stocked_product(label="price-change", stock_units=5)
    draft = _start_and_add(store, product["id"], chat_id="price-change", quantity=1)
    old_preview = store.service.preview_bill(
        draft_id=draft["id"], expected_revision=draft["revision"]
    )
    store.service.receive_stock(
        owner_id="owner-test",
        source_event_id="price-change-receipt",
        product_id=product["id"],
        quantity=1,
        unit="packet",
        unit_cost_rupees="70.00",
        new_mrp_rupees="120.00",
        new_sell_price_rupees="110.00",
    )

    with pytest.raises(DomainError) as error:
        store.service.finalize_bill(
            owner_id="owner-test",
            source_event_id="price-change-old-finalize",
            draft_id=draft["id"],
            expected_revision=draft["revision"],
            preview_hash=old_preview["preview_hash"],
        )

    assert error.value.code == "PRICE_CHANGED"
    refreshed = store.service.refresh_bill_draft(
        owner_id="owner-test",
        source_event_id="price-change-refresh",
        draft_id=draft["id"],
        expected_revision=draft["revision"],
    )
    assert len(refreshed["changes"]) == 1
    assert refreshed["changes"][0]["old_price"] != refreshed["changes"][0]["new_price"]
    new_draft = refreshed["draft"]
    new_preview = store.service.preview_bill(
        draft_id=new_draft["id"], expected_revision=new_draft["revision"]
    )
    assert new_preview["totals"]["gross_paise"] == 11_000


def test_concurrent_sales_cannot_drive_stock_negative(store) -> None:
    product = store.create_stocked_product(label="concurrent", stock_units=6)
    first_draft = _start_and_add(store, product["id"], chat_id="concurrent-a", quantity=4)
    second_draft = _start_and_add(store, product["id"], chat_id="concurrent-b", quantity=4)
    first_preview = store.service.preview_bill(
        draft_id=first_draft["id"], expected_revision=first_draft["revision"]
    )
    second_preview = store.service.preview_bill(
        draft_id=second_draft["id"], expected_revision=second_draft["revision"]
    )
    barrier = Barrier(2)

    def finalize(draft, preview, suffix):
        service = store.new_service()
        barrier.wait(timeout=5)
        try:
            return service.finalize_bill(
                owner_id="owner-test",
                source_event_id=f"concurrent-finalize-{suffix}",
                draft_id=draft["id"],
                expected_revision=draft["revision"],
                preview_hash=preview["preview_hash"],
            )
        except DomainError as error:
            return error

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(
            executor.map(
                lambda args: finalize(*args),
                [
                    (first_draft, first_preview, "a"),
                    (second_draft, second_preview, "b"),
                ],
            )
        )

    successes = [result for result in results if isinstance(result, dict)]
    refusals = [result for result in results if isinstance(result, DomainError)]
    assert len(successes) == 1
    assert len(refusals) == 1
    assert refusals[0].code in {"INSUFFICIENT_STOCK", "PRICE_CHANGED", "STALE_PREVIEW"}
    assert store.stock_atomic(product["id"]) == 2_000
    assert store.scalar("SELECT COUNT(*) FROM bills") == 1
    assert store.scalar("SELECT COUNT(*) FROM stock_movements WHERE movement_type = 'SALE'") == 1
