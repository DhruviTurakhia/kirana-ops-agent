from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Final

PAISE_PER_RUPEE: Final = Decimal("100")
BASIS_POINTS_PER_ONE: Final = Decimal("10000")
PAISE_QUANTUM: Final = Decimal("1")


def round_half_up(value: Decimal) -> int:
    return int(value.quantize(PAISE_QUANTUM, rounding=ROUND_HALF_UP))


def rupees_to_paise(value: str | int | Decimal) -> int:
    decimal_value = Decimal(str(value))
    if decimal_value < 0:
        raise ValueError("money cannot be negative")
    return round_half_up(decimal_value * PAISE_PER_RUPEE)


def paise_to_rupees(paise: int) -> Decimal:
    return (Decimal(paise) / PAISE_PER_RUPEE).quantize(Decimal("0.01"))


def format_inr(paise: int) -> str:
    value = paise_to_rupees(paise)
    return f"₹{value:,.2f}"


@dataclass(frozen=True, slots=True)
class TaxBreakdown:
    gross_paise: int
    taxable_paise: int
    gst_paise: int
    cgst_paise: int
    sgst_paise: int
    igst_paise: int

    def assert_balanced(self) -> None:
        assert self.taxable_paise + self.gst_paise == self.gross_paise
        assert self.cgst_paise + self.sgst_paise + self.igst_paise == self.gst_paise


def inclusive_tax_breakdown(
    gross_paise: int,
    gst_rate_bps: int,
    *,
    intra_state: bool = True,
) -> TaxBreakdown:
    """Extract GST from a tax-inclusive line using fixed-point arithmetic.

    The header is always the sum of stored line results. For an odd tax paise in
    an intra-state sale, the residual paise is assigned to SGST so reconciliation
    remains exact and deterministic.
    """

    if gross_paise < 0:
        raise ValueError("gross_paise cannot be negative")
    if gst_rate_bps < 0 or gst_rate_bps > 10000:
        raise ValueError("gst_rate_bps must be between 0 and 10000")

    if gst_rate_bps == 0:
        result = TaxBreakdown(gross_paise, gross_paise, 0, 0, 0, 0)
        result.assert_balanced()
        return result

    denominator = BASIS_POINTS_PER_ONE + Decimal(gst_rate_bps)
    taxable = round_half_up(Decimal(gross_paise) * BASIS_POINTS_PER_ONE / denominator)
    gst = gross_paise - taxable
    if intra_state:
        cgst = gst // 2
        sgst = gst - cgst
        igst = 0
    else:
        cgst = 0
        sgst = 0
        igst = gst

    result = TaxBreakdown(gross_paise, taxable, gst, cgst, sgst, igst)
    result.assert_balanced()
    return result
