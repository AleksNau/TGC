from __future__ import annotations

import os
from loguru import logger


def setup_logger(log_file: str) -> None:
    os.makedirs(os.path.dirname(log_file), exist_ok=True)
    logger.remove()
    logger.add(
        log_file,
        rotation="10 MB",
        retention="14 days",
        level="INFO",
        format="{time:YYYY-MM-DD HH:mm:ss} | {level} | {message}",
        enqueue=True,
        backtrace=False,
        diagnose=False,
    )
    logger.add(
        sink=lambda msg: print(msg, end=""),
        level="INFO",
        format="{time:HH:mm:ss} | {level} | {message}",
    )
