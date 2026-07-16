from __future__ import annotations

from kirana_agent.seed import load_seed_data, transform_products


def test_recording_guide_bill_matches_the_seeded_catalog(store) -> None:
    _, product_dataset = load_seed_data()
    store.service.seed_products(transform_products(product_dataset))

    maggi = store.product_by_sku("NB-001")
    butter = store.product_by_sku("DR-001")
    atta = store.product_by_sku("PS-001")
    toor_dal = store.product_by_sku("LS-009")

    receipt = store.service.receive_stock(
        owner_id="recording-owner",
        source_event_id="recording-receipt",
        product_id=butter["id"],
        quantity=20,
        unit="packet",
        unit_cost_rupees="48.00",
        new_mrp_rupees="58.00",
        new_sell_price_rupees="58.00",
        supplier_reference="GRN-DEMO-001",
    )
    assert receipt["stock_atomic"] == 23_000

    draft = store.service.start_bill_draft(
        owner_id="recording-owner",
        chat_id="recording-chat",
        source_event_id="recording-draft",
        payment_mode="CASH",
    )
    draft = store.service.patch_bill_draft(
        owner_id="recording-owner",
        source_event_id="recording-lines",
        draft_id=draft["id"],
        expected_revision=draft["revision"],
        operations=[
            {"action": "add", "product_id": atta["id"], "quantity": 1, "unit": "packet"},
            {"action": "add", "product_id": toor_dal["id"], "quantity": "1.5", "unit": "kg"},
            {"action": "add", "product_id": maggi["id"], "quantity": 4, "unit": "packet"},
            {"action": "add", "product_id": butter["id"], "quantity": 1, "unit": "packet"},
        ],
    )
    first_preview = store.service.preview_bill(
        draft_id=draft["id"], expected_revision=draft["revision"]
    )
    assert first_preview["totals"]["gross_paise"] == 67_150
    assert store.stock_atomic(maggi["id"]) == 6_000

    draft = store.service.patch_bill_draft(
        owner_id="recording-owner",
        source_event_id="recording-edit",
        draft_id=draft["id"],
        expected_revision=draft["revision"],
        operations=[
            {"action": "remove", "product_id": butter["id"]},
            {"action": "set", "product_id": maggi["id"], "quantity": 6, "unit": "packet"},
        ],
    )
    final_preview = store.service.preview_bill(
        draft_id=draft["id"], expected_revision=draft["revision"]
    )
    assert final_preview["totals"]["gross_paise"] == 64_150
    assert final_preview["totals"]["gst_paise"] == 2_376

    bill = store.service.finalize_bill(
        owner_id="recording-owner",
        source_event_id="recording-finalize",
        draft_id=draft["id"],
        expected_revision=draft["revision"],
        preview_hash=final_preview["preview_hash"],
    )
    assert bill["invoice_number"].startswith("AKD/")
    assert bill["gross_paise"] == 64_150
    assert store.stock_atomic(maggi["id"]) == 0
    assert store.stock_atomic(atta["id"]) == 24_000
    assert store.stock_atomic(toor_dal["id"]) == 33_500
    assert store.stock_atomic(butter["id"]) == 23_000
