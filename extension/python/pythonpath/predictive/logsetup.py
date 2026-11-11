"""Logging configuration (plain Python, no ``uno`` import).

Logs go to ``<user profile>/predictive-writer/log.txt``. The UNO layer
resolves the profile directory and passes it to :func:`setup_logging`.
"""

import logging
import logging.handlers
import os

LOGGER_NAME = "predictive"
_HANDLER_TAG = "_predictive_writer_handler"


def setup_logging(data_dir: str, level: int = logging.DEBUG) -> logging.Logger:
    """Configure the package logger to write to ``data_dir/log.txt``.

    Safe to call several times: the file handler is only added once.

    Args:
        data_dir: Directory for extension data; created if missing.
        level: Logging level for the package logger.

    Returns:
        The package logger.
    """
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(level)
    logger.propagate = False
    if any(getattr(h, _HANDLER_TAG, False) for h in logger.handlers):
        return logger
    os.makedirs(data_dir, exist_ok=True)
    handler = logging.handlers.RotatingFileHandler(
        os.path.join(data_dir, "log.txt"),
        maxBytes=1_000_000,
        backupCount=2,
        encoding="utf-8",
    )
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    )
    setattr(handler, _HANDLER_TAG, True)
    logger.addHandler(handler)
    return logger
