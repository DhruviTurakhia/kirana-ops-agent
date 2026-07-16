from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import unicodedata
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

from kirana_agent.db import Database, utc_now
from kirana_agent.domain.errors import DomainError, require
from kirana_agent.domain.money import (
    format_inr,
    inclusive_tax_breakdown,
    rupees_to_paise,
)
from kirana_agent.domain.units import line_gross_paise, to_atomic_quantity

_WHITESPACE = re.compile(r"\s+")
_NON_WORD = re.compile(r"[^\w\s]+", re.UNICODE)
_PAYMENT_MODES = {"CASH", "UPI", "CARD", "KHATA"}
_PREFERENCE_KEYS = {
    "default_payment_mode",
    "preferred_product.atta",
    "preferred_product.rice",
    "preferred_product.dal",
    "preferred_product.oil",
    "preferred_product.sugar",
}


def normalize_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold().strip()
    normalized = _NON_WORD.sub(" ", normalized)
    return _WHITESPACE.sub(" ", normalized)


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _hash_json(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _public_product(row: sqlite3.Row | Mapping[str, Any]) -> dict[str, Any]:
    product = dict(row)
    aliases = product.pop("aliases_json", "[]")
    product["aliases"] = json.loads(aliases) if isinstance(aliases, str) else aliases
    product["sell_price"] = format_inr(product["sell_paise"])
    product["cost_price"] = (
        format_inr(product["cost_paise"]) if product.get("cost_paise") is not None else None
    )
    product["mrp"] = format_inr(product["mrp_paise"]) if product.get("mrp_paise") else None
    product["gst_rate_percent"] = str(
        Decimal(product["gst_rate_bps"]) / Decimal(100)
    )
    return product


class StoreService:
    """Transactional application service used by both agent tools and tests."""

    def __init__(self, database: Database, *, timezone: str = "Asia/Kolkata"):
        self.db = database
        self.timezone = ZoneInfo(timezone)

    # ------------------------------------------------------------------
    # Bootstrap and catalog
    # ------------------------------------------------------------------
    def bootstrap_store(
        self,
        *,
        display_name: str = "Annapurna Kirana Demo",
        legal_name: str = "Annapurna Kirana Demo",
        address: str = "Demo Market, Bengaluru, Karnataka",
        state_code: str = "29",
        gstin: str | None = None,
        invoice_prefix: str = "AKD",
    ) -> dict[str, Any]:
        now = utc_now()
        with self.db.transaction() as connection:
            connection.execute(
                """
                INSERT INTO stores(
                    id, legal_name, display_name, address, state_code, gstin,
                    timezone, invoice_prefix, next_invoice_number, created_at, updated_at
                ) VALUES (1, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
                ON CONFLICT(id) DO NOTHING
                """,
                (
                    legal_name.strip(),
                    display_name.strip(),
                    address.strip(),
                    state_code.strip(),
                    gstin.strip() if gstin else None,
                    str(self.timezone),
                    invoice_prefix.strip().upper(),
                    now,
                    now,
                ),
            )
            row = connection.execute("SELECT * FROM stores WHERE id = 1").fetchone()
        assert row is not None
        return dict(row)

    def get_store_profile(self) -> dict[str, Any]:
        with self.db.read() as connection:
            row = connection.execute("SELECT * FROM stores WHERE id = 1").fetchone()
        require(row is not None, "STORE_NOT_CONFIGURED", "The store profile has not been configured.")
        return dict(row)

    def update_store_profile(
        self,
        *,
        owner_id: str,
        source_event_id: str,
        display_name: str | None = None,
        legal_name: str | None = None,
        address: str | None = None,
        gstin: str | None = None,
        state_code: str | None = None,
    ) -> dict[str, Any]:
        updates: dict[str, Any] = {}
        for key, value in {
            "display_name": display_name,
            "legal_name": legal_name,
            "address": address,
            "state_code": state_code,
        }.items():
            if value is not None:
                require(bool(value.strip()), "INVALID_STORE_PROFILE", f"{key} cannot be blank")
                updates[key] = value.strip()
        if gstin is not None:
            cleaned = gstin.strip().upper()
            if cleaned:
                require(
                    len(cleaned) == 15 and cleaned[:2].isdigit(),
                    "INVALID_GSTIN",
                    "GSTIN must be a 15-character identifier beginning with the state code.",
                )
            updates["gstin"] = cleaned or None
        require(updates, "NO_CHANGES", "No store profile changes were supplied.")

        now = utc_now()
        with self.db.transaction() as connection:
            current = connection.execute("SELECT * FROM stores WHERE id = 1").fetchone()
            require(
                current is not None,
                "STORE_NOT_CONFIGURED",
                "The store profile has not been configured.",
            )
            if updates.get("gstin"):
                expected_state = updates.get("state_code", current["state_code"])
                require(
                    updates["gstin"][:2] == expected_state,
                    "GSTIN_STATE_MISMATCH",
                    "The GSTIN state prefix does not match the configured store state.",
                )
            assignments = ", ".join(f"{key} = ?" for key in updates)
            connection.execute(
                f"UPDATE stores SET {assignments}, updated_at = ? WHERE id = 1",
                (*updates.values(), now),
            )
            self._audit(
                connection,
                owner_id=owner_id,
                source_event_id=source_event_id,
                action="UPDATE_STORE_PROFILE",
                entity_type="store",
                entity_id="1",
                payload=updates,
            )
            result = connection.execute("SELECT * FROM stores WHERE id = 1").fetchone()
        assert result is not None
        return dict(result)

    def upsert_tax_rules(self, rules: Sequence[Mapping[str, Any]]) -> int:
        now = utc_now()
        with self.db.transaction() as connection:
            for rule in rules:
                connection.execute(
                    """
                    INSERT INTO tax_rules(
                        id, hsn_code, description, gst_rate_bps, packaging_treatment,
                        price_tax_inclusive, effective_from, effective_to, source_url,
                        verified_at, version, active
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        hsn_code = excluded.hsn_code,
                        description = excluded.description,
                        gst_rate_bps = excluded.gst_rate_bps,
                        packaging_treatment = excluded.packaging_treatment,
                        price_tax_inclusive = excluded.price_tax_inclusive,
                        effective_from = excluded.effective_from,
                        effective_to = excluded.effective_to,
                        source_url = excluded.source_url,
                        verified_at = excluded.verified_at,
                        version = excluded.version,
                        active = excluded.active
                    """,
                    (
                        str(rule["id"]),
                        str(rule["hsn_code"]),
                        str(rule["description"]),
                        int(rule["gst_rate_bps"]),
                        str(rule["packaging_treatment"]),
                        int(bool(rule.get("price_tax_inclusive", True))),
                        str(rule["effective_from"]),
                        rule.get("effective_to"),
                        str(rule["source_url"]),
                        str(rule.get("verified_at", now[:10])),
                        str(rule.get("version", "1")),
                        int(bool(rule.get("active", True))),
                    ),
                )
        return len(rules)

    def seed_products(self, products: Sequence[Mapping[str, Any]]) -> int:
        now = utc_now()
        with self.db.transaction() as connection:
            for product in products:
                aliases = list(product.get("aliases", []))
                result = connection.execute(
                    """
                    INSERT INTO products(
                        sku, name, normalized_name, aliases_json, brand, category, kind,
                        tax_rule_id, base_uom, sale_uom, pack_size, min_sale_atomic,
                        cost_paise, sell_paise, mrp_paise, stock_atomic, reorder_atomic,
                        barcode, data_status, seed_version, active, version, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
                    ON CONFLICT(sku) DO NOTHING
                    """,
                    (
                        str(product["sku"]),
                        str(product["name"]),
                        normalize_text(str(product["name"])),
                        _canonical_json(aliases),
                        product.get("brand"),
                        str(product["category"]),
                        str(product["kind"]).upper(),
                        str(product["tax_rule_id"]),
                        str(product["base_uom"]).lower(),
                        str(product["sale_uom"]).lower(),
                        product.get("pack_size"),
                        int(product.get("min_sale_atomic", 1000)),
                        int(product["cost_paise"]) if product.get("cost_paise") is not None else None,
                        int(product["sell_paise"]),
                        int(product["mrp_paise"]) if product.get("mrp_paise") is not None else None,
                        int(product.get("stock_atomic", 0)),
                        int(product.get("reorder_atomic", 0)),
                        product.get("barcode"),
                        str(product.get("data_status", "DEMO")),
                        product.get("seed_version"),
                        int(bool(product.get("active", True))),
                        now,
                        now,
                    ),
                )
                if result.rowcount:
                    product_id = result.lastrowid
                    opening = int(product.get("stock_atomic", 0))
                    if opening > 0:
                        connection.execute(
                            """
                            INSERT INTO stock_movements(
                                id, product_id, movement_type, quantity_delta_atomic,
                                unit_cost_paise, reference_type, reference_id, source_event_id,
                                idempotency_key, created_at
                            ) VALUES (?, ?, 'OPENING', ?, ?, 'SEED', ?, 'seed', ?, ?)
                            """,
                            (
                                str(uuid4()),
                                product_id,
                                opening,
                                product.get("cost_paise"),
                                product.get("seed_version", "seed"),
                                f"opening:{product['sku']}",
                                now,
                            ),
                        )
        return len(products)

    def list_tax_rules(self, query: str | None = None) -> list[dict[str, Any]]:
        with self.db.read() as connection:
            rows = connection.execute(
                "SELECT * FROM tax_rules WHERE active = 1 ORDER BY hsn_code, gst_rate_bps"
            ).fetchall()
        rules = [dict(row) for row in rows]
        if query:
            terms = set(normalize_text(query).split())
            rules = [
                rule
                for rule in rules
                if terms
                <= set(normalize_text(f"{rule['description']} {rule['hsn_code']}").split())
                or terms.intersection(
                    normalize_text(f"{rule['description']} {rule['hsn_code']}").split()
                )
            ]
        return rules[:20]

    def create_product(
        self,
        *,
        owner_id: str,
        source_event_id: str,
        sku: str,
        name: str,
        category: str,
        kind: str,
        tax_rule_id: str,
        base_uom: str,
        sale_uom: str,
        sell_price_rupees: str | int | Decimal,
        cost_price_rupees: str | int | Decimal | None = None,
        mrp_rupees: str | int | Decimal | None = None,
        aliases: Sequence[str] = (),
        pack_size: str | None = None,
        reorder_quantity: str | int | Decimal = 0,
        min_sale_quantity: str | int | Decimal = 1,
    ) -> dict[str, Any]:
        cleaned_sku = sku.strip().upper()
        cleaned_name = name.strip()
        require(cleaned_sku and cleaned_name, "INVALID_PRODUCT", "SKU and product name are required.")
        kind_value = kind.strip().upper()
        require(kind_value in {"PACKAGED", "LOOSE", "FRESH"}, "INVALID_PRODUCT_KIND", "Product kind must be PACKAGED, LOOSE, or FRESH.")
        sell_paise = rupees_to_paise(sell_price_rupees)
        cost_paise = rupees_to_paise(cost_price_rupees) if cost_price_rupees is not None else None
        mrp_paise = rupees_to_paise(mrp_rupees) if mrp_rupees is not None else None
        if cost_paise is not None:
            require(sell_paise >= cost_paise, "BELOW_COST", "Sell price cannot be below cost price.")
        if mrp_paise is not None:
            require(sell_paise <= mrp_paise, "ABOVE_MRP", "Sell price cannot exceed MRP.")
        reorder_atomic = 0
        if Decimal(str(reorder_quantity)) > 0:
            reorder_atomic = to_atomic_quantity(reorder_quantity, sale_uom, base_uom)
        min_sale_atomic = to_atomic_quantity(min_sale_quantity, sale_uom, base_uom)
        now = utc_now()
        with self.db.transaction() as connection:
            rule = connection.execute(
                "SELECT * FROM tax_rules WHERE id = ? AND active = 1", (tax_rule_id,)
            ).fetchone()
            require(rule is not None, "UNKNOWN_TAX_RULE", "Select an active tax rule from the catalog before creating this product.")
            try:
                cursor = connection.execute(
                    """
                    INSERT INTO products(
                        sku, name, normalized_name, aliases_json, brand, category, kind,
                        tax_rule_id, base_uom, sale_uom, pack_size, min_sale_atomic,
                        cost_paise, sell_paise, mrp_paise, stock_atomic, reorder_atomic,
                        data_status, active, version, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, 'OWNER', 1, 1, ?, ?)
                    """,
                    (
                        cleaned_sku,
                        cleaned_name,
                        normalize_text(cleaned_name),
                        _canonical_json(list(aliases)),
                        category.strip(),
                        kind_value,
                        tax_rule_id,
                        base_uom.strip().lower(),
                        sale_uom.strip().lower(),
                        pack_size,
                        min_sale_atomic,
                        cost_paise,
                        sell_paise,
                        mrp_paise,
                        reorder_atomic,
                        now,
                        now,
                    ),
                )
            except sqlite3.IntegrityError as error:
                raise DomainError("DUPLICATE_SKU", f"A product with SKU {cleaned_sku} already exists.") from error
            product_id = int(cursor.lastrowid)
            self._audit(
                connection,
                owner_id=owner_id,
                source_event_id=source_event_id,
                action="CREATE_PRODUCT",
                entity_type="product",
                entity_id=str(product_id),
                payload={"sku": cleaned_sku, "name": cleaned_name, "tax_rule_id": tax_rule_id},
            )
            row = self._product_row(connection, product_id)
        assert row is not None
        return _public_product(row)

    def search_products(self, query: str, *, limit: int = 8) -> list[dict[str, Any]]:
        normalized_query = normalize_text(query)
        require(normalized_query, "EMPTY_QUERY", "Provide a product name, brand, alias, or SKU.")
        query_terms = set(normalized_query.split())
        with self.db.read() as connection:
            rows = connection.execute(
                """
                SELECT p.*, tr.hsn_code, tr.gst_rate_bps, tr.description AS tax_description,
                       tr.packaging_treatment
                FROM products p JOIN tax_rules tr ON tr.id = p.tax_rule_id
                WHERE p.active = 1
                """
            ).fetchall()

        scored: list[tuple[int, sqlite3.Row]] = []
        for row in rows:
            aliases = " ".join(json.loads(row["aliases_json"]))
            haystack = normalize_text(
                f"{row['sku']} {row['name']} {row['brand'] or ''} {row['category']} {aliases}"
            )
            haystack_terms = set(haystack.split())
            score = 0
            if normalized_query == normalize_text(row["sku"]):
                score += 100
            if normalized_query == row["normalized_name"]:
                score += 80
            if normalized_query in haystack:
                score += 25
            score += 12 * len(query_terms & haystack_terms)
            if query_terms <= haystack_terms:
                score += 20
            if score:
                scored.append((score, row))
        scored.sort(key=lambda item: (-item[0], item[1]["name"]))
        return [_public_product(row) for _, row in scored[: max(1, min(limit, 20))]]

    def get_stock(self, product_ids: Sequence[int]) -> list[dict[str, Any]]:
        require(product_ids, "NO_PRODUCTS", "At least one product ID is required.")
        placeholders = ",".join("?" for _ in product_ids)
        with self.db.read() as connection:
            rows = connection.execute(
                f"""
                SELECT p.*, tr.hsn_code, tr.gst_rate_bps, tr.description AS tax_description,
                       tr.packaging_treatment
                FROM products p JOIN tax_rules tr ON tr.id = p.tax_rule_id
                WHERE p.id IN ({placeholders}) AND p.active = 1
                ORDER BY p.name
                """,
                tuple(product_ids),
            ).fetchall()
        return [_public_product(row) for row in rows]

    def list_low_stock(self, *, limit: int = 50) -> list[dict[str, Any]]:
        with self.db.read() as connection:
            rows = connection.execute(
                """
                SELECT p.*, tr.hsn_code, tr.gst_rate_bps, tr.description AS tax_description,
                       tr.packaging_treatment
                FROM products p JOIN tax_rules tr ON tr.id = p.tax_rule_id
                WHERE p.active = 1 AND p.stock_atomic <= p.reorder_atomic
                ORDER BY CASE WHEN p.reorder_atomic = 0 THEN 999999.0
                              ELSE CAST(p.stock_atomic AS REAL) / p.reorder_atomic END,
                         p.name
                LIMIT ?
                """,
                (max(1, min(limit, 100)),),
            ).fetchall()
        return [_public_product(row) for row in rows]

    def receive_stock(
        self,
        *,
        owner_id: str,
        source_event_id: str,
        product_id: int,
        quantity: str | int | Decimal,
        unit: str,
        unit_cost_rupees: str | int | Decimal,
        new_mrp_rupees: str | int | Decimal | None = None,
        new_sell_price_rupees: str | int | Decimal | None = None,
        supplier_reference: str | None = None,
    ) -> dict[str, Any]:
        idempotency_key = f"receive:{source_event_id}:{product_id}"
        argument_payload = {
            "product_id": product_id,
            "quantity": str(quantity),
            "unit": unit,
            "unit_cost_rupees": str(unit_cost_rupees),
            "new_mrp_rupees": str(new_mrp_rupees) if new_mrp_rupees is not None else None,
            "new_sell_price_rupees": (
                str(new_sell_price_rupees) if new_sell_price_rupees is not None else None
            ),
            "supplier_reference": supplier_reference,
        }
        with self.db.transaction() as connection:
            replay = self._idempotent_replay(connection, idempotency_key, "RECEIVE_STOCK", argument_payload)
            if replay is not None:
                return replay
            product = self._product_row(connection, product_id)
            require(product is not None and product["active"], "PRODUCT_NOT_FOUND", "The product does not exist or is inactive.")
            quantity_atomic = to_atomic_quantity(quantity, unit, product["base_uom"])
            require(
                quantity_atomic % product["min_sale_atomic"] == 0,
                "INVALID_QUANTITY_INCREMENT",
                "The quantity is not a supported increment for this product.",
                min_sale_atomic=product["min_sale_atomic"],
            )
            cost_paise = rupees_to_paise(unit_cost_rupees)
            sell_paise = (
                rupees_to_paise(new_sell_price_rupees)
                if new_sell_price_rupees is not None
                else product["sell_paise"]
            )
            mrp_paise = (
                rupees_to_paise(new_mrp_rupees)
                if new_mrp_rupees is not None
                else product["mrp_paise"]
            )
            require(sell_paise >= cost_paise, "BELOW_COST", "Sell price cannot be below the received unit cost.")
            if mrp_paise is not None:
                require(sell_paise <= mrp_paise, "ABOVE_MRP", "Sell price cannot exceed MRP.")
            new_stock = product["stock_atomic"] + quantity_atomic
            now = utc_now()
            catalog_changed = any(
                (
                    cost_paise != product["cost_paise"],
                    sell_paise != product["sell_paise"],
                    mrp_paise != product["mrp_paise"],
                )
            )
            connection.execute(
                """
                UPDATE products
                SET stock_atomic = ?, cost_paise = ?, sell_paise = ?, mrp_paise = ?,
                    version = version + ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    new_stock,
                    cost_paise,
                    sell_paise,
                    mrp_paise,
                    int(catalog_changed),
                    now,
                    product_id,
                ),
            )
            movement_id = str(uuid4())
            connection.execute(
                """
                INSERT INTO stock_movements(
                    id, product_id, movement_type, quantity_delta_atomic, unit_cost_paise,
                    reference_type, reference_id, source_event_id, idempotency_key, created_at
                ) VALUES (?, ?, 'RECEIPT', ?, ?, 'SUPPLIER', ?, ?, ?, ?)
                """,
                (
                    movement_id,
                    product_id,
                    quantity_atomic,
                    cost_paise,
                    supplier_reference,
                    source_event_id,
                    idempotency_key,
                    now,
                ),
            )
            self._audit(
                connection,
                owner_id=owner_id,
                source_event_id=source_event_id,
                action="RECEIVE_STOCK",
                entity_type="product",
                entity_id=str(product_id),
                payload={
                    "quantity_atomic": quantity_atomic,
                    "unit_cost_paise": cost_paise,
                    "new_stock_atomic": new_stock,
                },
            )
            result = {
                "ok": True,
                "movement_id": movement_id,
                "product_id": product_id,
                "product_name": product["name"],
                "received_atomic": quantity_atomic,
                "stock_atomic": new_stock,
                "cost_price": format_inr(cost_paise),
                "sell_price": format_inr(sell_paise),
                "mrp": format_inr(mrp_paise) if mrp_paise is not None else None,
            }
            self._store_idempotent_result(
                connection, idempotency_key, "RECEIVE_STOCK", argument_payload, result
            )
            return result

    # ------------------------------------------------------------------
    # Multi-turn bill drafts and atomic finalization
    # ------------------------------------------------------------------
    def start_bill_draft(
        self,
        *,
        owner_id: str,
        chat_id: str,
        source_event_id: str,
        customer_id: str | None = None,
        payment_mode: str | None = None,
    ) -> dict[str, Any]:
        with self.db.transaction() as connection:
            existing = connection.execute(
                "SELECT * FROM bill_drafts WHERE chat_id = ? AND status = 'OPEN'", (chat_id,)
            ).fetchone()
            if existing is not None:
                return self._draft_view(connection, existing["id"])
            if customer_id:
                self._require_customer(connection, customer_id)
            mode = payment_mode.upper() if payment_mode else self._default_payment(connection, owner_id)
            if mode:
                require(mode in _PAYMENT_MODES, "INVALID_PAYMENT_MODE", "Payment mode must be Cash, UPI, Card, or Khata.")
            draft_id = str(uuid4())
            now = utc_now()
            connection.execute(
                """
                INSERT INTO bill_drafts(
                    id, owner_id, chat_id, customer_id, status, payment_mode,
                    revision, created_at, updated_at
                ) VALUES (?, ?, ?, ?, 'OPEN', ?, 1, ?, ?)
                """,
                (draft_id, owner_id, chat_id, customer_id, mode, now, now),
            )
            connection.execute(
                """
                INSERT INTO chat_sessions(chat_id, generation, focused_draft_id, updated_at)
                VALUES (?, 1, ?, ?)
                ON CONFLICT(chat_id) DO UPDATE SET
                    focused_draft_id = excluded.focused_draft_id,
                    updated_at = excluded.updated_at
                """,
                (chat_id, draft_id, now),
            )
            self._audit(
                connection,
                owner_id=owner_id,
                source_event_id=source_event_id,
                action="START_BILL_DRAFT",
                entity_type="bill_draft",
                entity_id=draft_id,
                payload={"customer_id": customer_id, "payment_mode": mode},
            )
            return self._draft_view(connection, draft_id)

    def get_open_bill_draft(self, chat_id: str) -> dict[str, Any] | None:
        with self.db.read() as connection:
            row = connection.execute(
                "SELECT id FROM bill_drafts WHERE chat_id = ? AND status = 'OPEN'", (chat_id,)
            ).fetchone()
            return self._draft_view(connection, row["id"]) if row else None

    def patch_bill_draft(
        self,
        *,
        owner_id: str,
        source_event_id: str,
        draft_id: str,
        expected_revision: int,
        operations: Sequence[Mapping[str, Any]],
    ) -> dict[str, Any]:
        require(operations, "NO_BILL_CHANGES", "At least one bill change is required.")
        payload = {
            "draft_id": draft_id,
            "expected_revision": expected_revision,
            "operations": [dict(operation) for operation in operations],
        }
        idempotency_key = f"patch-draft:{source_event_id}:{draft_id}"
        with self.db.transaction() as connection:
            replay = self._idempotent_replay(connection, idempotency_key, "PATCH_BILL_DRAFT", payload)
            if replay is not None:
                return replay
            draft = self._require_open_draft(connection, draft_id, expected_revision)
            for operation in operations:
                action = str(operation.get("action", "")).strip().lower()
                require(action in {"add", "set", "remove"}, "INVALID_BILL_OPERATION", "Bill line action must be add, set, or remove.")
                try:
                    product_id = int(operation["product_id"])
                except (KeyError, TypeError, ValueError) as error:
                    raise DomainError("INVALID_BILL_OPERATION", "Every bill line operation needs a numeric product_id.") from error
                product = self._product_row(connection, product_id)
                require(product is not None and product["active"], "PRODUCT_NOT_FOUND", "The selected product does not exist or is inactive.", product_id=product_id)
                current_line = connection.execute(
                    "SELECT * FROM bill_draft_items WHERE draft_id = ? AND product_id = ?",
                    (draft_id, product_id),
                ).fetchone()
                if action == "remove":
                    connection.execute(
                        "DELETE FROM bill_draft_items WHERE draft_id = ? AND product_id = ?",
                        (draft_id, product_id),
                    )
                    continue
                require("quantity" in operation and "unit" in operation, "INVALID_BILL_OPERATION", "Add/set operations require quantity and unit.")
                quantity_atomic = to_atomic_quantity(
                    operation["quantity"], str(operation["unit"]), product["base_uom"]
                )
                require(
                    quantity_atomic % product["min_sale_atomic"] == 0,
                    "INVALID_QUANTITY_INCREMENT",
                    "The quantity is not a supported increment for this product.",
                    product_id=product_id,
                    min_sale_atomic=product["min_sale_atomic"],
                )
                if action == "add" and current_line is not None:
                    quantity_atomic += current_line["quantity_atomic"]
                now = utc_now()
                connection.execute(
                    """
                    INSERT INTO bill_draft_items(
                        draft_id, product_id, quantity_atomic, product_version,
                        unit_price_paise, tax_rule_id, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(draft_id, product_id) DO UPDATE SET
                        quantity_atomic = excluded.quantity_atomic,
                        product_version = excluded.product_version,
                        unit_price_paise = excluded.unit_price_paise,
                        tax_rule_id = excluded.tax_rule_id,
                        updated_at = excluded.updated_at
                    """,
                    (
                        draft_id,
                        product_id,
                        quantity_atomic,
                        product["version"],
                        product["sell_paise"],
                        product["tax_rule_id"],
                        now,
                        now,
                    ),
                )
            new_revision = draft["revision"] + 1
            now = utc_now()
            connection.execute(
                """
                UPDATE bill_drafts
                SET revision = ?, preview_hash = NULL, previewed_at = NULL, updated_at = ?
                WHERE id = ?
                """,
                (new_revision, now, draft_id),
            )
            self._audit(
                connection,
                owner_id=owner_id,
                source_event_id=source_event_id,
                action="PATCH_BILL_DRAFT",
                entity_type="bill_draft",
                entity_id=draft_id,
                payload=payload,
            )
            result = self._draft_view(connection, draft_id)
            self._store_idempotent_result(
                connection, idempotency_key, "PATCH_BILL_DRAFT", payload, result
            )
            return result

    def set_bill_payment(
        self,
        *,
        owner_id: str,
        source_event_id: str,
        draft_id: str,
        expected_revision: int,
        payment_mode: str,
        payment_reference: str | None = None,
    ) -> dict[str, Any]:
        mode = payment_mode.strip().upper()
        require(mode in _PAYMENT_MODES, "INVALID_PAYMENT_MODE", "Payment mode must be Cash, UPI, Card, or Khata.")
        payload = {
            "draft_id": draft_id,
            "expected_revision": expected_revision,
            "payment_mode": mode,
            "payment_reference": payment_reference,
        }
        key = f"set-payment:{source_event_id}:{draft_id}"
        with self.db.transaction() as connection:
            replay = self._idempotent_replay(connection, key, "SET_BILL_PAYMENT", payload)
            if replay is not None:
                return replay
            draft = self._require_open_draft(connection, draft_id, expected_revision)
            if mode == "KHATA":
                require(draft["customer_id"] is not None, "KHATA_CUSTOMER_REQUIRED", "Choose a customer before setting a bill to Khata.")
            now = utc_now()
            connection.execute(
                """
                UPDATE bill_drafts
                SET payment_mode = ?, payment_reference = ?, revision = revision + 1,
                    preview_hash = NULL, previewed_at = NULL, updated_at = ?
                WHERE id = ?
                """,
                (mode, payment_reference.strip() if payment_reference else None, now, draft_id),
            )
            self._audit(
                connection,
                owner_id=owner_id,
                source_event_id=source_event_id,
                action="SET_BILL_PAYMENT",
                entity_type="bill_draft",
                entity_id=draft_id,
                payload=payload,
            )
            result = self._draft_view(connection, draft_id)
            self._store_idempotent_result(connection, key, "SET_BILL_PAYMENT", payload, result)
            return result

    def set_bill_customer(
        self,
        *,
        owner_id: str,
        source_event_id: str,
        draft_id: str,
        expected_revision: int,
        customer_id: str | None,
    ) -> dict[str, Any]:
        payload = {
            "draft_id": draft_id,
            "expected_revision": expected_revision,
            "customer_id": customer_id,
        }
        key = f"set-customer:{source_event_id}:{draft_id}"
        with self.db.transaction() as connection:
            replay = self._idempotent_replay(connection, key, "SET_BILL_CUSTOMER", payload)
            if replay is not None:
                return replay
            draft = self._require_open_draft(connection, draft_id, expected_revision)
            if customer_id:
                self._require_customer(connection, customer_id)
            if draft["payment_mode"] == "KHATA":
                require(customer_id is not None, "KHATA_CUSTOMER_REQUIRED", "A Khata bill must have a customer.")
            now = utc_now()
            connection.execute(
                """
                UPDATE bill_drafts
                SET customer_id = ?, revision = revision + 1, preview_hash = NULL,
                    previewed_at = NULL, updated_at = ?
                WHERE id = ?
                """,
                (customer_id, now, draft_id),
            )
            self._audit(
                connection,
                owner_id=owner_id,
                source_event_id=source_event_id,
                action="SET_BILL_CUSTOMER",
                entity_type="bill_draft",
                entity_id=draft_id,
                payload=payload,
            )
            result = self._draft_view(connection, draft_id)
            self._store_idempotent_result(connection, key, "SET_BILL_CUSTOMER", payload, result)
            return result

    def refresh_bill_draft(
        self,
        *,
        owner_id: str,
        source_event_id: str,
        draft_id: str,
        expected_revision: int,
    ) -> dict[str, Any]:
        with self.db.transaction() as connection:
            self._require_open_draft(connection, draft_id, expected_revision)
            lines = connection.execute(
                "SELECT * FROM bill_draft_items WHERE draft_id = ?", (draft_id,)
            ).fetchall()
            changes: list[dict[str, Any]] = []
            now = utc_now()
            for line in lines:
                product = self._product_row(connection, line["product_id"])
                require(product is not None and product["active"], "PRODUCT_NOT_FOUND", "A draft product is no longer available.", product_id=line["product_id"])
                if (
                    line["product_version"] != product["version"]
                    or line["unit_price_paise"] != product["sell_paise"]
                    or line["tax_rule_id"] != product["tax_rule_id"]
                ):
                    changes.append(
                        {
                            "product_id": product["id"],
                            "product_name": product["name"],
                            "old_price": format_inr(line["unit_price_paise"]),
                            "new_price": format_inr(product["sell_paise"]),
                            "old_tax_rule_id": line["tax_rule_id"],
                            "new_tax_rule_id": product["tax_rule_id"],
                        }
                    )
                    connection.execute(
                        """
                        UPDATE bill_draft_items
                        SET product_version = ?, unit_price_paise = ?, tax_rule_id = ?, updated_at = ?
                        WHERE id = ?
                        """,
                        (
                            product["version"],
                            product["sell_paise"],
                            product["tax_rule_id"],
                            now,
                            line["id"],
                        ),
                    )
            if changes:
                connection.execute(
                    """
                    UPDATE bill_drafts
                    SET revision = revision + 1, preview_hash = NULL, previewed_at = NULL, updated_at = ?
                    WHERE id = ?
                    """,
                    (now, draft_id),
                )
                self._audit(
                    connection,
                    owner_id=owner_id,
                    source_event_id=source_event_id,
                    action="REFRESH_BILL_DRAFT",
                    entity_type="bill_draft",
                    entity_id=draft_id,
                    payload={"changes": changes},
                )
            return {"draft": self._draft_view(connection, draft_id), "changes": changes}

    def preview_bill(self, *, draft_id: str, expected_revision: int) -> dict[str, Any]:
        with self.db.transaction() as connection:
            draft = self._require_open_draft(connection, draft_id, expected_revision)
            snapshot = self._build_draft_snapshot(connection, draft, validate_stock=True)
            preview_hash = _hash_json(snapshot)
            now = utc_now()
            connection.execute(
                "UPDATE bill_drafts SET preview_hash = ?, previewed_at = ?, updated_at = ? WHERE id = ?",
                (preview_hash, now, now, draft_id),
            )
            return {
                "ok": True,
                "draft_id": draft_id,
                "revision": draft["revision"],
                "preview_hash": preview_hash,
                "requires_explicit_finalize": True,
                **self._present_snapshot(snapshot),
            }

    def finalize_bill(
        self,
        *,
        owner_id: str,
        source_event_id: str,
        draft_id: str,
        expected_revision: int,
        preview_hash: str,
    ) -> dict[str, Any]:
        payload = {
            "draft_id": draft_id,
            "expected_revision": expected_revision,
            "preview_hash": preview_hash,
        }
        key = f"finalize:{source_event_id}:{draft_id}"
        with self.db.transaction() as connection:
            replay = self._idempotent_replay(connection, key, "FINALIZE_BILL", payload)
            if replay is not None:
                return replay
            draft = connection.execute("SELECT * FROM bill_drafts WHERE id = ?", (draft_id,)).fetchone()
            require(draft is not None, "DRAFT_NOT_FOUND", "The bill draft does not exist.")
            if draft["status"] == "FINALIZED" and draft["finalized_bill_id"]:
                return self._bill_view(connection, draft["finalized_bill_id"])
            require(draft["status"] == "OPEN", "DRAFT_NOT_OPEN", "This bill draft is no longer open.")
            require(draft["revision"] == expected_revision, "STALE_DRAFT", "The bill changed after the preview. Preview the latest revision before finalizing.", expected_revision=expected_revision, actual_revision=draft["revision"])
            require(draft["preview_hash"] is not None, "PREVIEW_REQUIRED", "Preview this bill before finalizing it.")
            require(draft["preview_hash"] == preview_hash, "STALE_PREVIEW", "The confirmation does not match the latest bill preview.")
            snapshot = self._build_draft_snapshot(connection, draft, validate_stock=True)
            current_hash = _hash_json(snapshot)
            require(current_hash == preview_hash, "STALE_PREVIEW", "Price, tax, customer, payment, or stock data changed. Refresh and preview again.")

            store = connection.execute("SELECT * FROM stores WHERE id = 1").fetchone()
            require(store is not None, "STORE_NOT_CONFIGURED", "The store profile has not been configured.")
            if draft["payment_mode"] in {"UPI", "CARD"}:
                require(bool(draft["payment_reference"]), "PAYMENT_REFERENCE_REQUIRED", f"A {draft['payment_mode']} reference is required before finalizing.")
            bill_id = str(uuid4())
            finalized_at = utc_now()
            invoice_number = self._allocate_invoice_number(connection, store, finalized_at)
            totals = snapshot["totals"]
            connection.execute(
                """
                INSERT INTO bills(
                    id, draft_id, invoice_number, owner_id, chat_id, customer_id,
                    supply_type, place_of_supply_state_code, payment_mode, payment_reference,
                    taxable_paise, cgst_paise, sgst_paise, igst_paise, gst_paise,
                    gross_paise, source_event_id, finalized_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    bill_id,
                    draft_id,
                    invoice_number,
                    owner_id,
                    draft["chat_id"],
                    draft["customer_id"],
                    snapshot["supply_type"],
                    snapshot["place_of_supply_state_code"],
                    draft["payment_mode"],
                    draft["payment_reference"],
                    totals["taxable_paise"],
                    totals["cgst_paise"],
                    totals["sgst_paise"],
                    totals["igst_paise"],
                    totals["gst_paise"],
                    totals["gross_paise"],
                    source_event_id,
                    finalized_at,
                ),
            )
            for line in snapshot["lines"]:
                product = self._product_row(connection, line["product_id"])
                assert product is not None
                updated = connection.execute(
                    """
                    UPDATE products
                    SET stock_atomic = stock_atomic - ?, updated_at = ?
                    WHERE id = ? AND stock_atomic >= ?
                    """,
                    (
                        line["quantity_atomic"],
                        finalized_at,
                        line["product_id"],
                        line["quantity_atomic"],
                    ),
                )
                require(updated.rowcount == 1, "INSUFFICIENT_STOCK", f"Not enough {line['product_name']} is available to finalize this bill.", product_id=line["product_id"])
                bill_item_id = str(uuid4())
                connection.execute(
                    """
                    INSERT INTO bill_items(
                        id, bill_id, product_id, sku, product_name, hsn_code, tax_rule_id,
                        gst_rate_bps, base_uom, sale_uom, quantity_atomic, unit_price_paise,
                        unit_cost_paise, taxable_paise, cgst_paise, sgst_paise, igst_paise,
                        gst_paise, gross_paise
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        bill_item_id,
                        bill_id,
                        line["product_id"],
                        line["sku"],
                        line["product_name"],
                        line["hsn_code"],
                        line["tax_rule_id"],
                        line["gst_rate_bps"],
                        line["base_uom"],
                        line["sale_uom"],
                        line["quantity_atomic"],
                        line["unit_price_paise"],
                        line["unit_cost_paise"],
                        line["taxable_paise"],
                        line["cgst_paise"],
                        line["sgst_paise"],
                        line["igst_paise"],
                        line["gst_paise"],
                        line["gross_paise"],
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO stock_movements(
                        id, product_id, movement_type, quantity_delta_atomic, unit_cost_paise,
                        reference_type, reference_id, source_event_id, idempotency_key, created_at
                    ) VALUES (?, ?, 'SALE', ?, ?, 'BILL', ?, ?, ?, ?)
                    """,
                    (
                        str(uuid4()),
                        line["product_id"],
                        -line["quantity_atomic"],
                        line["unit_cost_paise"],
                        bill_id,
                        source_event_id,
                        f"sale:{bill_id}:{line['product_id']}",
                        finalized_at,
                    ),
                )
            if draft["payment_mode"] == "KHATA":
                assert draft["customer_id"] is not None
                self._insert_khata_entry(
                    connection,
                    customer_id=draft["customer_id"],
                    entry_type="CREDIT_SALE",
                    amount_delta_paise=totals["gross_paise"],
                    note=f"Credit sale {invoice_number}",
                    payment_mode=None,
                    payment_reference=None,
                    reference_type="BILL",
                    reference_id=bill_id,
                    source_event_id=source_event_id,
                    idempotency_key=f"khata:bill:{bill_id}",
                )
            connection.execute(
                """
                UPDATE bill_drafts
                SET status = 'FINALIZED', finalized_bill_id = ?, updated_at = ?
                WHERE id = ?
                """,
                (bill_id, finalized_at, draft_id),
            )
            connection.execute(
                "UPDATE chat_sessions SET focused_draft_id = NULL, updated_at = ? WHERE chat_id = ?",
                (finalized_at, draft["chat_id"]),
            )
            self._audit(
                connection,
                owner_id=owner_id,
                source_event_id=source_event_id,
                action="FINALIZE_BILL",
                entity_type="bill",
                entity_id=bill_id,
                payload={"invoice_number": invoice_number, "gross_paise": totals["gross_paise"]},
            )
            result = self._bill_view(connection, bill_id)
            self._store_idempotent_result(connection, key, "FINALIZE_BILL", payload, result)
            return result

    def cancel_bill_draft(
        self,
        *,
        owner_id: str,
        source_event_id: str,
        draft_id: str,
        expected_revision: int,
    ) -> dict[str, Any]:
        with self.db.transaction() as connection:
            draft = self._require_open_draft(connection, draft_id, expected_revision)
            now = utc_now()
            connection.execute(
                "UPDATE bill_drafts SET status = 'CANCELLED', updated_at = ? WHERE id = ?",
                (now, draft_id),
            )
            connection.execute(
                "UPDATE chat_sessions SET focused_draft_id = NULL, updated_at = ? WHERE chat_id = ?",
                (now, draft["chat_id"]),
            )
            self._audit(
                connection,
                owner_id=owner_id,
                source_event_id=source_event_id,
                action="CANCEL_BILL_DRAFT",
                entity_type="bill_draft",
                entity_id=draft_id,
                payload={},
            )
            return {"ok": True, "draft_id": draft_id, "status": "CANCELLED"}

    def get_bill(self, reference: str) -> dict[str, Any]:
        with self.db.read() as connection:
            row = connection.execute(
                "SELECT id FROM bills WHERE id = ? OR invoice_number = ?",
                (reference, reference.upper()),
            ).fetchone()
            require(row is not None, "BILL_NOT_FOUND", "No finalized bill matches that reference.")
            return self._bill_view(connection, row["id"])

    def list_recent_bills(self, *, limit: int = 10) -> list[dict[str, Any]]:
        with self.db.read() as connection:
            rows = connection.execute(
                "SELECT id FROM bills ORDER BY finalized_at DESC LIMIT ?",
                (max(1, min(limit, 50)),),
            ).fetchall()
            return [self._bill_view(connection, row["id"]) for row in rows]

    # ------------------------------------------------------------------
    # Customers and Khata ledger
    # ------------------------------------------------------------------
    def search_customers(self, query: str, *, limit: int = 8) -> list[dict[str, Any]]:
        normalized = normalize_text(query)
        require(normalized, "EMPTY_QUERY", "Provide a customer name or phone number.")
        with self.db.read() as connection:
            rows = connection.execute(
                "SELECT * FROM customers WHERE active = 1 ORDER BY name"
            ).fetchall()
            results = []
            for row in rows:
                haystack = normalize_text(f"{row['name']} {row['phone'] or ''}")
                if normalized in haystack or set(normalized.split()) <= set(haystack.split()):
                    customer = dict(row)
                    customer["balance_paise"] = self._khata_balance(connection, row["id"])
                    customer["balance"] = format_inr(customer["balance_paise"])
                    results.append(customer)
            return results[: max(1, min(limit, 20))]

    def create_customer(
        self,
        *,
        owner_id: str,
        source_event_id: str,
        name: str,
        phone: str | None = None,
        state_code: str | None = None,
    ) -> dict[str, Any]:
        cleaned_name = name.strip()
        require(cleaned_name, "INVALID_CUSTOMER", "Customer name cannot be blank.")
        normalized = normalize_text(cleaned_name)
        cleaned_phone = phone.strip() if phone else None
        with self.db.transaction() as connection:
            if cleaned_phone:
                existing = connection.execute(
                    "SELECT * FROM customers WHERE phone = ? AND active = 1", (cleaned_phone,)
                ).fetchone()
            else:
                existing = connection.execute(
                    "SELECT * FROM customers WHERE normalized_name = ? AND phone IS NULL AND active = 1",
                    (normalized,),
                ).fetchone()
            if existing is not None:
                result = dict(existing)
                result["balance_paise"] = self._khata_balance(connection, existing["id"])
                result["balance"] = format_inr(result["balance_paise"])
                result["already_existed"] = True
                return result
            customer_id = str(uuid4())
            now = utc_now()
            connection.execute(
                """
                INSERT INTO customers(
                    id, name, normalized_name, phone, state_code, active, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, 1, ?, ?)
                """,
                (customer_id, cleaned_name, normalized, cleaned_phone, state_code, now, now),
            )
            self._audit(
                connection,
                owner_id=owner_id,
                source_event_id=source_event_id,
                action="CREATE_CUSTOMER",
                entity_type="customer",
                entity_id=customer_id,
                payload={"name": cleaned_name, "phone": cleaned_phone, "state_code": state_code},
            )
            return {
                "id": customer_id,
                "name": cleaned_name,
                "phone": cleaned_phone,
                "state_code": state_code,
                "balance_paise": 0,
                "balance": format_inr(0),
                "already_existed": False,
            }

    def get_khata_balance(self, customer_id: str) -> dict[str, Any]:
        with self.db.read() as connection:
            customer = self._require_customer(connection, customer_id)
            balance = self._khata_balance(connection, customer_id)
            entries = connection.execute(
                "SELECT COUNT(*) AS count FROM khata_entries WHERE customer_id = ?",
                (customer_id,),
            ).fetchone()["count"]
            return {
                "customer_id": customer_id,
                "customer_name": customer["name"],
                "balance_paise": balance,
                "balance": format_inr(balance),
                "entry_count": entries,
            }

    def get_khata_statement(self, customer_id: str, *, limit: int = 20) -> dict[str, Any]:
        with self.db.read() as connection:
            customer = self._require_customer(connection, customer_id)
            rows = connection.execute(
                """
                SELECT * FROM khata_entries
                WHERE customer_id = ?
                ORDER BY created_at DESC, id DESC
                LIMIT ?
                """,
                (customer_id, max(1, min(limit, 100))),
            ).fetchall()
            entries = []
            for row in rows:
                entry = dict(row)
                entry["amount"] = format_inr(abs(entry["amount_delta_paise"]))
                entry["direction"] = "OWED_TO_STORE" if entry["amount_delta_paise"] > 0 else "PAID"
                entries.append(entry)
            balance = self._khata_balance(connection, customer_id)
            return {
                "customer_id": customer_id,
                "customer_name": customer["name"],
                "balance_paise": balance,
                "balance": format_inr(balance),
                "entries": entries,
            }

    def record_khata_charge(
        self,
        *,
        owner_id: str,
        source_event_id: str,
        customer_id: str,
        amount_rupees: str | int | Decimal,
        note: str,
    ) -> dict[str, Any]:
        amount_paise = rupees_to_paise(amount_rupees)
        require(amount_paise > 0, "INVALID_AMOUNT", "Khata charge must be greater than zero.")
        require(note.strip(), "KHATA_NOTE_REQUIRED", "A reason is required for a direct Khata charge.")
        payload = {"customer_id": customer_id, "amount_paise": amount_paise, "note": note.strip()}
        key = f"khata-charge:{source_event_id}:{customer_id}"
        with self.db.transaction() as connection:
            replay = self._idempotent_replay(connection, key, "KHATA_CHARGE", payload)
            if replay is not None:
                return replay
            customer = self._require_customer(connection, customer_id)
            entry_id = self._insert_khata_entry(
                connection,
                customer_id=customer_id,
                entry_type="CHARGE",
                amount_delta_paise=amount_paise,
                note=note.strip(),
                payment_mode=None,
                payment_reference=None,
                reference_type=None,
                reference_id=None,
                source_event_id=source_event_id,
                idempotency_key=key,
            )
            balance = self._khata_balance(connection, customer_id)
            self._audit(
                connection,
                owner_id=owner_id,
                source_event_id=source_event_id,
                action="KHATA_CHARGE",
                entity_type="khata_entry",
                entity_id=entry_id,
                payload=payload,
            )
            result = {
                "ok": True,
                "entry_id": entry_id,
                "customer_id": customer_id,
                "customer_name": customer["name"],
                "charged": format_inr(amount_paise),
                "balance_paise": balance,
                "balance": format_inr(balance),
            }
            self._store_idempotent_result(connection, key, "KHATA_CHARGE", payload, result)
            return result

    def record_khata_payment(
        self,
        *,
        owner_id: str,
        source_event_id: str,
        customer_id: str,
        amount_rupees: str | int | Decimal,
        payment_mode: str,
        payment_reference: str | None = None,
    ) -> dict[str, Any]:
        amount_paise = rupees_to_paise(amount_rupees)
        require(amount_paise > 0, "INVALID_AMOUNT", "Khata payment must be greater than zero.")
        mode = payment_mode.strip().upper()
        require(mode in {"CASH", "UPI", "CARD"}, "INVALID_PAYMENT_MODE", "Khata payment mode must be Cash, UPI, or Card.")
        if mode in {"UPI", "CARD"}:
            require(bool(payment_reference and payment_reference.strip()), "PAYMENT_REFERENCE_REQUIRED", f"A {mode} reference is required.")
        payload = {
            "customer_id": customer_id,
            "amount_paise": amount_paise,
            "payment_mode": mode,
            "payment_reference": payment_reference,
        }
        key = f"khata-payment:{source_event_id}:{customer_id}"
        with self.db.transaction() as connection:
            replay = self._idempotent_replay(connection, key, "KHATA_PAYMENT", payload)
            if replay is not None:
                return replay
            customer = self._require_customer(connection, customer_id)
            entries_exist = connection.execute(
                "SELECT 1 FROM khata_entries WHERE customer_id = ? LIMIT 1", (customer_id,)
            ).fetchone()
            require(entries_exist is not None, "KHATA_NOT_FOUND", f"{customer['name']} does not have a Khata ledger yet.")
            balance_before = self._khata_balance(connection, customer_id)
            require(balance_before > 0, "NO_OUTSTANDING_BALANCE", f"{customer['name']} has no outstanding Khata balance.")
            require(amount_paise <= balance_before, "KHATA_OVERPAYMENT", "Payment cannot exceed the outstanding Khata balance.", outstanding_paise=balance_before, attempted_paise=amount_paise)
            entry_id = self._insert_khata_entry(
                connection,
                customer_id=customer_id,
                entry_type="PAYMENT",
                amount_delta_paise=-amount_paise,
                note="Khata payment",
                payment_mode=mode,
                payment_reference=payment_reference.strip() if payment_reference else None,
                reference_type=None,
                reference_id=None,
                source_event_id=source_event_id,
                idempotency_key=key,
            )
            balance = self._khata_balance(connection, customer_id)
            self._audit(
                connection,
                owner_id=owner_id,
                source_event_id=source_event_id,
                action="KHATA_PAYMENT",
                entity_type="khata_entry",
                entity_id=entry_id,
                payload=payload,
            )
            result = {
                "ok": True,
                "entry_id": entry_id,
                "customer_id": customer_id,
                "customer_name": customer["name"],
                "paid": format_inr(amount_paise),
                "payment_mode": mode,
                "payment_reference": payment_reference,
                "balance_paise": balance,
                "balance": format_inr(balance),
            }
            self._store_idempotent_result(connection, key, "KHATA_PAYMENT", payload, result)
            return result

    # ------------------------------------------------------------------
    # Durable preferences and chat generations
    # ------------------------------------------------------------------
    def get_preferences(self, owner_id: str) -> dict[str, Any]:
        with self.db.read() as connection:
            rows = connection.execute(
                "SELECT preference_key, value_json FROM owner_preferences WHERE owner_id = ? ORDER BY preference_key",
                (owner_id,),
            ).fetchall()
        return {row["preference_key"]: json.loads(row["value_json"]) for row in rows}

    def set_preference(
        self,
        *,
        owner_id: str,
        source_event_id: str,
        key: str,
        value: Any,
    ) -> dict[str, Any]:
        preference_key = key.strip().lower()
        require(preference_key in _PREFERENCE_KEYS, "UNSUPPORTED_PREFERENCE", "That preference is not supported.", supported=sorted(_PREFERENCE_KEYS))
        if preference_key == "default_payment_mode":
            value = str(value).upper()
            require(value in {"CASH", "UPI", "CARD"}, "INVALID_PAYMENT_MODE", "Default payment must be Cash, UPI, or Card.")
        elif preference_key.startswith("preferred_product."):
            try:
                value = int(value)
            except (TypeError, ValueError) as error:
                raise DomainError("INVALID_PRODUCT_PREFERENCE", "Preferred product value must be a product ID returned by search.") from error
        now = utc_now()
        with self.db.transaction() as connection:
            if preference_key.startswith("preferred_product."):
                product = self._product_row(connection, value)
                require(product is not None and product["active"], "PRODUCT_NOT_FOUND", "The preferred product does not exist or is inactive.")
            connection.execute(
                """
                INSERT INTO owner_preferences(
                    owner_id, preference_key, value_json, source_event_id, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(owner_id, preference_key) DO UPDATE SET
                    value_json = excluded.value_json,
                    source_event_id = excluded.source_event_id,
                    updated_at = excluded.updated_at
                """,
                (owner_id, preference_key, _canonical_json(value), source_event_id, now, now),
            )
            self._audit(
                connection,
                owner_id=owner_id,
                source_event_id=source_event_id,
                action="SET_PREFERENCE",
                entity_type="owner_preference",
                entity_id=f"{owner_id}:{preference_key}",
                payload={"value": value},
            )
        return {"ok": True, "key": preference_key, "value": value, "persists_across_new_chat": True}

    def clear_preference(
        self, *, owner_id: str, source_event_id: str, key: str
    ) -> dict[str, Any]:
        preference_key = key.strip().lower()
        require(preference_key in _PREFERENCE_KEYS, "UNSUPPORTED_PREFERENCE", "That preference is not supported.")
        with self.db.transaction() as connection:
            deleted = connection.execute(
                "DELETE FROM owner_preferences WHERE owner_id = ? AND preference_key = ?",
                (owner_id, preference_key),
            ).rowcount
            self._audit(
                connection,
                owner_id=owner_id,
                source_event_id=source_event_id,
                action="CLEAR_PREFERENCE",
                entity_type="owner_preference",
                entity_id=f"{owner_id}:{preference_key}",
                payload={"existed": bool(deleted)},
            )
        return {"ok": True, "key": preference_key, "cleared": bool(deleted)}

    def get_agent_session_id(self, chat_id: str) -> str:
        now = utc_now()
        with self.db.transaction() as connection:
            connection.execute(
                """
                INSERT INTO chat_sessions(chat_id, generation, updated_at)
                VALUES (?, 1, ?)
                ON CONFLICT(chat_id) DO NOTHING
                """,
                (chat_id, now),
            )
            generation = connection.execute(
                "SELECT generation FROM chat_sessions WHERE chat_id = ?", (chat_id,)
            ).fetchone()["generation"]
        return f"telegram:{chat_id}:g{generation}"

    def rotate_agent_session(self, chat_id: str) -> dict[str, Any]:
        now = utc_now()
        with self.db.transaction() as connection:
            connection.execute(
                """
                INSERT INTO chat_sessions(chat_id, generation, focused_draft_id, updated_at)
                VALUES (?, 2, NULL, ?)
                ON CONFLICT(chat_id) DO UPDATE SET
                    generation = generation + 1,
                    focused_draft_id = NULL,
                    updated_at = excluded.updated_at
                """,
                (chat_id, now),
            )
            generation = connection.execute(
                "SELECT generation FROM chat_sessions WHERE chat_id = ?", (chat_id,)
            ).fetchone()["generation"]
        return {
            "ok": True,
            "session_id": f"telegram:{chat_id}:g{generation}",
            "conversation_cleared": True,
            "preferences_preserved": True,
            "drafts_preserved": True,
        }

    # ------------------------------------------------------------------
    # Sales summaries and immutable close snapshots
    # ------------------------------------------------------------------
    def daily_summary(self, business_date: date | str | None = None) -> dict[str, Any]:
        target = self._coerce_date(business_date)
        start_utc, end_utc = self._utc_bounds(target, target)
        return self._analytics_between(start_utc, end_utc, target.isoformat(), target.isoformat())

    def sales_analysis(
        self, *, from_date: date | str, to_date: date | str
    ) -> dict[str, Any]:
        start = self._coerce_date(from_date)
        end = self._coerce_date(to_date)
        require(end >= start, "INVALID_DATE_RANGE", "to_date cannot be before from_date.")
        require((end - start).days <= 366, "DATE_RANGE_TOO_LARGE", "Analysis range cannot exceed 367 days.")
        start_utc, end_utc = self._utc_bounds(start, end)
        return self._analytics_between(start_utc, end_utc, start.isoformat(), end.isoformat())

    def close_day(
        self,
        *,
        owner_id: str,
        source_event_id: str,
        business_date: date | str | None = None,
    ) -> dict[str, Any]:
        target = self._coerce_date(business_date)
        summary = self.daily_summary(target)
        now = utc_now()
        with self.db.transaction() as connection:
            existing = connection.execute(
                "SELECT * FROM day_closes WHERE business_date = ?", (target.isoformat(),)
            ).fetchone()
            if existing:
                return {
                    "ok": True,
                    "already_closed": True,
                    "closed_at": existing["closed_at"],
                    "summary": json.loads(existing["summary_json"]),
                }
            connection.execute(
                """
                INSERT INTO day_closes(business_date, summary_json, source_event_id, closed_at)
                VALUES (?, ?, ?, ?)
                """,
                (target.isoformat(), _canonical_json(summary), source_event_id, now),
            )
            self._audit(
                connection,
                owner_id=owner_id,
                source_event_id=source_event_id,
                action="CLOSE_DAY",
                entity_type="day_close",
                entity_id=target.isoformat(),
                payload=summary,
            )
        return {"ok": True, "already_closed": False, "closed_at": now, "summary": summary}

    # ------------------------------------------------------------------
    # Telegram update ledger
    # ------------------------------------------------------------------
    def claim_telegram_update(self, *, update_id: str, chat_id: str, user_id: str) -> dict[str, Any]:
        now = utc_now()
        with self.db.transaction() as connection:
            existing = connection.execute(
                "SELECT * FROM telegram_updates WHERE update_id = ?", (update_id,)
            ).fetchone()
            if existing:
                return {
                    "claimed": False,
                    "status": existing["status"],
                    "response_text": existing["response_text"],
                    "artifacts": json.loads(existing["artifacts_json"]),
                }
            connection.execute(
                """
                INSERT INTO telegram_updates(
                    update_id, chat_id, user_id, status, created_at, updated_at
                ) VALUES (?, ?, ?, 'PROCESSING', ?, ?)
                """,
                (update_id, chat_id, user_id, now, now),
            )
        return {"claimed": True, "status": "PROCESSING"}

    def complete_telegram_update(
        self,
        *,
        update_id: str,
        response_text: str,
        artifacts: Sequence[Mapping[str, Any]] = (),
    ) -> None:
        with self.db.transaction() as connection:
            connection.execute(
                """
                UPDATE telegram_updates
                SET status = 'COMPLETED', response_text = ?, artifacts_json = ?,
                    error_text = NULL, updated_at = ?
                WHERE update_id = ?
                """,
                (response_text, _canonical_json(list(artifacts)), utc_now(), update_id),
            )

    def fail_telegram_update(self, *, update_id: str, error_text: str) -> None:
        with self.db.transaction() as connection:
            connection.execute(
                """
                UPDATE telegram_updates
                SET status = 'FAILED', error_text = ?, updated_at = ?
                WHERE update_id = ?
                """,
                (error_text[:2000], utc_now(), update_id),
            )

    def retry_failed_telegram_update(self, update_id: str) -> bool:
        with self.db.transaction() as connection:
            changed = connection.execute(
                """
                UPDATE telegram_updates
                SET status = 'PROCESSING', error_text = NULL, updated_at = ?
                WHERE update_id = ? AND status = 'FAILED'
                """,
                (utc_now(), update_id),
            ).rowcount
        return bool(changed)

    # ------------------------------------------------------------------
    # Artifact cache metadata
    # ------------------------------------------------------------------
    def find_artifact(
        self,
        *,
        artifact_type: str,
        source_id: str,
        source_hash: str,
        template_version: str,
    ) -> dict[str, Any] | None:
        with self.db.read() as connection:
            row = connection.execute(
                """
                SELECT * FROM artifacts
                WHERE artifact_type = ? AND source_id = ? AND source_hash = ?
                  AND template_version = ?
                """,
                (artifact_type, source_id, source_hash, template_version),
            ).fetchone()
        if row is None or not Path(row["file_path"]).exists():
            return None
        return dict(row)

    def record_artifact(
        self,
        *,
        artifact_type: str,
        source_id: str,
        source_hash: str,
        template_version: str,
        file_path: str | Path,
    ) -> dict[str, Any]:
        artifact_id = str(uuid4())
        now = utc_now()
        with self.db.transaction() as connection:
            connection.execute(
                """
                INSERT INTO artifacts(
                    id, artifact_type, source_id, source_hash, template_version,
                    file_path, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(artifact_type, source_id, source_hash, template_version)
                DO UPDATE SET file_path = excluded.file_path, created_at = excluded.created_at
                """,
                (
                    artifact_id,
                    artifact_type,
                    source_id,
                    source_hash,
                    template_version,
                    str(Path(file_path).resolve()),
                    now,
                ),
            )
            row = connection.execute(
                """
                SELECT * FROM artifacts
                WHERE artifact_type = ? AND source_id = ? AND source_hash = ?
                  AND template_version = ?
                """,
                (artifact_type, source_id, source_hash, template_version),
            ).fetchone()
        assert row is not None
        return dict(row)

    @staticmethod
    def content_hash(value: Any) -> str:
        return _hash_json(value)

    # ------------------------------------------------------------------
    # Internal helpers. These are deliberately not agent tools.
    # ------------------------------------------------------------------
    def _product_row(self, connection: sqlite3.Connection, product_id: int) -> sqlite3.Row | None:
        return connection.execute(
            """
            SELECT p.*, tr.hsn_code, tr.gst_rate_bps, tr.description AS tax_description,
                   tr.packaging_treatment, tr.price_tax_inclusive,
                   tr.effective_from AS tax_effective_from, tr.version AS tax_version
            FROM products p JOIN tax_rules tr ON tr.id = p.tax_rule_id
            WHERE p.id = ?
            """,
            (product_id,),
        ).fetchone()

    def _require_open_draft(
        self, connection: sqlite3.Connection, draft_id: str, expected_revision: int
    ) -> sqlite3.Row:
        draft = connection.execute("SELECT * FROM bill_drafts WHERE id = ?", (draft_id,)).fetchone()
        require(draft is not None, "DRAFT_NOT_FOUND", "The bill draft does not exist.")
        require(draft["status"] == "OPEN", "DRAFT_NOT_OPEN", "This bill draft is no longer open.")
        require(
            draft["revision"] == expected_revision,
            "STALE_DRAFT",
            "The bill changed. Load the latest draft before editing it.",
            expected_revision=expected_revision,
            actual_revision=draft["revision"],
        )
        return draft

    def _draft_view(self, connection: sqlite3.Connection, draft_id: str) -> dict[str, Any]:
        draft = connection.execute("SELECT * FROM bill_drafts WHERE id = ?", (draft_id,)).fetchone()
        require(draft is not None, "DRAFT_NOT_FOUND", "The bill draft does not exist.")
        rows = connection.execute(
            """
            SELECT di.*, p.sku, p.name AS product_name, p.base_uom, p.sale_uom,
                   p.stock_atomic, p.sell_paise AS current_sell_paise,
                   tr.hsn_code, tr.gst_rate_bps
            FROM bill_draft_items di
            JOIN products p ON p.id = di.product_id
            JOIN tax_rules tr ON tr.id = di.tax_rule_id
            WHERE di.draft_id = ?
            ORDER BY di.id
            """,
            (draft_id,),
        ).fetchall()
        lines = []
        for row in rows:
            line = dict(row)
            line["quantity"] = self._quantity_label(
                row["quantity_atomic"], row["base_uom"], row["sale_uom"]
            )
            line["unit_price"] = format_inr(row["unit_price_paise"])
            line["line_total"] = format_inr(
                line_gross_paise(
                    row["unit_price_paise"], row["quantity_atomic"], row["base_uom"]
                )
            )
            line["gst_rate_percent"] = str(Decimal(row["gst_rate_bps"]) / Decimal(100))
            lines.append(line)
        customer = None
        if draft["customer_id"]:
            customer_row = connection.execute(
                "SELECT * FROM customers WHERE id = ?", (draft["customer_id"],)
            ).fetchone()
            customer = dict(customer_row) if customer_row else None
        return {
            "ok": True,
            "id": draft["id"],
            "owner_id": draft["owner_id"],
            "chat_id": draft["chat_id"],
            "status": draft["status"],
            "revision": draft["revision"],
            "payment_mode": draft["payment_mode"],
            "payment_reference": draft["payment_reference"],
            "customer": customer,
            "preview_hash": draft["preview_hash"],
            "lines": lines,
            "line_count": len(lines),
        }

    def _build_draft_snapshot(
        self,
        connection: sqlite3.Connection,
        draft: sqlite3.Row,
        *,
        validate_stock: bool,
    ) -> dict[str, Any]:
        require(draft["payment_mode"] in _PAYMENT_MODES, "PAYMENT_MODE_REQUIRED", "Choose Cash, UPI, Card, or Khata before previewing the bill.")
        if draft["payment_mode"] == "KHATA":
            require(draft["customer_id"] is not None, "KHATA_CUSTOMER_REQUIRED", "Choose a customer for a Khata bill.")
        store = connection.execute("SELECT * FROM stores WHERE id = 1").fetchone()
        require(store is not None, "STORE_NOT_CONFIGURED", "The store profile has not been configured.")
        customer = None
        if draft["customer_id"]:
            customer = self._require_customer(connection, draft["customer_id"])
        place_of_supply = (
            customer["state_code"] if customer is not None and customer["state_code"] else store["state_code"]
        )
        supply_type = (
            "INTRA_STATE" if place_of_supply == store["state_code"] else "INTER_STATE"
        )
        intra_state = supply_type == "INTRA_STATE"
        draft_lines = connection.execute(
            "SELECT * FROM bill_draft_items WHERE draft_id = ? ORDER BY product_id",
            (draft["id"],),
        ).fetchall()
        require(draft_lines, "EMPTY_BILL", "Add at least one product before previewing the bill.")
        lines: list[dict[str, Any]] = []
        insufficient: list[dict[str, Any]] = []
        stale: list[dict[str, Any]] = []
        for draft_line in draft_lines:
            product = self._product_row(connection, draft_line["product_id"])
            require(product is not None and product["active"], "PRODUCT_NOT_FOUND", "A draft product no longer exists or is inactive.", product_id=draft_line["product_id"])
            if (
                draft_line["product_version"] != product["version"]
                or draft_line["unit_price_paise"] != product["sell_paise"]
                or draft_line["tax_rule_id"] != product["tax_rule_id"]
            ):
                stale.append(
                    {
                        "product_id": product["id"],
                        "product_name": product["name"],
                        "draft_price": format_inr(draft_line["unit_price_paise"]),
                        "current_price": format_inr(product["sell_paise"]),
                        "draft_product_version": draft_line["product_version"],
                        "current_product_version": product["version"],
                    }
                )
                continue
            require(product["cost_paise"] is not None, "MISSING_COST_PRICE", f"{product['name']} has no cost price and cannot be sold.", product_id=product["id"])
            require(product["sell_paise"] >= product["cost_paise"], "BELOW_COST", f"{product['name']} is priced below cost and cannot be sold.", product_id=product["id"])
            if product["mrp_paise"] is not None:
                require(product["sell_paise"] <= product["mrp_paise"], "ABOVE_MRP", f"{product['name']} is priced above MRP and cannot be sold.", product_id=product["id"])
            if validate_stock and product["stock_atomic"] < draft_line["quantity_atomic"]:
                insufficient.append(
                    {
                        "product_id": product["id"],
                        "product_name": product["name"],
                        "requested_atomic": draft_line["quantity_atomic"],
                        "available_atomic": product["stock_atomic"],
                    }
                )
                continue
            gross = line_gross_paise(
                draft_line["unit_price_paise"],
                draft_line["quantity_atomic"],
                product["base_uom"],
            )
            tax = inclusive_tax_breakdown(
                gross, product["gst_rate_bps"], intra_state=intra_state
            )
            lines.append(
                {
                    "product_id": product["id"],
                    "sku": product["sku"],
                    "product_name": product["name"],
                    "hsn_code": product["hsn_code"],
                    "tax_rule_id": product["tax_rule_id"],
                    "tax_rule_version": product["tax_version"],
                    "gst_rate_bps": product["gst_rate_bps"],
                    "base_uom": product["base_uom"],
                    "sale_uom": product["sale_uom"],
                    "quantity_atomic": draft_line["quantity_atomic"],
                    "unit_price_paise": draft_line["unit_price_paise"],
                    "unit_cost_paise": product["cost_paise"],
                    "taxable_paise": tax.taxable_paise,
                    "cgst_paise": tax.cgst_paise,
                    "sgst_paise": tax.sgst_paise,
                    "igst_paise": tax.igst_paise,
                    "gst_paise": tax.gst_paise,
                    "gross_paise": tax.gross_paise,
                }
            )
        require(not stale, "PRICE_CHANGED", "One or more product records changed after they were added. Refresh the draft, show the changes, and preview again.", changes=stale)
        require(not insufficient, "INSUFFICIENT_STOCK", "There is not enough stock to prepare this bill.", products=insufficient)
        totals = {
            field: sum(line[field] for line in lines)
            for field in (
                "taxable_paise",
                "cgst_paise",
                "sgst_paise",
                "igst_paise",
                "gst_paise",
                "gross_paise",
            )
        }
        require(totals["taxable_paise"] + totals["gst_paise"] == totals["gross_paise"], "TAX_RECONCILIATION_FAILED", "Bill tax totals did not reconcile.")
        return {
            "draft_id": draft["id"],
            "revision": draft["revision"],
            "owner_id": draft["owner_id"],
            "customer_id": draft["customer_id"],
            "payment_mode": draft["payment_mode"],
            "payment_reference": draft["payment_reference"],
            "supply_type": supply_type,
            "place_of_supply_state_code": place_of_supply,
            "lines": lines,
            "totals": totals,
        }

    def _present_snapshot(self, snapshot: Mapping[str, Any]) -> dict[str, Any]:
        lines = []
        for raw in snapshot["lines"]:
            line = dict(raw)
            line["quantity"] = self._quantity_label(
                line["quantity_atomic"], line["base_uom"], line["sale_uom"]
            )
            line["unit_price"] = format_inr(line["unit_price_paise"])
            line["taxable"] = format_inr(line["taxable_paise"])
            line["cgst"] = format_inr(line["cgst_paise"])
            line["sgst"] = format_inr(line["sgst_paise"])
            line["igst"] = format_inr(line["igst_paise"])
            line["gst"] = format_inr(line["gst_paise"])
            line["gross"] = format_inr(line["gross_paise"])
            line["gst_rate_percent"] = str(Decimal(line["gst_rate_bps"]) / Decimal(100))
            lines.append(line)
        totals = dict(snapshot["totals"])
        for key in list(totals):
            totals[key.removesuffix("_paise")] = format_inr(totals[key])
        return {
            "payment_mode": snapshot["payment_mode"],
            "payment_reference": snapshot["payment_reference"],
            "supply_type": snapshot["supply_type"],
            "place_of_supply_state_code": snapshot["place_of_supply_state_code"],
            "lines": lines,
            "totals": totals,
        }

    def _bill_view(self, connection: sqlite3.Connection, bill_id: str) -> dict[str, Any]:
        bill = connection.execute("SELECT * FROM bills WHERE id = ?", (bill_id,)).fetchone()
        require(bill is not None, "BILL_NOT_FOUND", "The finalized bill does not exist.")
        store = connection.execute("SELECT * FROM stores WHERE id = 1").fetchone()
        customer = None
        if bill["customer_id"]:
            customer_row = connection.execute(
                "SELECT * FROM customers WHERE id = ?", (bill["customer_id"],)
            ).fetchone()
            customer = dict(customer_row) if customer_row else None
        items = connection.execute(
            "SELECT * FROM bill_items WHERE bill_id = ? ORDER BY id", (bill_id,)
        ).fetchall()
        line_views = []
        for row in items:
            line = dict(row)
            line["quantity"] = self._quantity_label(
                row["quantity_atomic"], row["base_uom"], row["sale_uom"]
            )
            line["unit_price"] = format_inr(row["unit_price_paise"])
            line["taxable"] = format_inr(row["taxable_paise"])
            line["cgst"] = format_inr(row["cgst_paise"])
            line["sgst"] = format_inr(row["sgst_paise"])
            line["igst"] = format_inr(row["igst_paise"])
            line["gst"] = format_inr(row["gst_paise"])
            line["gross"] = format_inr(row["gross_paise"])
            line["gst_rate_percent"] = str(Decimal(row["gst_rate_bps"]) / Decimal(100))
            line_views.append(line)
        result = dict(bill)
        result.update(
            {
                "ok": True,
                "store": dict(store) if store else None,
                "customer": customer,
                "lines": line_views,
                "taxable": format_inr(bill["taxable_paise"]),
                "cgst": format_inr(bill["cgst_paise"]),
                "sgst": format_inr(bill["sgst_paise"]),
                "igst": format_inr(bill["igst_paise"]),
                "gst": format_inr(bill["gst_paise"]),
                "gross": format_inr(bill["gross_paise"]),
            }
        )
        return result

    def _allocate_invoice_number(
        self, connection: sqlite3.Connection, store: sqlite3.Row, finalized_at: str
    ) -> str:
        number = store["next_invoice_number"]
        local_date = datetime.fromisoformat(finalized_at).astimezone(self.timezone).date()
        fy_start = local_date.year if local_date.month >= 4 else local_date.year - 1
        fiscal_year = f"{fy_start % 100:02d}-{(fy_start + 1) % 100:02d}"
        invoice = f"{store['invoice_prefix']}/{fiscal_year}/{number:06d}"
        connection.execute(
            "UPDATE stores SET next_invoice_number = next_invoice_number + 1, updated_at = ? WHERE id = 1",
            (utc_now(),),
        )
        return invoice

    def _default_payment(self, connection: sqlite3.Connection, owner_id: str) -> str | None:
        row = connection.execute(
            """
            SELECT value_json FROM owner_preferences
            WHERE owner_id = ? AND preference_key = 'default_payment_mode'
            """,
            (owner_id,),
        ).fetchone()
        return str(json.loads(row["value_json"])) if row else None

    @staticmethod
    def _quantity_label(quantity_atomic: int, base_uom: str, sale_uom: str) -> str:
        if base_uom == "g":
            if quantity_atomic >= 1000 and quantity_atomic % 1000 == 0:
                return f"{Decimal(quantity_atomic) / Decimal(1000):g} kg"
            return f"{quantity_atomic} g"
        if base_uom == "ml":
            if quantity_atomic >= 1000 and quantity_atomic % 1000 == 0:
                return f"{Decimal(quantity_atomic) / Decimal(1000):g} L"
            return f"{quantity_atomic} ml"
        return f"{Decimal(quantity_atomic) / Decimal(1000):g} {sale_uom}"

    def _require_customer(
        self, connection: sqlite3.Connection, customer_id: str
    ) -> sqlite3.Row:
        customer = connection.execute(
            "SELECT * FROM customers WHERE id = ? AND active = 1", (customer_id,)
        ).fetchone()
        require(customer is not None, "CUSTOMER_NOT_FOUND", "The selected customer does not exist or is inactive.")
        return customer

    @staticmethod
    def _khata_balance(connection: sqlite3.Connection, customer_id: str) -> int:
        row = connection.execute(
            "SELECT COALESCE(SUM(amount_delta_paise), 0) AS balance FROM khata_entries WHERE customer_id = ?",
            (customer_id,),
        ).fetchone()
        return int(row["balance"])

    @staticmethod
    def _insert_khata_entry(
        connection: sqlite3.Connection,
        *,
        customer_id: str,
        entry_type: str,
        amount_delta_paise: int,
        note: str,
        payment_mode: str | None,
        payment_reference: str | None,
        reference_type: str | None,
        reference_id: str | None,
        source_event_id: str,
        idempotency_key: str,
    ) -> str:
        entry_id = str(uuid4())
        connection.execute(
            """
            INSERT INTO khata_entries(
                id, customer_id, entry_type, amount_delta_paise, note,
                payment_mode, payment_reference, reference_type, reference_id,
                source_event_id, idempotency_key, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                entry_id,
                customer_id,
                entry_type,
                amount_delta_paise,
                note,
                payment_mode,
                payment_reference,
                reference_type,
                reference_id,
                source_event_id,
                idempotency_key,
                utc_now(),
            ),
        )
        return entry_id

    @staticmethod
    def _audit(
        connection: sqlite3.Connection,
        *,
        owner_id: str | None,
        source_event_id: str | None,
        action: str,
        entity_type: str,
        entity_id: str,
        payload: Mapping[str, Any] | Sequence[Any],
    ) -> None:
        connection.execute(
            """
            INSERT INTO audit_log(
                id, owner_id, source_event_id, action, entity_type,
                entity_id, payload_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(uuid4()),
                owner_id,
                source_event_id,
                action,
                entity_type,
                entity_id,
                _canonical_json(payload),
                utc_now(),
            ),
        )

    @staticmethod
    def _idempotent_replay(
        connection: sqlite3.Connection,
        idempotency_key: str,
        operation: str,
        arguments: Mapping[str, Any],
    ) -> dict[str, Any] | None:
        row = connection.execute(
            "SELECT * FROM idempotency_records WHERE idempotency_key = ?",
            (idempotency_key,),
        ).fetchone()
        if row is None:
            return None
        argument_hash = _hash_json(arguments)
        require(
            row["operation"] == operation and row["argument_hash"] == argument_hash,
            "IDEMPOTENCY_CONFLICT",
            "This Telegram update was already used for a different mutation.",
            original_operation=row["operation"],
            attempted_operation=operation,
        )
        return json.loads(row["result_json"])

    @staticmethod
    def _store_idempotent_result(
        connection: sqlite3.Connection,
        idempotency_key: str,
        operation: str,
        arguments: Mapping[str, Any],
        result: Mapping[str, Any],
    ) -> None:
        connection.execute(
            """
            INSERT INTO idempotency_records(
                idempotency_key, operation, argument_hash, result_json, created_at
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                idempotency_key,
                operation,
                _hash_json(arguments),
                _canonical_json(result),
                utc_now(),
            ),
        )

    def _coerce_date(self, value: date | str | None) -> date:
        if value is None:
            return datetime.now(self.timezone).date()
        if isinstance(value, datetime):
            return value.astimezone(self.timezone).date()
        if isinstance(value, date):
            return value
        try:
            return date.fromisoformat(value)
        except ValueError as error:
            raise DomainError("INVALID_DATE", "Dates must use YYYY-MM-DD format.") from error

    def _utc_bounds(self, start: date, end: date) -> tuple[str, str]:
        local_start = datetime.combine(start, time.min, tzinfo=self.timezone)
        local_end = datetime.combine(end + timedelta(days=1), time.min, tzinfo=self.timezone)
        return (
            local_start.astimezone(UTC).isoformat(timespec="milliseconds"),
            local_end.astimezone(UTC).isoformat(timespec="milliseconds"),
        )

    def _analytics_between(
        self, start_utc: str, end_utc: str, from_date: str, to_date: str
    ) -> dict[str, Any]:
        with self.db.read() as connection:
            bills = connection.execute(
                """
                SELECT * FROM bills
                WHERE finalized_at >= ? AND finalized_at < ?
                ORDER BY finalized_at
                """,
                (start_utc, end_utc),
            ).fetchall()
            item_rows = connection.execute(
                """
                SELECT bi.*, b.finalized_at
                FROM bill_items bi JOIN bills b ON b.id = bi.bill_id
                WHERE b.finalized_at >= ? AND b.finalized_at < ?
                """,
                (start_utc, end_utc),
            ).fetchall()
            low_stock_rows = connection.execute(
                """
                SELECT p.*, tr.hsn_code, tr.gst_rate_bps, tr.description AS tax_description,
                       tr.packaging_treatment
                FROM products p JOIN tax_rules tr ON tr.id = p.tax_rule_id
                WHERE p.active = 1 AND p.stock_atomic <= p.reorder_atomic
                ORDER BY p.stock_atomic ASC, p.name
                LIMIT 20
                """
            ).fetchall()

        totals = {
            "bill_count": len(bills),
            "taxable_paise": sum(row["taxable_paise"] for row in bills),
            "cgst_paise": sum(row["cgst_paise"] for row in bills),
            "sgst_paise": sum(row["sgst_paise"] for row in bills),
            "igst_paise": sum(row["igst_paise"] for row in bills),
            "gst_paise": sum(row["gst_paise"] for row in bills),
            "gross_paise": sum(row["gross_paise"] for row in bills),
        }
        totals.update(
            {
                "taxable": format_inr(totals["taxable_paise"]),
                "cgst": format_inr(totals["cgst_paise"]),
                "sgst": format_inr(totals["sgst_paise"]),
                "igst": format_inr(totals["igst_paise"]),
                "gst": format_inr(totals["gst_paise"]),
                "gross": format_inr(totals["gross_paise"]),
            }
        )

        tender: dict[str, dict[str, Any]] = {}
        daily: dict[str, dict[str, Any]] = {}
        for bill in bills:
            mode = bill["payment_mode"]
            bucket = tender.setdefault(mode, {"payment_mode": mode, "bill_count": 0, "gross_paise": 0})
            bucket["bill_count"] += 1
            bucket["gross_paise"] += bill["gross_paise"]
            business_day = datetime.fromisoformat(bill["finalized_at"]).astimezone(self.timezone).date().isoformat()
            day_bucket = daily.setdefault(
                business_day,
                {"date": business_day, "bill_count": 0, "gross_paise": 0, "gst_paise": 0},
            )
            day_bucket["bill_count"] += 1
            day_bucket["gross_paise"] += bill["gross_paise"]
            day_bucket["gst_paise"] += bill["gst_paise"]
        for bucket in tender.values():
            bucket["gross"] = format_inr(bucket["gross_paise"])
        for bucket in daily.values():
            bucket["gross"] = format_inr(bucket["gross_paise"])
            bucket["gst"] = format_inr(bucket["gst_paise"])

        products: dict[int, dict[str, Any]] = {}
        slabs: dict[int, dict[str, Any]] = {}
        for line in item_rows:
            product = products.setdefault(
                line["product_id"],
                {
                    "product_id": line["product_id"],
                    "sku": line["sku"],
                    "product_name": line["product_name"],
                    "quantity_atomic": 0,
                    "gross_paise": 0,
                },
            )
            product["quantity_atomic"] += line["quantity_atomic"]
            product["gross_paise"] += line["gross_paise"]
            slab = slabs.setdefault(
                line["gst_rate_bps"],
                {
                    "gst_rate_bps": line["gst_rate_bps"],
                    "gst_rate_percent": str(Decimal(line["gst_rate_bps"]) / Decimal(100)),
                    "taxable_paise": 0,
                    "gst_paise": 0,
                    "gross_paise": 0,
                },
            )
            slab["taxable_paise"] += line["taxable_paise"]
            slab["gst_paise"] += line["gst_paise"]
            slab["gross_paise"] += line["gross_paise"]
        top_products = sorted(products.values(), key=lambda item: (-item["gross_paise"], item["product_name"]))[:10]
        for product in top_products:
            product["gross"] = format_inr(product["gross_paise"])
        for slab in slabs.values():
            slab["taxable"] = format_inr(slab["taxable_paise"])
            slab["gst"] = format_inr(slab["gst_paise"])
            slab["gross"] = format_inr(slab["gross_paise"])

        return {
            "from_date": from_date,
            "to_date": to_date,
            "timezone": str(self.timezone),
            "totals": totals,
            "daily_sales": sorted(daily.values(), key=lambda item: item["date"]),
            "payment_mix": sorted(tender.values(), key=lambda item: (-item["gross_paise"], item["payment_mode"])),
            "gst_by_slab": sorted(slabs.values(), key=lambda item: item["gst_rate_bps"]),
            "top_products": top_products,
            "low_stock": [_public_product(row) for row in low_stock_rows],
            "generated_at": utc_now(),
        }
