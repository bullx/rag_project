"""
Central logging configuration — writes to rotating log file, optional console.
"""

import logging
import os
import shutil
from logging.handlers import RotatingFileHandler
from pathlib import Path


def setup_logging() -> Path:
    """Configure app-wide logging. Returns the log file path."""
    log_dir = Path(os.getenv("LOG_DIR", "./local_data/logs"))
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "rag.log"

    level_name = os.getenv("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    log_to_console = os.getenv("LOG_TO_CONSOLE", "false").lower() == "true"

    formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    root = logging.getLogger()
    root.setLevel(level)

    # Avoid duplicate handlers on reload
    if not any(isinstance(h, RotatingFileHandler) for h in root.handlers):
        file_handler = RotatingFileHandler(
            log_file,
            maxBytes=10 * 1024 * 1024,
            backupCount=5,
            encoding="utf-8",
        )
        file_handler.setFormatter(formatter)
        file_handler.setLevel(level)
        root.addHandler(file_handler)

    if log_to_console and not any(
        isinstance(h, logging.StreamHandler) and not isinstance(h, RotatingFileHandler)
        for h in root.handlers
    ):
        console = logging.StreamHandler()
        console.setFormatter(formatter)
        console.setLevel(level)
        root.addHandler(console)

    # Quiet noisy third-party loggers
    for name in ("httpx", "httpcore", "urllib3", "chromadb", "sentence_transformers"):
        logging.getLogger(name).setLevel(logging.WARNING)

    logging.getLogger(__name__).info("Logging initialized → %s", log_file)
    return log_file


def check_tesseract() -> bool:
    """Return True if tesseract is available on PATH."""
    return shutil.which("tesseract") is not None
