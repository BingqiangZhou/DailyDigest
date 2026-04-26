"""Centralized logging configuration for DailyDigest."""

import logging
import os
import sys


def get_logger(name: str) -> logging.Logger:
    """Get a configured logger with %(message)s formatter.

    Log level configurable via LOG_LEVEL environment variable (default: INFO).
    Set LOG_LEVEL=DEBUG for verbose output during development.
    """
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(handler)
    level_name = os.environ.get("LOG_LEVEL", "INFO").upper()
    logger.setLevel(getattr(logging, level_name, logging.INFO))
    return logger
