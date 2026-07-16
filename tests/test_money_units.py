from __future__ import annotations

from decimal import Decimal

import pytest

from kirana_agent.domain.money import inclusive_tax_breakdown, rupees_to_paise
from kirana_agent.domain.units import line_gross_paise, to_atomic_quantity


@pytest.mark.parametrize(
    ("gst_rate_bps", "taxable", "gst", "cgst", "sgst"),
    [
        (0, 10_000, 0, 0, 0),
        (500, 9_524, 476, 238, 238),
        (1200, 8_929, 1_071, 535, 536),
        (1800, 8_475, 1_525, 762, 763),
    ],
)
def test_tax_inclusive_fixed_point_golden_values(
    gst_rate_bps: int,
    taxable: int,
    gst: int,
    cgst: int,
    sgst: int,
) -> None:
    result = inclusive_tax_breakdown(10_000, gst_rate_bps, intra_state=True)

    assert result.gross_paise == 10_000
    assert result.taxable_paise == taxable
    assert result.gst_paise == gst
    assert result.cgst_paise == cgst
    assert result.sgst_paise == sgst
    assert result.igst_paise == 0
    result.assert_balanced()


@pytest.mark.parametrize("gst_rate_bps", [0, 500, 1200, 1800])
def test_interstate_tax_is_entirely_igst(gst_rate_bps: int) -> None:
    result = inclusive_tax_breakdown(12_345, gst_rate_bps, intra_state=False)

    assert result.cgst_paise == 0
    assert result.sgst_paise == 0
    assert result.igst_paise == result.gst_paise
    result.assert_balanced()


def test_money_rounds_half_up_to_paise() -> None:
    assert rupees_to_paise("12.344") == 1_234
    assert rupees_to_paise("12.345") == 1_235
    assert rupees_to_paise(Decimal("0.005")) == 1


@pytest.mark.parametrize(
    ("quantity", "requested_unit", "base_unit", "expected"),
    [
        ("2", "kg", "g", 2_000),
        ("250", "g", "g", 250),
        ("1.5", "litre", "ml", 1_500),
        ("750", "ml", "ml", 750),
        ("3", "packet", "piece", 3_000),
        ("2", "dozen", "packet", 24_000),
    ],
)
def test_user_quantities_convert_to_integer_base_atoms(
    quantity: str,
    requested_unit: str,
    base_unit: str,
    expected: int,
) -> None:
    assert to_atomic_quantity(quantity, requested_unit, base_unit) == expected


@pytest.mark.parametrize(
    ("quantity", "requested_unit", "base_unit"),
    [
        ("1.5", "packet", "piece"),
        ("1", "litre", "g"),
        ("0.0005", "kg", "g"),
        ("0", "kg", "g"),
    ],
)
def test_invalid_or_unrepresentable_quantities_are_rejected(
    quantity: str, requested_unit: str, base_unit: str
) -> None:
    with pytest.raises(ValueError):
        to_atomic_quantity(quantity, requested_unit, base_unit)


def test_line_value_uses_the_catalog_pricing_quantum() -> None:
    assert line_gross_paise(4_000, 2_000, "g") == 8_000
    assert line_gross_paise(4_000, 250, "g") == 1_000
    assert line_gross_paise(1_400, 3_000, "piece") == 4_200
