from __future__ import annotations

from pathlib import Path

import pypdf

from kirana_agent.artifacts.invoice import InvoiceGenerator


def _finalized_invoice_bill(store):
    first = store.create_stocked_product(
        gst_rate_bps=500, sell_price="105.00", label="invoice-five"
    )
    second = store.create_stocked_product(
        gst_rate_bps=1200, sell_price="112.00", label="invoice-twelve"
    )
    draft = store.service.start_bill_draft(
        owner_id="owner-test",
        chat_id="invoice-chat",
        source_event_id="invoice-start",
        payment_mode="CASH",
    )
    draft = store.service.patch_bill_draft(
        owner_id="owner-test",
        source_event_id="invoice-lines",
        draft_id=draft["id"],
        expected_revision=draft["revision"],
        operations=[
            {"action": "add", "product_id": first["id"], "quantity": 2, "unit": "packet"},
            {"action": "add", "product_id": second["id"], "quantity": 1, "unit": "packet"},
        ],
    )
    preview = store.service.preview_bill(
        draft_id=draft["id"], expected_revision=draft["revision"]
    )
    return store.service.finalize_bill(
        owner_id="owner-test",
        source_event_id="invoice-finalize",
        draft_id=draft["id"],
        expected_revision=draft["revision"],
        preview_hash=preview["preview_hash"],
    )


def test_invoice_pdf_is_cached_openable_and_reconciles_to_bill(store, tmp_path) -> None:
    bill = _finalized_invoice_bill(store)
    generator = InvoiceGenerator(store.service, tmp_path / "artifacts")

    first = generator.generate(bill["id"])
    second = generator.generate(bill["invoice_number"])

    assert first["cached"] is False
    assert second["cached"] is True
    assert second["id"] == first["id"]
    assert second["file_path"] == first["file_path"]
    path = Path(first["file_path"])
    assert path.is_file()
    assert path.read_bytes().startswith(b"%PDF")

    reader = pypdf.PdfReader(path)
    assert len(reader.pages) >= 1
    text = "\n".join(page.extract_text() or "" for page in reader.pages)
    assert bill["invoice_number"] in text
    assert bill["store"]["display_name"] in text
    assert bill["gross"] in text
    normalized_text = " ".join(text.split())
    assert "CGST" in normalized_text
    assert "SGST" in normalized_text
    assert "GRAND TOTAL" in normalized_text
    for line in bill["lines"]:
        assert " ".join(line["product_name"].split()) in normalized_text
        assert line["hsn_code"] in text
        assert line["gross"] in text

    assert store.scalar(
        "SELECT COUNT(*) FROM artifacts WHERE artifact_type = 'INVOICE_PDF' AND source_id = ?",
        (bill["id"],),
    ) == 1
