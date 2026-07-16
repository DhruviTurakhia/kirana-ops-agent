from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from typing import Final

ATOMIC_PER_UNIT: Final = 1000
_MASS_TO_GRAMS: Final = {
    "kg": Decimal("1000"),
    "g": Decimal("1"),
}
_VOLUME_TO_ML: Final = {
    "litre": Decimal("1000"),
    "l": Decimal("1000"),
    "ml": Decimal("1"),
}
_COUNT_UNITS: Final = {
    "piece",
    "packet",
    "pack",
    "bag",
    "bar",
    "bottle",
    "box",
    "can",
    "carton",
    "cup",
    "jar",
    "loaf",
    "pouch",
    "tub",
    "tube",
}


def to_atomic_quantity(quantity: str | int | Decimal, requested_unit: str, base_unit: str) -> int:
    """Convert user-facing quantities into an integer canonical quantity.

    Mass products use grams, volume products use millilitres, and count products
    use thousandths of their catalog sale unit. Thousandths let the same storage
    type represent all products while count guardrails still require whole units.
    """

    amount = Decimal(str(quantity))
    if amount <= 0:
        raise ValueError("quantity must be positive")
    requested = requested_unit.strip().lower()
    base = base_unit.strip().lower()

    if base == "g":
        if requested not in _MASS_TO_GRAMS:
            raise ValueError(f"{requested_unit} is not compatible with a mass product")
        atomic = amount * _MASS_TO_GRAMS[requested]
    elif base == "ml":
        if requested not in _VOLUME_TO_ML:
            raise ValueError(f"{requested_unit} is not compatible with a volume product")
        atomic = amount * _VOLUME_TO_ML[requested]
    elif base in _COUNT_UNITS:
        if requested == "dozen":
            amount *= 12
        elif requested not in _COUNT_UNITS and requested not in {"unit", "units"}:
            raise ValueError(f"{requested_unit} is not compatible with a count product")
        if amount != amount.to_integral_value():
            raise ValueError("count products must be sold in whole units")
        atomic = amount * ATOMIC_PER_UNIT
    else:
        raise ValueError(f"unsupported base unit: {base_unit}")

    rounded = atomic.quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    if rounded != atomic:
        raise ValueError("quantity is smaller than the supported minimum increment")
    return int(rounded)


def atomic_to_display(quantity_atomic: int, base_unit: str) -> tuple[Decimal, str]:
    base = base_unit.lower()
    if base in {"g", "ml"}:
        return Decimal(quantity_atomic), base
    return (Decimal(quantity_atomic) / Decimal(ATOMIC_PER_UNIT), base)


def line_gross_paise(unit_price_paise: int, quantity_atomic: int, base_unit: str) -> int:
    if quantity_atomic < 0:
        raise ValueError("quantity cannot be negative")
    denominator = Decimal(1000)
    # Prices for mass/volume products are per kg/litre (1,000 base atoms).
    value = Decimal(unit_price_paise) * Decimal(quantity_atomic) / denominator
    return int(value.quantize(Decimal("1"), rounding=ROUND_HALF_UP))
