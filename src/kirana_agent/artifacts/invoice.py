from __future__ import annotations

import os
from collections import defaultdict
from pathlib import Path
from typing import Any

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    KeepTogether,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from kirana_agent.domain.money import format_inr
from kirana_agent.domain.service import StoreService

TEMPLATE_VERSION = "invoice-v2"
NAVY = colors.HexColor("#132238")
TEAL = colors.HexColor("#0F8B8D")
PALE_TEAL = colors.HexColor("#E9F7F6")
PALE_GRAY = colors.HexColor("#F4F6F8")
MID_GRAY = colors.HexColor("#6B7280")


def _register_fonts() -> tuple[str, str]:
    regular_candidates = [
        Path(os.environ.get("KIRANA_FONT_REGULAR", "")),
        Path(__file__).resolve().parents[1] / "assets" / "fonts" / "DejaVuSans.ttf",
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
        Path("C:/Windows/Fonts/segoeui.ttf"),
    ]
    bold_candidates = [
        Path(os.environ.get("KIRANA_FONT_BOLD", "")),
        Path(__file__).resolve().parents[1] / "assets" / "fonts" / "DejaVuSans-Bold.ttf",
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
        Path("C:/Windows/Fonts/segoeuib.ttf"),
    ]
    regular = next((path for path in regular_candidates if str(path) and path.is_file()), None)
    bold = next((path for path in bold_candidates if str(path) and path.is_file()), None)
    if regular is None or bold is None:
        raise RuntimeError(
            "A Unicode font with the rupee symbol is required. Install DejaVu Sans "
            "or set KIRANA_FONT_REGULAR and KIRANA_FONT_BOLD."
        )
    if "KiranaSans" not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont("KiranaSans", str(regular)))
        pdfmetrics.registerFont(TTFont("KiranaSans-Bold", str(bold)))
        pdfmetrics.registerFontFamily(
            "KiranaSans",
            normal="KiranaSans",
            bold="KiranaSans-Bold",
        )
    return "KiranaSans", "KiranaSans-Bold"


class InvoiceGenerator:
    def __init__(self, service: StoreService, output_dir: str | Path):
        self.service = service
        self.output_dir = Path(output_dir)

    def generate(self, bill_reference: str) -> dict[str, Any]:
        bill = self.service.get_bill(bill_reference)
        source_hash = self.service.content_hash(bill)
        cached = self.service.find_artifact(
            artifact_type="INVOICE_PDF",
            source_id=bill["id"],
            source_hash=source_hash,
            template_version=TEMPLATE_VERSION,
        )
        if cached:
            return {"ok": True, "cached": True, **cached}

        invoice_dir = self.output_dir / "pdf"
        invoice_dir.mkdir(parents=True, exist_ok=True)
        safe_number = bill["invoice_number"].replace("/", "-")
        path = invoice_dir / f"invoice-{safe_number}.pdf"
        self._render(bill, path)
        record = self.service.record_artifact(
            artifact_type="INVOICE_PDF",
            source_id=bill["id"],
            source_hash=source_hash,
            template_version=TEMPLATE_VERSION,
            file_path=path,
        )
        return {"ok": True, "cached": False, **record}

    def _render(self, bill: dict[str, Any], path: Path) -> None:
        regular, bold = _register_fonts()
        styles = getSampleStyleSheet()
        body = ParagraphStyle(
            "InvoiceBody",
            parent=styles["BodyText"],
            fontName=regular,
            fontSize=8.5,
            leading=11,
            textColor=NAVY,
        )
        small = ParagraphStyle(
            "InvoiceSmall",
            parent=body,
            fontSize=7,
            leading=9,
            textColor=MID_GRAY,
        )
        h1 = ParagraphStyle(
            "InvoiceTitle",
            parent=body,
            fontName=bold,
            fontSize=17,
            leading=20,
            textColor=NAVY,
        )
        label = ParagraphStyle(
            "InvoiceLabel",
            parent=body,
            fontName=bold,
            fontSize=7.5,
            textColor=TEAL,
        )
        right = ParagraphStyle("InvoiceRight", parent=body, alignment=TA_RIGHT)
        center = ParagraphStyle("InvoiceCenter", parent=body, alignment=TA_CENTER)
        table_head = ParagraphStyle(
            "InvoiceTableHead",
            parent=center,
            fontName=bold,
            fontSize=7,
            leading=8,
            textColor=colors.white,
        )

        store = bill["store"]
        customer = bill.get("customer")
        mixed_tax = any(line["gst_rate_bps"] == 0 for line in bill["lines"]) and any(
            line["gst_rate_bps"] > 0 for line in bill["lines"]
        )
        document_label = (
            "INVOICE-CUM-BILL OF SUPPLY"
            if mixed_tax
            else ("TAX INVOICE" if bill["gst_paise"] else "BILL OF SUPPLY")
        )
        demo_mode = not bool(store.get("gstin"))

        doc = SimpleDocTemplate(
            str(path),
            pagesize=A4,
            leftMargin=15 * mm,
            rightMargin=15 * mm,
            topMargin=15 * mm,
            bottomMargin=15 * mm,
            title=f"Invoice {bill['invoice_number']}",
            author=store["display_name"],
        )

        def page_chrome(canvas: Any, document: Any) -> None:
            canvas.saveState()
            width, height = A4
            canvas.setStrokeColor(TEAL)
            canvas.setLineWidth(1.2)
            canvas.line(15 * mm, height - 10 * mm, width - 15 * mm, height - 10 * mm)
            canvas.setFont(regular, 7)
            canvas.setFillColor(MID_GRAY)
            canvas.drawString(15 * mm, 8 * mm, f"{store['display_name']} · {bill['invoice_number']}")
            canvas.drawRightString(width - 15 * mm, 8 * mm, f"Page {document.page}")
            if demo_mode:
                canvas.setFillColor(colors.Color(0.82, 0.13, 0.18, alpha=0.12))
                canvas.setFont(bold, 34)
                canvas.translate(width / 2, height / 2)
                canvas.rotate(33)
                canvas.drawCentredString(0, 0, "DEMO · NOT A TAX INVOICE")
            canvas.restoreState()

        story: list[Any] = []
        header = Table(
            [
                [
                    Paragraph(store["display_name"], h1),
                    Paragraph(document_label, ParagraphStyle("DocType", parent=h1, alignment=TA_RIGHT, fontSize=13)),
                ],
                [
                    Paragraph(
                        f"{store['legal_name']}<br/>{store['address']}<br/>"
                        f"State code: {store['state_code']} · GSTIN: {store.get('gstin') or 'Not configured (demo)' }",
                        body,
                    ),
                    Paragraph(
                        f"<b>Invoice</b> {bill['invoice_number']}<br/>"
                        f"<b>Date</b> {bill['finalized_at'][:10]}<br/>"
                        f"<b>Supply</b> {bill['supply_type'].replace('_', ' ').title()}",
                        right,
                    ),
                ],
            ],
            colWidths=[112 * mm, 68 * mm],
        )
        header.setStyle(
            TableStyle(
                [
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("LINEBELOW", (0, 0), (-1, 0), 0.6, colors.HexColor("#D8E2E8")),
                    ("BOTTOMPADDING", (0, 0), (-1, 0), 6),
                    ("TOPPADDING", (0, 1), (-1, 1), 7),
                ]
            )
        )
        story.extend([header, Spacer(1, 7 * mm)])

        customer_text = (
            f"{customer['name']}"
            + (f" · {customer['phone']}" if customer and customer.get("phone") else "")
            if customer
            else "Walk-in customer"
        )
        payment_reference = bill.get("payment_reference") or "—"
        info = Table(
            [
                [Paragraph("BILLED TO", label), Paragraph("PAYMENT", label), Paragraph("PLACE OF SUPPLY", label)],
                [
                    Paragraph(customer_text, body),
                    Paragraph(f"{bill['payment_mode']}<br/>Ref: {payment_reference}", body),
                    Paragraph(bill["place_of_supply_state_code"], body),
                ],
            ],
            colWidths=[70 * mm, 60 * mm, 50 * mm],
        )
        info.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, -1), PALE_TEAL),
                    ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#B9DEDC")),
                    ("INNERGRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#CEE8E6")),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 6),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                    ("TOPPADDING", (0, 0), (-1, -1), 5),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                ]
            )
        )
        story.extend([info, Spacer(1, 6 * mm)])

        item_data = [
            [
                Paragraph("#", table_head),
                Paragraph("Item", table_head),
                Paragraph("HSN", table_head),
                Paragraph("Qty", table_head),
                Paragraph("Rate", table_head),
                Paragraph("Taxable", table_head),
                Paragraph("GST", table_head),
                Paragraph("Amount", table_head),
            ]
        ]
        for index, line in enumerate(bill["lines"], start=1):
            tax_label = f"{line['gst_rate_percent']}% · {line['gst']}"
            item_data.append(
                [
                    Paragraph(str(index), center),
                    Paragraph(f"<b>{line['product_name']}</b><br/><font color='#6B7280'>{line['sku']}</font>", body),
                    Paragraph(line["hsn_code"], center),
                    Paragraph(line["quantity"], right),
                    Paragraph(line["unit_price"], right),
                    Paragraph(line["taxable"], right),
                    Paragraph(tax_label, right),
                    Paragraph(line["gross"], right),
                ]
            )
        item_table = Table(
            item_data,
            repeatRows=1,
            colWidths=[7 * mm, 45 * mm, 17 * mm, 20 * mm, 22 * mm, 24 * mm, 24 * mm, 24 * mm],
        )
        item_table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), NAVY),
                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                    ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#D5DCE2")),
                    ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, PALE_GRAY]),
                    ("LEFTPADDING", (0, 0), (-1, -1), 4),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                    ("TOPPADDING", (0, 1), (-1, -1), 5),
                    ("BOTTOMPADDING", (0, 1), (-1, -1), 5),
                ]
            )
        )
        story.extend([item_table, Spacer(1, 6 * mm)])

        slab_totals: dict[int, dict[str, int]] = defaultdict(
            lambda: {"taxable": 0, "cgst": 0, "sgst": 0, "igst": 0, "gst": 0}
        )
        for line in bill["lines"]:
            slab = slab_totals[line["gst_rate_bps"]]
            for field in slab:
                slab[field] += line[f"{field}_paise"]
        slab_rows = [["GST slab", "Taxable", "CGST", "SGST", "IGST", "Total tax"]]
        for rate, values in sorted(slab_totals.items()):
            slab_rows.append(
                [
                    f"{rate / 100:g}%",
                    format_inr(values["taxable"]),
                    format_inr(values["cgst"]),
                    format_inr(values["sgst"]),
                    format_inr(values["igst"]),
                    format_inr(values["gst"]),
                ]
            )
        slab_table = Table(
            slab_rows,
            colWidths=[18 * mm, 22 * mm, 19 * mm, 19 * mm, 19 * mm, 19 * mm],
        )
        slab_table.setStyle(
            TableStyle(
                [
                    ("FONTNAME", (0, 0), (-1, 0), bold),
                    ("FONTNAME", (0, 1), (-1, -1), regular),
                    ("FONTSIZE", (0, 0), (-1, -1), 7),
                    ("BACKGROUND", (0, 0), (-1, 0), PALE_GRAY),
                    ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#D5DCE2")),
                    ("ALIGN", (1, 0), (-1, -1), "RIGHT"),
                    ("TOPPADDING", (0, 0), (-1, -1), 4),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ]
            )
        )
        totals_table = Table(
            [
                [Paragraph("Taxable value", body), Paragraph(bill["taxable"], right)],
                [Paragraph("CGST", body), Paragraph(bill["cgst"], right)],
                [Paragraph("SGST", body), Paragraph(bill["sgst"], right)],
                [Paragraph("IGST", body), Paragraph(bill["igst"], right)],
                [Paragraph("GRAND TOTAL", ParagraphStyle("GrandLabel", parent=body, fontName=bold, fontSize=10)), Paragraph(bill["gross"], ParagraphStyle("GrandValue", parent=right, fontName=bold, fontSize=11, textColor=TEAL))],
            ],
            colWidths=[32 * mm, 28 * mm],
        )
        totals_table.setStyle(
            TableStyle(
                [
                    ("LINEABOVE", (0, -1), (-1, -1), 1, TEAL),
                    ("TOPPADDING", (0, 0), (-1, -1), 4),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ]
            )
        )
        closing = Table([[slab_table, totals_table]], colWidths=[118 * mm, 62 * mm])
        closing.setStyle(
            TableStyle(
                [
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 0),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                ]
            )
        )
        story.append(KeepTogether([closing, Spacer(1, 8 * mm)]))
        story.append(
            Paragraph(
                "Amounts are computed from tax-inclusive catalog prices using fixed-point paise arithmetic. "
                "This document is generated from the immutable finalized bill snapshot.",
                small,
            )
        )
        if demo_mode:
            story.extend(
                [
                    Spacer(1, 3 * mm),
                    Paragraph(
                        "DEMO NOTICE: Seller GSTIN is not configured. This sample demonstrates software behavior and must not be used as a legal tax invoice.",
                        ParagraphStyle("DemoNotice", parent=small, textColor=colors.HexColor("#A61B1B"), fontName=bold),
                    ),
                ]
            )
        doc.build(story, onFirstPage=page_chrome, onLaterPages=page_chrome)
