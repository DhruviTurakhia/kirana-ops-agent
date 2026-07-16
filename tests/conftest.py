from __future__ import annotations

import sys
import types
from dataclasses import dataclass
from datetime import UTC
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

# The execution image used for repository checks includes pytest but not the
# project's declared Pydantic runtime dependency. `seed.py` imports Settings
# only for its CLI `main`; domain seeding does not use it. Keep the offline
# domain suite runnable without pretending to exercise configuration parsing.
try:
    import pydantic  # noqa: F401
except ModuleNotFoundError:
    config_stub = types.ModuleType("kirana_agent.config")

    class _UnavailableSettings:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            raise RuntimeError("Settings require the project's Pydantic dependencies")

    config_stub.Settings = _UnavailableSettings
    sys.modules["kirana_agent.config"] = config_stub

from kirana_agent.db import Database  # noqa: E402
from kirana_agent.domain.service import StoreService  # noqa: E402
from kirana_agent.seed import load_seed_data, transform_tax_rules  # noqa: E402

RULE_BY_RATE = {
    0: "IN-GST-2025-01-2501-SALT-NIL",
    500: "IN-GST-2025-01-1101-PACKAGED-5",
    1200: "IN-GST-2025-01-0405-BUTTER-GHEE-12",
    1800: "IN-GST-2025-01-1905-BISCUITS-18",
}


def _offline_store_service(database: Database) -> StoreService:
    """Build the service with a fixed UTC tzinfo when Windows tzdata is absent."""

    service = StoreService.__new__(StoreService)
    service.db = database
    service.timezone = UTC
    return service


@dataclass(slots=True)
class StoreHarness:
    database_path: Path
    database: Database
    service: StoreService
    seed_summary: dict[str, Any]

    def new_service(self) -> StoreService:
        return _offline_store_service(Database(self.database_path))

    def scalar(self, sql: str, parameters: tuple[Any, ...] = ()) -> Any:
        with self.database.read() as connection:
            row = connection.execute(sql, parameters).fetchone()
        assert row is not None
        return row[0]

    def product_by_sku(self, sku: str) -> dict[str, Any]:
        products = self.service.search_products(sku)
        exact = [product for product in products if product["sku"] == sku]
        assert len(exact) == 1
        return exact[0]

    def stock_atomic(self, product_id: int) -> int:
        products = self.service.get_stock([product_id])
        assert len(products) == 1
        return int(products[0]["stock_atomic"])

    def create_product(
        self,
        *,
        gst_rate_bps: int = 500,
        sell_price: str = "100.00",
        cost_price: str = "70.00",
        mrp: str = "120.00",
        label: str | None = None,
    ) -> dict[str, Any]:
        token = label or uuid4().hex[:8]
        return self.service.create_product(
            owner_id="owner-test",
            source_event_id=f"create-product-{token}",
            sku=f"TEST-{gst_rate_bps}-{token}".upper(),
            name=f"Test Product {gst_rate_bps} {token}",
            category="test",
            kind="PACKAGED",
            tax_rule_id=RULE_BY_RATE[gst_rate_bps],
            base_uom="piece",
            sale_uom="packet",
            sell_price_rupees=sell_price,
            cost_price_rupees=cost_price,
            mrp_rupees=mrp,
            min_sale_quantity=1,
        )

    def create_stocked_product(
        self,
        *,
        gst_rate_bps: int = 500,
        stock_units: int = 10,
        sell_price: str = "100.00",
        cost_price: str = "70.00",
        mrp: str = "120.00",
        label: str | None = None,
    ) -> dict[str, Any]:
        product = self.create_product(
            gst_rate_bps=gst_rate_bps,
            sell_price=sell_price,
            cost_price=cost_price,
            mrp=mrp,
            label=label,
        )
        self.service.receive_stock(
            owner_id="owner-test",
            source_event_id=f"setup-receive-{product['sku']}",
            product_id=product["id"],
            quantity=stock_units,
            unit="packet",
            unit_cost_rupees=cost_price,
            new_mrp_rupees=mrp,
            new_sell_price_rupees=sell_price,
            supplier_reference="TEST-SETUP",
        )
        return self.service.get_stock([product["id"]])[0]

    def finalize_sale(
        self,
        *,
        product_id: int,
        quantity: int = 1,
        chat_id: str | None = None,
        payment_mode: str = "CASH",
        payment_reference: str | None = None,
    ) -> dict[str, Any]:
        token = uuid4().hex[:10]
        chat = chat_id or f"chat-{token}"
        draft = self.service.start_bill_draft(
            owner_id="owner-test",
            chat_id=chat,
            source_event_id=f"start-{token}",
            payment_mode=payment_mode,
        )
        if payment_mode in {"UPI", "CARD"}:
            draft = self.service.set_bill_payment(
                owner_id="owner-test",
                source_event_id=f"payment-{token}",
                draft_id=draft["id"],
                expected_revision=draft["revision"],
                payment_mode=payment_mode,
                payment_reference=payment_reference or f"REF-{token}",
            )
        draft = self.service.patch_bill_draft(
            owner_id="owner-test",
            source_event_id=f"patch-{token}",
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
        preview = self.service.preview_bill(
            draft_id=draft["id"], expected_revision=draft["revision"]
        )
        return self.service.finalize_bill(
            owner_id="owner-test",
            source_event_id=f"finalize-{token}",
            draft_id=draft["id"],
            expected_revision=draft["revision"],
            preview_hash=preview["preview_hash"],
        )


@pytest.fixture
def store(tmp_path: Path) -> StoreHarness:
    database_path = tmp_path / "kirana.sqlite3"
    database = Database(database_path)
    database.initialize()
    service = _offline_store_service(database)
    service.bootstrap_store()
    tax_dataset, _ = load_seed_data()
    rules = transform_tax_rules(tax_dataset)
    service.upsert_tax_rules(rules)
    return StoreHarness(
        database_path=database_path,
        database=database,
        service=service,
        seed_summary={"products": 0, "tax_rules": len(rules)},
    )


@pytest.fixture
def store_service_factory():
    return _offline_store_service
