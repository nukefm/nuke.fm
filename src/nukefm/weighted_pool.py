from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_DOWN, ROUND_UP, getcontext


getcontext().prec = 50

ONE = Decimal("1")
PRICE_QUANTUM = Decimal("0.000001")
TINY_PRICE_QUANTUM = Decimal("0.000000000001")


@dataclass(frozen=True)
class WeightedPoolState:
    long_reserve_atomic: int
    short_reserve_atomic: int
    long_weight: Decimal
    short_weight: Decimal
    cash_backing_atomic: int
    total_liquidity_atomic: int


def parse_decimal(value: str | int | float | Decimal) -> Decimal:
    return value if isinstance(value, Decimal) else Decimal(str(value))


def format_decimal(value: Decimal, quantum: Decimal = PRICE_QUANTUM) -> str:
    if value != 0 and abs(value) < quantum:
        normalized = value.quantize(TINY_PRICE_QUANTUM)
        return format(normalized.normalize(), "f")
    normalized = value.quantize(quantum)
    return format(normalized.normalize(), "f")


def long_price(pool: WeightedPoolState) -> Decimal:
    long_reserve = Decimal(pool.long_reserve_atomic)
    short_reserve = Decimal(pool.short_reserve_atomic)
    numerator = short_reserve * pool.long_weight
    denominator = numerator + long_reserve * pool.short_weight
    return numerator / denominator


def short_price(pool: WeightedPoolState) -> Decimal:
    return ONE - long_price(pool)


def amount_out_given_in(
    *,
    reserve_in_atomic: int,
    reserve_out_atomic: int,
    weight_in: Decimal,
    weight_out: Decimal,
    amount_in_atomic: int,
) -> int:
    if amount_in_atomic <= 0:
        raise ValueError("Trade amount must be positive.")

    reserve_in = Decimal(reserve_in_atomic)
    reserve_out = Decimal(reserve_out_atomic)
    amount_in = Decimal(amount_in_atomic)
    ratio = reserve_in / (reserve_in + amount_in)
    exponent = weight_in / weight_out
    amount_out = reserve_out * (ONE - _pow_decimal(ratio, exponent))
    return int(amount_out.to_integral_value(rounding=ROUND_DOWN))


def amount_in_given_out(
    *,
    reserve_in_atomic: int,
    reserve_out_atomic: int,
    weight_in: Decimal,
    weight_out: Decimal,
    amount_out_atomic: int,
) -> int:
    if amount_out_atomic <= 0:
        raise ValueError("Trade amount must be positive.")
    if amount_out_atomic >= reserve_out_atomic:
        raise ValueError("Trade amount exceeds available pool depth.")

    reserve_in = Decimal(reserve_in_atomic)
    reserve_out = Decimal(reserve_out_atomic)
    amount_out = Decimal(amount_out_atomic)
    ratio = reserve_out / (reserve_out - amount_out)
    exponent = weight_out / weight_in
    amount_in = reserve_in * (_pow_decimal(ratio, exponent) - ONE)
    return int(amount_in.to_integral_value(rounding=ROUND_UP))


def retuned_weights_for_equal_liquidity(
    *,
    long_reserve_atomic: int,
    short_reserve_atomic: int,
    equal_liquidity_atomic: int,
    preserved_long_price: Decimal,
) -> tuple[Decimal, Decimal]:
    return weights_for_price(
        long_reserve_atomic=long_reserve_atomic + equal_liquidity_atomic,
        short_reserve_atomic=short_reserve_atomic + equal_liquidity_atomic,
        long_price=preserved_long_price,
    )


def weights_for_price(
    *,
    long_reserve_atomic: int,
    short_reserve_atomic: int,
    long_price: Decimal,
) -> tuple[Decimal, Decimal]:
    if long_reserve_atomic <= 0 or short_reserve_atomic <= 0:
        raise ValueError("Pool reserves must remain positive.")
    if long_price <= 0 or long_price >= ONE:
        raise ValueError("Long price must be inside (0, 1).")

    long_reserve = Decimal(long_reserve_atomic)
    short_reserve = Decimal(short_reserve_atomic)
    numerator = long_price * long_reserve
    denominator = (short_reserve * (ONE - long_price)) + numerator
    long_weight = numerator / denominator
    return long_weight, ONE - long_weight


def _pow_decimal(base: Decimal, exponent: Decimal) -> Decimal:
    return (base.ln() * exponent).exp()
