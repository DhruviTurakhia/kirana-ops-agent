from __future__ import annotations

import asyncio
import logging
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

from agents import function_tool
from agents.tool_context import ToolContext
from pydantic import BaseModel, Field

from kirana_agent.artifacts.deck import SalesDeckGenerator
from kirana_agent.artifacts.invoice import InvoiceGenerator
from kirana_agent.domain.errors import DomainError
from kirana_agent.domain.service import StoreService

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class ArtifactAttachment:
    path: str
    filename: str
    caption: str
    kind: Literal["pdf", "pptx"]

    def as_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass(slots=True)
class AgentContext:
    service: StoreService
    invoice_generator: InvoiceGenerator
    deck_generator: SalesDeckGenerator
    owner_id: str
    chat_id: str
    source_event_id: str
    artifacts: list[ArtifactAttachment] = field(default_factory=list)


class BillLineOperation(BaseModel):
    action: Literal["add", "set", "remove"] = Field(
        description="add increments a line, set replaces its quantity, remove deletes it"
    )
    product_id: int = Field(description="Exact product ID returned by search_products")
    quantity: str | None = Field(
        default=None,
        description="Required for add/set as an exact decimal string; omitted for remove",
    )
    unit: str | None = Field(
        default=None, description="Required for add/set, such as kg, g, packet, piece, or bottle"
    )


async def _call(method: Any, *args: Any, **kwargs: Any) -> Any:
    try:
        return await asyncio.to_thread(method, *args, **kwargs)
    except DomainError as error:
        return error.as_dict()
    except ValueError as error:
        return DomainError("INVALID_INPUT", str(error)).as_dict()
    except Exception:
        logger.exception("Unexpected domain tool failure")
        return DomainError(
            "INTERNAL_TOOL_ERROR",
            "The operation failed unexpectedly. No successful mutation should be assumed.",
        ).as_dict()


@function_tool(name_override="search_products")
async def search_products_tool(ctx: ToolContext[AgentContext], query: str) -> Any:
    """Search the grounded product catalog by name, brand, alias, category, or SKU.

    Use this before any product-specific operation. If several plausible products
    are returned, ask the owner which one they mean rather than choosing silently.
    """

    return await _call(ctx.context.service.search_products, query)


@function_tool(name_override="get_stock")
async def get_stock_tool(ctx: ToolContext[AgentContext], product_ids: list[int]) -> Any:
    """Return current stock, prices, HSN, and GST for exact product IDs."""

    return await _call(ctx.context.service.get_stock, product_ids)


@function_tool(name_override="list_low_stock")
async def list_low_stock_tool(ctx: ToolContext[AgentContext]) -> Any:
    """List active products at or below their configured reorder level."""

    return await _call(ctx.context.service.list_low_stock)


@function_tool(name_override="list_tax_rules")
async def list_tax_rules_tool(
    ctx: ToolContext[AgentContext], query: str | None = None
) -> Any:
    """Find grounded versioned GST/HSN rules before creating a product."""

    return await _call(ctx.context.service.list_tax_rules, query)


@function_tool(name_override="create_product")
async def create_product_tool(
    ctx: ToolContext[AgentContext],
    sku: str,
    name: str,
    category: str,
    kind: Literal["PACKAGED", "LOOSE", "FRESH"],
    tax_rule_id: str,
    base_uom: str,
    sale_uom: str,
    sell_price_rupees: str,
    cost_price_rupees: str | None = None,
    mrp_rupees: str | None = None,
    aliases: list[str] | None = None,
    pack_size: str | None = None,
    reorder_quantity: str = "0",
    min_sale_quantity: str = "1",
) -> Any:
    """Create a catalog product using a tax_rule_id returned by list_tax_rules.

    This creates zero stock. Do not invent HSN or GST metadata. A missing cost is
    allowed during setup but the product remains unsellable until stock is received.
    """

    return await _call(
        ctx.context.service.create_product,
        owner_id=ctx.context.owner_id,
        source_event_id=ctx.context.source_event_id,
        sku=sku,
        name=name,
        category=category,
        kind=kind,
        tax_rule_id=tax_rule_id,
        base_uom=base_uom,
        sale_uom=sale_uom,
        sell_price_rupees=sell_price_rupees,
        cost_price_rupees=cost_price_rupees,
        mrp_rupees=mrp_rupees,
        aliases=aliases or [],
        pack_size=pack_size,
        reorder_quantity=reorder_quantity,
        min_sale_quantity=min_sale_quantity,
    )


@function_tool(name_override="receive_stock")
async def receive_stock_tool(
    ctx: ToolContext[AgentContext],
    product_id: int,
    quantity: str,
    unit: str,
    unit_cost_rupees: str,
    new_mrp_rupees: str | None = None,
    new_sell_price_rupees: str | None = None,
    supplier_reference: str | None = None,
) -> Any:
    """Receive positive stock for one exact product and update its grounded cost.

    The transaction refuses a sell price below received cost or above MRP and is
    idempotent for this Telegram update and product.
    """

    return await _call(
        ctx.context.service.receive_stock,
        owner_id=ctx.context.owner_id,
        source_event_id=ctx.context.source_event_id,
        product_id=product_id,
        quantity=quantity,
        unit=unit,
        unit_cost_rupees=unit_cost_rupees,
        new_mrp_rupees=new_mrp_rupees,
        new_sell_price_rupees=new_sell_price_rupees,
        supplier_reference=supplier_reference,
    )


@function_tool(name_override="get_open_bill")
async def get_open_bill_tool(ctx: ToolContext[AgentContext]) -> Any:
    """Return the current chat's open multi-turn bill draft, if any."""

    result = await _call(ctx.context.service.get_open_bill_draft, ctx.context.chat_id)
    return result or {"ok": True, "draft": None}


@function_tool(name_override="start_bill")
async def start_bill_tool(
    ctx: ToolContext[AgentContext],
    customer_id: str | None = None,
    payment_mode: Literal["CASH", "UPI", "CARD", "KHATA"] | None = None,
) -> Any:
    """Start or return this chat's open bill draft without changing stock."""

    return await _call(
        ctx.context.service.start_bill_draft,
        owner_id=ctx.context.owner_id,
        chat_id=ctx.context.chat_id,
        source_event_id=ctx.context.source_event_id,
        customer_id=customer_id,
        payment_mode=payment_mode,
    )


@function_tool(name_override="patch_bill")
async def patch_bill_tool(
    ctx: ToolContext[AgentContext],
    draft_id: str,
    expected_revision: int,
    operations: list[BillLineOperation],
) -> Any:
    """Apply one utterance's bill-line additions, replacements, and removals atomically.

    Use exact product IDs returned by search_products. Draft edits never decrement
    stock. Prefer one batched call containing every edit in the owner's message.
    """

    return await _call(
        ctx.context.service.patch_bill_draft,
        owner_id=ctx.context.owner_id,
        source_event_id=ctx.context.source_event_id,
        draft_id=draft_id,
        expected_revision=expected_revision,
        operations=[operation.model_dump(exclude_none=True, mode="json") for operation in operations],
    )


@function_tool(name_override="set_bill_payment")
async def set_bill_payment_tool(
    ctx: ToolContext[AgentContext],
    draft_id: str,
    expected_revision: int,
    payment_mode: Literal["CASH", "UPI", "CARD", "KHATA"],
    payment_reference: str | None = None,
) -> Any:
    """Set the draft's payment mode and reference without changing stock."""

    return await _call(
        ctx.context.service.set_bill_payment,
        owner_id=ctx.context.owner_id,
        source_event_id=ctx.context.source_event_id,
        draft_id=draft_id,
        expected_revision=expected_revision,
        payment_mode=payment_mode,
        payment_reference=payment_reference,
    )


@function_tool(name_override="set_bill_customer")
async def set_bill_customer_tool(
    ctx: ToolContext[AgentContext],
    draft_id: str,
    expected_revision: int,
    customer_id: str | None,
) -> Any:
    """Attach or remove a grounded customer on an open bill draft."""

    return await _call(
        ctx.context.service.set_bill_customer,
        owner_id=ctx.context.owner_id,
        source_event_id=ctx.context.source_event_id,
        draft_id=draft_id,
        expected_revision=expected_revision,
        customer_id=customer_id,
    )


@function_tool(name_override="refresh_bill")
async def refresh_bill_tool(
    ctx: ToolContext[AgentContext], draft_id: str, expected_revision: int
) -> Any:
    """Refresh stale price/tax snapshots and return every change for owner review."""

    return await _call(
        ctx.context.service.refresh_bill_draft,
        owner_id=ctx.context.owner_id,
        source_event_id=ctx.context.source_event_id,
        draft_id=draft_id,
        expected_revision=expected_revision,
    )


@function_tool(name_override="preview_bill")
async def preview_bill_tool(
    ctx: ToolContext[AgentContext], draft_id: str, expected_revision: int
) -> Any:
    """Validate stock/rules and return an exact GST preview plus confirmation hash.

    Previewing does not decrement stock. Show the result and wait for explicit owner
    confirmation unless the current message explicitly asked to finalize.
    """

    return await _call(
        ctx.context.service.preview_bill,
        draft_id=draft_id,
        expected_revision=expected_revision,
    )


@function_tool(name_override="finalize_bill")
async def finalize_bill_tool(
    ctx: ToolContext[AgentContext],
    draft_id: str,
    expected_revision: int,
    preview_hash: str,
) -> Any:
    """Atomically finalize an explicitly confirmed preview and decrement stock once.

    Never call this just because the owner said 'make/cut a bill'; first build and
    preview the draft. Call only when this message clearly confirms/finalizes a shown
    preview or explicitly asks to build and finalize in one request.
    """

    return await _call(
        ctx.context.service.finalize_bill,
        owner_id=ctx.context.owner_id,
        source_event_id=ctx.context.source_event_id,
        draft_id=draft_id,
        expected_revision=expected_revision,
        preview_hash=preview_hash,
    )


@function_tool(name_override="cancel_bill")
async def cancel_bill_tool(
    ctx: ToolContext[AgentContext], draft_id: str, expected_revision: int
) -> Any:
    """Cancel an open draft; this never deletes or changes stock."""

    return await _call(
        ctx.context.service.cancel_bill_draft,
        owner_id=ctx.context.owner_id,
        source_event_id=ctx.context.source_event_id,
        draft_id=draft_id,
        expected_revision=expected_revision,
    )


@function_tool(name_override="get_bill")
async def get_bill_tool(ctx: ToolContext[AgentContext], reference: str) -> Any:
    """Load a finalized bill by invoice number or bill ID."""

    return await _call(ctx.context.service.get_bill, reference)


@function_tool(name_override="list_recent_bills")
async def list_recent_bills_tool(
    ctx: ToolContext[AgentContext], limit: int = 10
) -> Any:
    """List recent finalized bills when the owner refers to a bill ambiguously."""

    return await _call(ctx.context.service.list_recent_bills, limit=limit)


@function_tool(name_override="search_customers")
async def search_customers_tool(ctx: ToolContext[AgentContext], query: str) -> Any:
    """Search grounded customers by name or phone before Khata operations."""

    return await _call(ctx.context.service.search_customers, query)


@function_tool(name_override="create_customer")
async def create_customer_tool(
    ctx: ToolContext[AgentContext],
    name: str,
    phone: str | None = None,
    state_code: str | None = None,
) -> Any:
    """Create a customer after the owner clearly asks to open/use a new Khata."""

    return await _call(
        ctx.context.service.create_customer,
        owner_id=ctx.context.owner_id,
        source_event_id=ctx.context.source_event_id,
        name=name,
        phone=phone,
        state_code=state_code,
    )


@function_tool(name_override="get_khata_balance")
async def get_khata_balance_tool(
    ctx: ToolContext[AgentContext], customer_id: str
) -> Any:
    """Return the exact outstanding Khata balance for a grounded customer."""

    return await _call(ctx.context.service.get_khata_balance, customer_id)


@function_tool(name_override="get_khata_statement")
async def get_khata_statement_tool(
    ctx: ToolContext[AgentContext], customer_id: str, limit: int = 20
) -> Any:
    """Return recent immutable Khata ledger entries and the current balance."""

    return await _call(ctx.context.service.get_khata_statement, customer_id, limit=limit)


@function_tool(name_override="record_khata_charge")
async def record_khata_charge_tool(
    ctx: ToolContext[AgentContext],
    customer_id: str,
    amount_rupees: str,
    note: str,
) -> Any:
    """Add a direct positive charge to an existing customer's Khata ledger."""

    return await _call(
        ctx.context.service.record_khata_charge,
        owner_id=ctx.context.owner_id,
        source_event_id=ctx.context.source_event_id,
        customer_id=customer_id,
        amount_rupees=amount_rupees,
        note=note,
    )


@function_tool(name_override="record_khata_payment")
async def record_khata_payment_tool(
    ctx: ToolContext[AgentContext],
    customer_id: str,
    amount_rupees: str,
    payment_mode: Literal["CASH", "UPI", "CARD"],
    payment_reference: str | None = None,
) -> Any:
    """Record settlement against an existing positive Khata balance.

    The transaction rejects a missing ledger, no balance, and overpayment.
    """

    return await _call(
        ctx.context.service.record_khata_payment,
        owner_id=ctx.context.owner_id,
        source_event_id=ctx.context.source_event_id,
        customer_id=customer_id,
        amount_rupees=amount_rupees,
        payment_mode=payment_mode,
        payment_reference=payment_reference,
    )


@function_tool(name_override="get_preferences")
async def get_preferences_tool(ctx: ToolContext[AgentContext]) -> Any:
    """Return this owner's durable operational preferences."""

    return await _call(ctx.context.service.get_preferences, ctx.context.owner_id)


@function_tool(name_override="set_preference")
async def set_preference_tool(
    ctx: ToolContext[AgentContext], key: str, value: str | int
) -> Any:
    """Persist an allowlisted preference across chats.

    Preferred products must use an exact ID from search_products. Explicit current
    message choices always override a stored default without rewriting it.
    """

    return await _call(
        ctx.context.service.set_preference,
        owner_id=ctx.context.owner_id,
        source_event_id=ctx.context.source_event_id,
        key=key,
        value=value,
    )


@function_tool(name_override="clear_preference")
async def clear_preference_tool(ctx: ToolContext[AgentContext], key: str) -> Any:
    """Remove one allowlisted durable owner preference."""

    return await _call(
        ctx.context.service.clear_preference,
        owner_id=ctx.context.owner_id,
        source_event_id=ctx.context.source_event_id,
        key=key,
    )


@function_tool(name_override="get_daily_summary")
async def get_daily_summary_tool(
    ctx: ToolContext[AgentContext], business_date: str | None = None
) -> Any:
    """Return finalized sales, tax, tender mix, top items, and stock health for a day."""

    return await _call(ctx.context.service.daily_summary, business_date)


@function_tool(name_override="close_day")
async def close_day_tool(
    ctx: ToolContext[AgentContext], business_date: str | None = None
) -> Any:
    """Persist an idempotent as-of daily close snapshot without deleting or locking data."""

    return await _call(
        ctx.context.service.close_day,
        owner_id=ctx.context.owner_id,
        source_event_id=ctx.context.source_event_id,
        business_date=business_date,
    )


@function_tool(name_override="generate_invoice_pdf")
async def generate_invoice_pdf_tool(
    ctx: ToolContext[AgentContext], bill_reference: str
) -> Any:
    """Generate and attach a clean PDF for a finalized bill."""

    result = await _call(ctx.context.invoice_generator.generate, bill_reference)
    if not result.get("ok"):
        return result
    bill = await _call(ctx.context.service.get_bill, bill_reference)
    attachment = ArtifactAttachment(
        path=result["file_path"],
        filename=f"invoice-{bill['invoice_number'].replace('/', '-')}.pdf",
        caption=f"Invoice {bill['invoice_number']} · {bill['gross']}",
        kind="pdf",
    )
    ctx.context.artifacts.append(attachment)
    return {
        "ok": True,
        "invoice_number": bill["invoice_number"],
        "gross": bill["gross"],
        "attached_to_telegram_reply": True,
        "cached": result["cached"],
    }


@function_tool(name_override="generate_sales_deck")
async def generate_sales_deck_tool(
    ctx: ToolContext[AgentContext], from_date: str, to_date: str
) -> Any:
    """Generate and attach an editable PowerPoint with charts from real stored data."""

    result = await _call(
        ctx.context.deck_generator.generate, from_date=from_date, to_date=to_date
    )
    if not result.get("ok"):
        return result
    analysis = result["analysis"]
    attachment = ArtifactAttachment(
        path=result["file_path"],
        filename=Path(result["file_path"]).name,
        caption=(
            f"Sales analysis {analysis['from_date']} to {analysis['to_date']} · "
            f"{analysis['totals']['gross']}"
        ),
        kind="pptx",
    )
    ctx.context.artifacts.append(attachment)
    return {
        "ok": True,
        "period": f"{analysis['from_date']} to {analysis['to_date']}",
        "gross_sales": analysis["totals"]["gross"],
        "bill_count": analysis["totals"]["bill_count"],
        "attached_to_telegram_reply": True,
        "cached": result["cached"],
    }


ALL_TOOLS = [
    search_products_tool,
    get_stock_tool,
    list_low_stock_tool,
    list_tax_rules_tool,
    create_product_tool,
    receive_stock_tool,
    get_open_bill_tool,
    start_bill_tool,
    patch_bill_tool,
    set_bill_payment_tool,
    set_bill_customer_tool,
    refresh_bill_tool,
    preview_bill_tool,
    finalize_bill_tool,
    cancel_bill_tool,
    get_bill_tool,
    list_recent_bills_tool,
    search_customers_tool,
    create_customer_tool,
    get_khata_balance_tool,
    get_khata_statement_tool,
    record_khata_charge_tool,
    record_khata_payment_tool,
    get_preferences_tool,
    set_preference_tool,
    clear_preference_tool,
    get_daily_summary_tool,
    close_day_tool,
    generate_invoice_pdf_tool,
    generate_sales_deck_tool,
]
