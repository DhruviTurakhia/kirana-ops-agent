PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS schema_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS stores (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    legal_name TEXT NOT NULL,
    display_name TEXT NOT NULL,
    address TEXT NOT NULL DEFAULT '',
    state_code TEXT NOT NULL,
    gstin TEXT,
    timezone TEXT NOT NULL DEFAULT 'Asia/Kolkata',
    invoice_prefix TEXT NOT NULL DEFAULT 'KOA',
    next_invoice_number INTEGER NOT NULL DEFAULT 1 CHECK (next_invoice_number > 0),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tax_rules (
    id TEXT PRIMARY KEY,
    hsn_code TEXT NOT NULL,
    description TEXT NOT NULL,
    gst_rate_bps INTEGER NOT NULL CHECK (gst_rate_bps IN (0, 500, 1200, 1800, 2800)),
    packaging_treatment TEXT NOT NULL,
    price_tax_inclusive INTEGER NOT NULL DEFAULT 1 CHECK (price_tax_inclusive IN (0, 1)),
    effective_from TEXT NOT NULL,
    effective_to TEXT,
    source_url TEXT NOT NULL,
    verified_at TEXT NOT NULL,
    version TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1))
);

CREATE TABLE IF NOT EXISTS products (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sku TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    normalized_name TEXT NOT NULL,
    aliases_json TEXT NOT NULL DEFAULT '[]',
    brand TEXT,
    category TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('PACKAGED', 'LOOSE', 'FRESH')),
    tax_rule_id TEXT NOT NULL REFERENCES tax_rules(id),
    base_uom TEXT NOT NULL,
    sale_uom TEXT NOT NULL,
    pack_size TEXT,
    min_sale_atomic INTEGER NOT NULL DEFAULT 1000 CHECK (min_sale_atomic > 0),
    cost_paise INTEGER CHECK (cost_paise IS NULL OR cost_paise >= 0),
    sell_paise INTEGER NOT NULL CHECK (sell_paise >= 0),
    mrp_paise INTEGER CHECK (mrp_paise IS NULL OR mrp_paise >= 0),
    stock_atomic INTEGER NOT NULL DEFAULT 0 CHECK (stock_atomic >= 0),
    reorder_atomic INTEGER NOT NULL DEFAULT 0 CHECK (reorder_atomic >= 0),
    barcode TEXT,
    data_status TEXT NOT NULL DEFAULT 'DEMO',
    seed_version TEXT,
    active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
    version INTEGER NOT NULL DEFAULT 1 CHECK (version > 0),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_products_normalized_name ON products(normalized_name);
CREATE INDEX IF NOT EXISTS idx_products_low_stock ON products(active, stock_atomic, reorder_atomic);

CREATE TABLE IF NOT EXISTS stock_movements (
    id TEXT PRIMARY KEY,
    product_id INTEGER NOT NULL REFERENCES products(id),
    movement_type TEXT NOT NULL CHECK (movement_type IN ('OPENING', 'RECEIPT', 'SALE', 'REVERSAL')),
    quantity_delta_atomic INTEGER NOT NULL CHECK (quantity_delta_atomic != 0),
    unit_cost_paise INTEGER CHECK (unit_cost_paise IS NULL OR unit_cost_paise >= 0),
    reference_type TEXT,
    reference_id TEXT,
    source_event_id TEXT,
    idempotency_key TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_stock_movements_product ON stock_movements(product_id, created_at);

CREATE TABLE IF NOT EXISTS customers (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    normalized_name TEXT NOT NULL,
    phone TEXT,
    state_code TEXT,
    active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_customers_name ON customers(normalized_name);

CREATE TABLE IF NOT EXISTS bill_drafts (
    id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL,
    chat_id TEXT NOT NULL,
    customer_id TEXT REFERENCES customers(id),
    status TEXT NOT NULL CHECK (status IN ('OPEN', 'FINALIZED', 'CANCELLED')),
    payment_mode TEXT CHECK (payment_mode IN ('CASH', 'UPI', 'CARD', 'KHATA')),
    payment_reference TEXT,
    revision INTEGER NOT NULL DEFAULT 1 CHECK (revision > 0),
    preview_hash TEXT,
    previewed_at TEXT,
    finalized_bill_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_one_open_draft_per_chat
    ON bill_drafts(chat_id) WHERE status = 'OPEN';

CREATE TABLE IF NOT EXISTS bill_draft_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    draft_id TEXT NOT NULL REFERENCES bill_drafts(id) ON DELETE CASCADE,
    product_id INTEGER NOT NULL REFERENCES products(id),
    quantity_atomic INTEGER NOT NULL CHECK (quantity_atomic > 0),
    product_version INTEGER NOT NULL,
    unit_price_paise INTEGER NOT NULL CHECK (unit_price_paise >= 0),
    tax_rule_id TEXT NOT NULL REFERENCES tax_rules(id),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(draft_id, product_id)
);

CREATE TABLE IF NOT EXISTS bills (
    id TEXT PRIMARY KEY,
    draft_id TEXT NOT NULL UNIQUE REFERENCES bill_drafts(id),
    invoice_number TEXT NOT NULL UNIQUE,
    owner_id TEXT NOT NULL,
    chat_id TEXT NOT NULL,
    customer_id TEXT REFERENCES customers(id),
    supply_type TEXT NOT NULL CHECK (supply_type IN ('INTRA_STATE', 'INTER_STATE')),
    place_of_supply_state_code TEXT NOT NULL,
    payment_mode TEXT NOT NULL CHECK (payment_mode IN ('CASH', 'UPI', 'CARD', 'KHATA')),
    payment_reference TEXT,
    taxable_paise INTEGER NOT NULL CHECK (taxable_paise >= 0),
    cgst_paise INTEGER NOT NULL CHECK (cgst_paise >= 0),
    sgst_paise INTEGER NOT NULL CHECK (sgst_paise >= 0),
    igst_paise INTEGER NOT NULL CHECK (igst_paise >= 0),
    gst_paise INTEGER NOT NULL CHECK (gst_paise >= 0),
    gross_paise INTEGER NOT NULL CHECK (gross_paise >= 0),
    source_event_id TEXT NOT NULL,
    finalized_at TEXT NOT NULL,
    CHECK (taxable_paise + gst_paise = gross_paise),
    CHECK (cgst_paise + sgst_paise + igst_paise = gst_paise)
);

CREATE TABLE IF NOT EXISTS bill_items (
    id TEXT PRIMARY KEY,
    bill_id TEXT NOT NULL REFERENCES bills(id),
    product_id INTEGER NOT NULL REFERENCES products(id),
    sku TEXT NOT NULL,
    product_name TEXT NOT NULL,
    hsn_code TEXT NOT NULL,
    tax_rule_id TEXT NOT NULL,
    gst_rate_bps INTEGER NOT NULL,
    base_uom TEXT NOT NULL,
    sale_uom TEXT NOT NULL,
    quantity_atomic INTEGER NOT NULL CHECK (quantity_atomic > 0),
    unit_price_paise INTEGER NOT NULL CHECK (unit_price_paise >= 0),
    unit_cost_paise INTEGER NOT NULL CHECK (unit_cost_paise >= 0),
    taxable_paise INTEGER NOT NULL CHECK (taxable_paise >= 0),
    cgst_paise INTEGER NOT NULL CHECK (cgst_paise >= 0),
    sgst_paise INTEGER NOT NULL CHECK (sgst_paise >= 0),
    igst_paise INTEGER NOT NULL CHECK (igst_paise >= 0),
    gst_paise INTEGER NOT NULL CHECK (gst_paise >= 0),
    gross_paise INTEGER NOT NULL CHECK (gross_paise >= 0),
    CHECK (taxable_paise + gst_paise = gross_paise),
    CHECK (cgst_paise + sgst_paise + igst_paise = gst_paise)
);

CREATE INDEX IF NOT EXISTS idx_bills_finalized ON bills(finalized_at);
CREATE INDEX IF NOT EXISTS idx_bill_items_bill ON bill_items(bill_id);
CREATE INDEX IF NOT EXISTS idx_bill_items_product ON bill_items(product_id);

CREATE TABLE IF NOT EXISTS khata_entries (
    id TEXT PRIMARY KEY,
    customer_id TEXT NOT NULL REFERENCES customers(id),
    entry_type TEXT NOT NULL CHECK (entry_type IN ('CHARGE', 'CREDIT_SALE', 'PAYMENT', 'REVERSAL')),
    amount_delta_paise INTEGER NOT NULL CHECK (amount_delta_paise != 0),
    note TEXT NOT NULL,
    payment_mode TEXT CHECK (payment_mode IN ('CASH', 'UPI', 'CARD')),
    payment_reference TEXT,
    reference_type TEXT,
    reference_id TEXT,
    source_event_id TEXT NOT NULL,
    idempotency_key TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_khata_customer ON khata_entries(customer_id, created_at);

CREATE TABLE IF NOT EXISTS owner_preferences (
    owner_id TEXT NOT NULL,
    preference_key TEXT NOT NULL,
    value_json TEXT NOT NULL,
    source_event_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (owner_id, preference_key)
);

CREATE TABLE IF NOT EXISTS chat_sessions (
    chat_id TEXT PRIMARY KEY,
    generation INTEGER NOT NULL DEFAULT 1 CHECK (generation > 0),
    focused_draft_id TEXT,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS day_closes (
    business_date TEXT PRIMARY KEY,
    summary_json TEXT NOT NULL,
    source_event_id TEXT NOT NULL UNIQUE,
    closed_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS telegram_updates (
    update_id TEXT PRIMARY KEY,
    chat_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('PROCESSING', 'COMPLETED', 'FAILED')),
    response_text TEXT,
    artifacts_json TEXT NOT NULL DEFAULT '[]',
    error_text TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS idempotency_records (
    idempotency_key TEXT PRIMARY KEY,
    operation TEXT NOT NULL,
    argument_hash TEXT NOT NULL,
    result_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS artifacts (
    id TEXT PRIMARY KEY,
    artifact_type TEXT NOT NULL CHECK (artifact_type IN ('INVOICE_PDF', 'SALES_PPTX')),
    source_id TEXT NOT NULL,
    source_hash TEXT NOT NULL,
    template_version TEXT NOT NULL,
    file_path TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(artifact_type, source_id, source_hash, template_version)
);

CREATE TABLE IF NOT EXISTS audit_log (
    id TEXT PRIMARY KEY,
    owner_id TEXT,
    source_event_id TEXT,
    action TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
