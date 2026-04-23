# Color Predicted Nuke Values By Sign

## Status

Complete. Implemented in commit `f3d7da4`.

## Goal

Make positive and negative predicted nuke percentages visually distinct without changing their numeric meaning or API representation.

## Completed Work

- Added a display helper that maps `predicted_nuke_fraction` to a sign class: positive, negative, or neutral.
- Based the sign class on the raw serialized fraction, not on formatted percent text.
- Applied the sign class to predicted-nuke value elements on the main board and token detail page only when the value exists.
- Left pending/missing predicted nuke values uncolored.
- Styled positive predicted nuke values with the warning/risk side of the existing palette because they imply downside.
- Styled negative predicted nuke values with the constructive/upside side of the existing palette because they imply upside.
- Preserved public API numeric fields, percent formatting, sorting, and missing-value behavior.

## Assumptions

- Positive predicted nuke percent means implied downside/risk and should read visually bearish.
- Negative predicted nuke percent means implied upside and should read visually bullish.

## Validation

- `uv run --env-file /home/pimania/dev/nukefm/.env pytest tests/test_app.py` passed in the implementation worktree.
- `uv run --env-file /home/pimania/dev/nukefm/.env pytest` passed in the implementation worktree.
