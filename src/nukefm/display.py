from __future__ import annotations

from decimal import Decimal

from .weighted_pool import parse_decimal


PRICE_QUANTUM = Decimal("0.000001")


def format_usd_display(value: str | int | float | Decimal | None) -> str | None:
    if value is None:
        return None

    decimal_value = parse_decimal(value)
    if decimal_value == 0:
        return "$0"

    if abs(decimal_value) < PRICE_QUANTUM:
        text = format(decimal_value, "f")
    else:
        text = format(decimal_value.quantize(PRICE_QUANTUM), "f")

    if "." in text:
        text = text.rstrip("0").rstrip(".")

    return f"${text}"
