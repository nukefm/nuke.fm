# Color Predicted Nuke Values By Sign

## Goal

Make positive and negative predicted nuke percentages visually distinct without changing their numeric meaning or API representation.

## Implementation Plan

- Add a small display helper that maps `predicted_nuke_fraction` to a sign class: positive, negative, or neutral.
- Base the sign class on the raw serialized fraction, not on formatted percent text.
- Apply the sign class to predicted-nuke value elements on the main board and token detail page only when the value exists.
- Leave pending/missing predicted nuke values uncolored.
- Style positive predicted nuke values with the warning/risk side of the existing palette because they imply downside.
- Style negative predicted nuke values with the constructive/upside side of the existing palette because they imply upside.
- Do not change public API numeric fields, percent formatting, sorting, or fallback behavior.

## Assumptions

- Positive predicted nuke percent means implied downside/risk and should read visually bearish.
- Negative predicted nuke percent means implied upside and should read visually bullish.

## Validation

- Add or update app-level tests proving positive and negative nuke values render with distinct sign classes.
- Run targeted app tests with `uv run --env-file .env pytest tests/test_app.py`.
- Run the full suite with `uv run --env-file .env pytest`.
