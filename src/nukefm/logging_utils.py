from __future__ import annotations

import sys
from pathlib import Path

from loguru import logger


def configure_logging(log_path: Path) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logger.remove()
    logger.add(
        log_path,
        rotation="1 MB",
        retention=5,
        enqueue=True,
        backtrace=False,
        diagnose=False,
    )
    logger.add(
        sys.stdout,
        format="<level>{time:HH:mm:ss} {level.name:.1}</level> | {message}",
    )
