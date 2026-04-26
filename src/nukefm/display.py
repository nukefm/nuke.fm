from __future__ import annotations

from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
import re

from .weighted_pool import parse_decimal


PRICE_QUANTUM = Decimal("0.000001")
TABLE_QUANTUM = Decimal("0.01")


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


def format_usd_table_display(
    value: str | int | float | Decimal | None,
    *,
    decimal_places: int = 2,
    preserve_tiny_price: bool = False,
) -> str | None:
    if value is None:
        return None

    decimal_value = parse_decimal(value)
    if preserve_tiny_price and decimal_value != 0 and abs(decimal_value) < TABLE_QUANTUM:
        return format_usd_display(decimal_value)

    quantum = Decimal(1).scaleb(-decimal_places)
    return f"${format(decimal_value.quantize(quantum, rounding=ROUND_HALF_UP), f',.{decimal_places}f')}"


def format_percent_table_display(value: str | int | float | Decimal | None) -> str | None:
    if value is None:
        return None

    text = str(value).strip()
    if text == "":
        return None
    if text.endswith("%"):
        text = text[:-1]

    return f"{format(parse_decimal(text).quantize(TABLE_QUANTUM), ',.2f')}%"


def format_short_deadline(value: str | None) -> str | None:
    if value is None:
        return None

    deadline = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return f"By {deadline.day} {deadline.strftime('%b')}"


def predicted_nuke_sign_class(value: str | int | float | Decimal | None) -> str | None:
    if value is None:
        return None

    decimal_value = parse_decimal(value)
    if decimal_value > 0:
        return "nuke-sign-positive"
    if decimal_value < 0:
        return "nuke-sign-negative"
    return "nuke-sign-neutral"


def rationale_bullets(value: str | None) -> list[str]:
    if value is None:
        return []

    text = " ".join(value.split())
    if not text:
        return []

    return [sentence.strip() for sentence in re.split(r"(?<=[.!?])\s+", text) if sentence.strip()]
