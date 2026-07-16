from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime, time, timedelta
from pathlib import Path
from typing import Any

from kirana_agent.artifacts.deck import SalesDeckGenerator
from kirana_agent.artifacts.invoice import InvoiceGenerator
from kirana_agent.db import Database
from kirana_agent.domain.errors import DomainError
from kirana_agent.domain.service import StoreService
from kirana_agent.seed import seed_database


def _product_id(service: StoreService, sku: str) -> int:
    matches = service.search_products(sku)
    exact = next((item for item in matches if item["sku"] == sku), None)
    if exact is None:
        raise RuntimeError(f"Demo SKU not found: {sku}")
    return int(exact["id"])


def _make_bill(
    service: StoreService,
    *,
    index: int,
    items: list[tuple[str, str, str]],
    payment_mode: str,
    customer_id: str | None = None,
) -> dict[str, Any]:
    chat_id = f"offline-demo-{index}"
    event = f"demo-{index}"
    draft = service.start_bill_draft(
        owner_id="demo-owner",
        chat_id=chat_id,
        source_event_id=f"{event}-start",
        customer_id=customer_id,
        payment_mode=payment_mode,
    )
    operations = [
        {
            "action": "set",
            "product_id": _product_id(service, sku),
            "quantity": quantity,
            "unit": unit,
        }
        for sku, quantity, unit in items
    ]
    draft = service.patch_bill_draft(
        owner_id="demo-owner",
        source_event_id=f"{event}-items",
        draft_id=draft["id"],
        expected_revision=draft["revision"],
        operations=operations,
    )
    if payment_mode in {"UPI", "CARD"}:
        draft = service.set_bill_payment(
            owner_id="demo-owner",
            source_event_id=f"{event}-payment",
            draft_id=draft["id"],
            expected_revision=draft["revision"],
            payment_mode=payment_mode,
            payment_reference=f"DEMO-{payment_mode}-{index:04d}",
        )
    preview = service.preview_bill(
        draft_id=draft["id"], expected_revision=draft["revision"]
    )
    return service.finalize_bill(
        owner_id="demo-owner",
        source_event_id=f"{event}-finalize",
        draft_id=draft["id"],
        expected_revision=draft["revision"],
        preview_hash=preview["preview_hash"],
    )


def run_demo(database_path: Path, output_dir: Path, *, reset: bool) -> dict[str, Any]:
    if reset and database_path.exists():
        database_path.unlink()
        for suffix in ("-shm", "-wal"):
            sidecar = Path(f"{database_path}{suffix}")
            if sidecar.exists():
                sidecar.unlink()
    if database_path.exists():
        raise RuntimeError(
            f"Demo database already exists: {database_path}. Pass --reset to recreate it."
        )
    seed_database(database_path)
    database = Database(database_path)
    service = StoreService(database)

    # Show the tool-layer oversell guard while seed Maggi stock is exactly six.
    guard_draft = service.start_bill_draft(
        owner_id="demo-owner",
        chat_id="offline-demo-oversell",
        source_event_id="demo-oversell-start",
        payment_mode="CASH",
    )
    guard_draft = service.patch_bill_draft(
        owner_id="demo-owner",
        source_event_id="demo-oversell-items",
        draft_id=guard_draft["id"],
        expected_revision=guard_draft["revision"],
        operations=[
            {
                "action": "set",
                "product_id": _product_id(service, "NB-001"),
                "quantity": "10",
                "unit": "packet",
            }
        ],
    )
    try:
        service.preview_bill(
            draft_id=guard_draft["id"], expected_revision=guard_draft["revision"]
        )
        oversell_guard = {"unexpected": "preview succeeded"}
    except DomainError as error:
        oversell_guard = error.as_dict()
    service.cancel_bill_draft(
        owner_id="demo-owner",
        source_event_id="demo-oversell-cancel",
        draft_id=guard_draft["id"],
        expected_revision=guard_draft["revision"],
    )

    service.receive_stock(
        owner_id="demo-owner",
        source_event_id="demo-receive-maggi",
        product_id=_product_id(service, "NB-001"),
        quantity="50",
        unit="packet",
        unit_cost_rupees="12",
        new_mrp_rupees="14",
        new_sell_price_rupees="14",
        supplier_reference="DEMO-GRN-001",
    )
    service.receive_stock(
        owner_id="demo-owner",
        source_event_id="demo-receive-butter",
        product_id=_product_id(service, "DR-001"),
        quantity="20",
        unit="packet",
        unit_cost_rupees="48",
        new_mrp_rupees="58",
        new_sell_price_rupees="58",
        supplier_reference="DEMO-GRN-002",
    )

    ramesh = service.create_customer(
        owner_id="demo-owner",
        source_event_id="demo-customer-ramesh",
        name="Ramesh Demo",
        phone="9000000000",
        state_code="29",
    )
    service.set_preference(
        owner_id="demo-owner",
        source_event_id="demo-pref-upi",
        key="default_payment_mode",
        value="UPI",
    )
    session_before = service.get_agent_session_id("demo-memory-chat")
    session_after = service.rotate_agent_session("demo-memory-chat")["session_id"]

    plans = [
        ([('PS-001', '1', 'packet'), ('PS-010', '2', 'packet'), ('NB-001', '4', 'packet')], "UPI", None),
        ([('LS-016', '2', 'kg'), ('OC-001', '1', 'pouch'), ('BS-001', '5', 'packet')], "CASH", None),
        ([('DR-001', '2', 'packet'), ('NB-001', '6', 'packet'), ('HC-001', '1', 'packet')], "CARD", None),
        ([('LS-006', '5', 'kg'), ('LS-009', '1.5', 'kg'), ('PS-010', '1', 'packet')], "UPI", None),
        ([('PS-006', '2', 'packet'), ('OC-001', '2', 'pouch'), ('BS-001', '8', 'packet')], "KHATA", ramesh["id"]),
        ([('PS-001', '1', 'packet'), ('DR-001', '1', 'packet'), ('NB-001', '3', 'packet')], "CASH", None),
        ([('HC-001', '2', 'packet'), ('BS-001', '10', 'packet')], "UPI", None),
        ([('LS-016', '1.5', 'kg'), ('LS-005', '2', 'kg'), ('NB-001', '5', 'packet')], "CARD", None),
        ([('PS-010', '3', 'packet'), ('OC-001', '1', 'pouch'), ('DR-001', '2', 'packet')], "CASH", None),
        ([('PS-001', '2', 'packet'), ('NB-001', '6', 'packet'), ('BS-001', '6', 'packet')], "UPI", None),
    ]
    bills = [
        _make_bill(
            service,
            index=index,
            items=items,
            payment_mode=mode,
            customer_id=customer,
        )
        for index, (items, mode, customer) in enumerate(plans, start=1)
    ]

    today = datetime.now(service.timezone).date()
    week_start = today - timedelta(days=6)
    with database.transaction() as connection:
        for index, bill in enumerate(bills):
            day = week_start + timedelta(days=index % 7)
            local_time = datetime.combine(day, time(11 + (index % 7), 15), tzinfo=service.timezone)
            finalized_at = local_time.astimezone(UTC).isoformat(timespec="milliseconds")
            connection.execute(
                "UPDATE bills SET finalized_at = ? WHERE id = ?",
                (finalized_at, bill["id"]),
            )

    service.record_khata_payment(
        owner_id="demo-owner",
        source_event_id="demo-khata-payment",
        customer_id=ramesh["id"],
        amount_rupees="300",
        payment_mode="CASH",
    )

    invoice = InvoiceGenerator(service, output_dir).generate(bills[-1]["id"])
    deck = SalesDeckGenerator(service, output_dir).generate(
        from_date=week_start.isoformat(), to_date=today.isoformat()
    )
    summary = service.sales_analysis(
        from_date=week_start.isoformat(), to_date=today.isoformat()
    )
    return {
        "database": str(database_path.resolve()),
        "oversell_guard": oversell_guard,
        "finalized_bills": len(bills),
        "latest_invoice": invoice["file_path"],
        "sales_deck": deck["file_path"],
        "week_gross": summary["totals"]["gross"],
        "week_gst": summary["totals"]["gst"],
        "ramesh_balance": service.get_khata_balance(ramesh["id"])["balance"],
        "memory": {
            "session_before_new": session_before,
            "session_after_new": session_after,
            "preferences": service.get_preferences("demo-owner"),
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Create a deterministic offline demo database and real artifacts."
    )
    parser.add_argument(
        "--database", type=Path, default=Path("output/demo/kirana-demo.sqlite3")
    )
    parser.add_argument("--output", type=Path, default=Path("output"))
    parser.add_argument("--reset", action="store_true")
    args = parser.parse_args()
    args.database.parent.mkdir(parents=True, exist_ok=True)
    result = run_demo(args.database, args.output, reset=args.reset)
    # Keep CLI output portable on Windows terminals that still default to cp1252.
    print(json.dumps(result, indent=2, ensure_ascii=True))


if __name__ == "__main__":
    main()
