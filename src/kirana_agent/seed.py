from __future__ import annotations

import argparse
import json
from importlib.resources import files
from pathlib import Path
from typing import Any

from kirana_agent.config import Settings
from kirana_agent.db import Database
from kirana_agent.domain.service import StoreService
from kirana_agent.domain.units import to_atomic_quantity


def load_seed_data() -> tuple[dict[str, Any], dict[str, Any]]:
    data_root = files("kirana_agent").joinpath("data")
    tax_dataset = json.loads(
        data_root.joinpath("tax_rules.json").read_text(encoding="utf-8")
    )
    product_dataset = json.loads(
        data_root.joinpath("products.json").read_text(encoding="utf-8")
    )
    return tax_dataset, product_dataset


def transform_tax_rules(dataset: dict[str, Any]) -> list[dict[str, Any]]:
    sources = {source["source_id"]: source for source in dataset["sources"]}
    transformed = []
    for rule in dataset["rules"]:
        source_id = rule["source_ids"][0]
        source = sources[source_id]
        transformed.append(
            {
                "id": rule["tax_rule_id"],
                "hsn_code": rule["hsn_codes"][0],
                "description": rule["label"],
                "gst_rate_bps": rule["gst_rate_bps"],
                "packaging_treatment": rule["applicability"],
                "price_tax_inclusive": True,
                "effective_from": rule["effective_from"],
                "effective_to": rule.get("effective_to"),
                "source_url": source["url"],
                "verified_at": rule["verified_at"],
                "version": dataset["dataset_id"],
                "active": True,
            }
        )
    return transformed


def transform_products(dataset: dict[str, Any]) -> list[dict[str, Any]]:
    transformed = []
    for product in dataset["products"]:
        base_uom = product["inventory_uom"]
        sale_uom = product["sale_uom"]
        min_sale_atomic = to_atomic_quantity(
            product["min_sale_increment"], sale_uom, base_uom
        )
        is_count_product = base_uom == "piece"
        stock_atomic = product["stock_atomic"] * 1000 if is_count_product else product["stock_atomic"]
        reorder_atomic = (
            product["reorder_atomic"] * 1000
            if is_count_product
            else product["reorder_atomic"]
        )
        transformed.append(
            {
                "sku": product["sku"],
                "name": product["name"],
                "aliases": product["aliases"],
                "brand": product.get("brand"),
                "category": product["category"],
                "kind": product["kind"],
                "tax_rule_id": product["tax_rule_id"],
                "base_uom": base_uom,
                "sale_uom": sale_uom,
                "pack_size": f"{product['net_quantity']:g}{product['net_uom']}",
                "min_sale_atomic": min_sale_atomic,
                "cost_paise": product["cost_paise"],
                "sell_paise": product["sell_paise"],
                "mrp_paise": product["mrp_paise"],
                "stock_atomic": stock_atomic,
                "reorder_atomic": reorder_atomic,
                "data_status": product["data_status"],
                "seed_version": dataset["dataset_id"],
                "active": True,
            }
        )
    return transformed


def seed_database(database_path: str | Path) -> dict[str, Any]:
    database = Database(database_path)
    database.initialize()
    service = StoreService(database)
    store = service.bootstrap_store()
    tax_dataset, product_dataset = load_seed_data()
    rules = transform_tax_rules(tax_dataset)
    products = transform_products(product_dataset)
    service.upsert_tax_rules(rules)
    service.seed_products(products)
    with database.read() as connection:
        product_count = connection.execute("SELECT COUNT(*) FROM products").fetchone()[0]
        rule_count = connection.execute("SELECT COUNT(*) FROM tax_rules").fetchone()[0]
    return {
        "database_path": str(Path(database_path).resolve()),
        "store": store["display_name"],
        "tax_rules": rule_count,
        "products": product_count,
        "catalog_dataset": product_dataset["dataset_id"],
        "tax_dataset": tax_dataset["dataset_id"],
    }


def main() -> None:
    settings = Settings()
    parser = argparse.ArgumentParser(description="Initialize and seed the kirana database.")
    parser.add_argument("--database", type=Path, default=settings.database_path)
    args = parser.parse_args()
    result = seed_database(args.database)
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
