from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler

from app.config import Settings

LOGGER_NAME = "soul_mate"

_FORMAT = "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


def setup_logging(settings: Settings) -> logging.Logger:
    """
    初始化日志系统，返回根 logger。

    可重复调用：已存在的 handler 会被先清除，避免 --reload 时重复输出。
    """
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(settings.logging.level.upper())
    logger.propagate = False

    # 清除已有 handler，防止 reload 重复
    for handler in logger.handlers:
        logger.removeHandler(handler)
        handler.close()

    formatter = logging.Formatter(fmt=_FORMAT, datefmt=_DATE_FORMAT)

    # 控制台
    if settings.logging.console:
        console = logging.StreamHandler(sys.stdout)
        console.setFormatter(formatter)
        logger.addHandler(console)

    # 文件
    log_file = settings.paths.log_file
    log_file.parent.mkdir(parents=True, exist_ok=True)

    file_handler = RotatingFileHandler(
        log_file,
        maxBytes=settings.logging.max_bytes,
        backupCount=settings.logging.backup_count,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    return logger

def get_logger(name: str | None = None) -> logging.Logger:
    """获取 logger"""
    if not name:
        return logging.getLogger(LOGGER_NAME)

    if name.startswith(LOGGER_NAME + "."):
        return logging.getLogger(name)

    return logging.getLogger(f"{LOGGER_NAME}.{name}")
