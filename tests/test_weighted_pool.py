from decimal import Decimal

from nukefm.weighted_pool import format_decimal


def test_format_decimal_preserves_small_non_zero_values() -> None:
    assert format_decimal(Decimal("0.00000013761553725380515")) == "0.000000137616"
    assert format_decimal(Decimal("0.000003219639329774355")) == "0.000003"
    assert format_decimal(Decimal("0")) == "0"
