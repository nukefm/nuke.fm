from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_DOWN


USDC_DECIMALS = 6
ATOMIC_UNITS_PER_USDC = 10**USDC_DECIMALS


def parse_usdc_amount(value: str) -> int:
    try:
        decimal_value = Decimal(value)
    except InvalidOperation as error:
        raise ValueError(f"Invalid USDC amount: {value}") from error

    if decimal_value <= 0:
        raise ValueError("USDC amount must be positive.")

    quantized_value = decimal_value.quantize(Decimal("0.000001"), rounding=ROUND_DOWN)
    atomic_amount = int(quantized_value * ATOMIC_UNITS_PER_USDC)
    if atomic_amount <= 0:
        raise ValueError("USDC amount must be at least 0.000001.")
    return atomic_amount


def format_usdc_amount(atomic_amount: int) -> str:
    if atomic_amount < 0:
        sign = "-"
        atomic_amount = abs(atomic_amount)
    else:
        sign = ""

    whole_units, fractional_units = divmod(atomic_amount, ATOMIC_UNITS_PER_USDC)
    if fractional_units == 0:
        return f"{sign}{whole_units}"

    fractional_text = f"{fractional_units:06d}".rstrip("0")
    return f"{sign}{whole_units}.{fractional_text}"
