from __future__ import annotations

from collections import Counter

import kirana_agent.seed as seed_module
from kirana_agent.db import Database
from kirana_agent.seed import (
    load_seed_data,
    seed_database,
    transform_products,
    transform_tax_rules,
)


def test_seed_metadata_counts_and_categories_match_payload() -> None:
    tax_dataset, product_dataset = load_seed_data()
    rules = transform_tax_rules(tax_dataset)

    assert product_dataset["expected_product_count"] == 120
    assert len(product_dataset["products"]) == product_dataset["expected_product_count"]
    assert len(rules) == len(tax_dataset["rules"]) == 46
    assert Counter(product["category"] for product in product_dataset["products"]) == Counter(
        product_dataset["expected_category_counts"]
    )
    assert {product["gst_rate_bps"] for product in product_dataset["products"]} == {
        0,
        500,
        1200,
        1800,
    }


def test_every_seed_product_can_be_transformed_to_domain_units() -> None:
    _, product_dataset = load_seed_data()

    products = transform_products(product_dataset)

    assert len(products) == product_dataset["expected_product_count"]
    assert all(product["min_sale_atomic"] > 0 for product in products)


def test_seed_count_stock_and_reorder_values_use_thousandth_atoms() -> None:
    _, product_dataset = load_seed_data()
    raw = {product["sku"]: product for product in product_dataset["products"]}
    transformed = {product["sku"]: product for product in transform_products(product_dataset)}

    assert transformed["PS-001"]["stock_atomic"] == raw["PS-001"]["stock_atomic"] * 1000
    assert transformed["PS-001"]["reorder_atomic"] == raw["PS-001"]["reorder_atomic"] * 1000
    assert transformed["PS-001"]["min_sale_atomic"] == 1000
    assert transformed["LS-001"]["stock_atomic"] == raw["LS-001"]["stock_atomic"]
    for product in transformed.values():
        if product["base_uom"] == "piece":
            assert product["stock_atomic"] % 1000 == 0
            assert product["reorder_atomic"] % 1000 == 0


def test_seed_database_is_repeatable_without_duplicate_rows_or_openings(
    tmp_path, monkeypatch, store_service_factory
) -> None:
    _, product_dataset = load_seed_data()
    expected_openings = sum(
        1 for product in product_dataset["products"] if product["stock_atomic"] > 0
    )
    database_path = tmp_path / "seed-repeat.sqlite3"
    monkeypatch.setattr(
        seed_module,
        "StoreService",
        store_service_factory,
    )

    first = seed_database(database_path)
    second = seed_database(database_path)
    database = Database(database_path)

    assert first["products"] == second["products"] == 120
    assert first["tax_rules"] == second["tax_rules"] == 46
    with database.read() as connection:
        assert connection.execute("SELECT COUNT(*) FROM products").fetchone()[0] == 120
        assert connection.execute(
            "SELECT COUNT(*) FROM stock_movements WHERE movement_type = 'OPENING'"
        ).fetchone()[0] == expected_openings
        assert connection.execute("SELECT COUNT(DISTINCT sku) FROM products").fetchone()[0] == 120


def test_golden_real_skus_have_expected_hsn_and_gst() -> None:
    _, product_dataset = load_seed_data()
    products = {product["sku"]: product for product in product_dataset["products"]}
    expected = {
        "PS-001": ("Aashirvaad Shudh Chakki Atta 5kg", "1101", 500),
        "PS-010": ("Tata Salt 1kg", "2501", 0),
        "OC-001": ("Fortune Sunflower Oil 1L", "1512", 500),
        "DR-001": ("Amul Butter 100g", "0405", 1200),
        "NB-001": ("Maggi 2-Minute Noodles Masala 70g", "1902", 1200),
        "BS-001": ("Parle-G Original Biscuits 79g", "1905", 1800),
        "HC-001": ("Surf Excel Easy Wash Detergent Powder 1kg", "3402", 1800),
    }

    for sku, (name, hsn_code, gst_rate_bps) in expected.items():
        product = products[sku]
        assert product["name"] == name
        assert product["hsn_code"] == hsn_code
        assert product["gst_rate_bps"] == gst_rate_bps
        assert product["cost_paise"] <= product["sell_paise"] <= product["mrp_paise"]


def test_seeded_packaged_product_can_be_previewed_as_one_packet(
    tmp_path, monkeypatch, store_service_factory
) -> None:
    """A golden packaged SKU must use the same count representation as billing."""

    database_path = tmp_path / "seeded-sale.sqlite3"
    monkeypatch.setattr(
        seed_module,
        "StoreService",
        store_service_factory,
    )
    seed_database(database_path)
    service = store_service_factory(Database(database_path))
    product = next(product for product in service.search_products("PS-001") if product["sku"] == "PS-001")
    draft = service.start_bill_draft(
        owner_id="owner-test",
        chat_id="seeded-packet-sale",
        source_event_id="seeded-packet-start",
        payment_mode="CASH",
    )
    draft = service.patch_bill_draft(
        owner_id="owner-test",
        source_event_id="seeded-packet-patch",
        draft_id=draft["id"],
        expected_revision=draft["revision"],
        operations=[
            {
                "action": "add",
                "product_id": product["id"],
                "quantity": 1,
                "unit": "packet",
            }
        ],
    )

    preview = service.preview_bill(
        draft_id=draft["id"], expected_revision=draft["revision"]
    )

    assert preview["lines"][0]["quantity_atomic"] > 0
    assert preview["totals"]["gross_paise"] == product["sell_paise"]
