from nukefm.display import format_usd_table_display


def test_format_usd_table_display_can_round_to_whole_dollars() -> None:
    assert format_usd_table_display("1234.5", decimal_places=0) == "$1,235"
