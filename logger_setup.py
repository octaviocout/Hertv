"""
logger_setup.py — Configuración profesional de logging para el bot ORB-NKD.
Produce logs con timestamp en hora Argentina y rotación diaria automática.
"""
import logging
import os
from datetime import datetime
from logging.handlers import TimedRotatingFileHandler

import pytz

import config


class _ArgTimezoneFormatter(logging.Formatter):
    """Formatter que estampa el timestamp en hora Argentina (UTC-3)."""

    _tz = pytz.timezone("America/Argentina/Buenos_Aires")

    def formatTime(self, record, datefmt=None):
        ct = datetime.fromtimestamp(record.created, self._tz)
        fmt = datefmt if datefmt else "%H:%M:%S"
        return ct.strftime(fmt)


def setup_logger(name: str = "orb_nkd") -> logging.Logger:
    """
    Configura y retorna el logger principal del bot.

    - Archivo: logs/orb_YYYY-MM-DD.txt  (rotación a medianoche, 30 días de backup)
    - Consola: nivel INFO
    - Archivo:  nivel DEBUG
    """
    os.makedirs(config.LOG_DIR, exist_ok=True)

    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG)

    # Evitar handlers duplicados si setup_logger() se llama varias veces
    if logger.handlers:
        return logger

    fmt = "[%(asctime)s] [%(levelname)-8s] %(message)s"
    formatter = _ArgTimezoneFormatter(fmt=fmt, datefmt="%H:%M:%S")

    # ---- Handler de archivo (rotación diaria) ----
    fecha = datetime.now(config.TZ).strftime("%Y-%m-%d")
    log_path = os.path.join(config.LOG_DIR, f"orb_{fecha}.txt")

    file_handler = TimedRotatingFileHandler(
        filename=log_path,
        when="midnight",
        interval=1,
        backupCount=30,
        encoding="utf-8",
        atTime=None,
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(formatter)
    file_handler.suffix = "%Y-%m-%d"

    # ---- Handler de consola ----
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(formatter)

    logger.addHandler(file_handler)
    logger.addHandler(console_handler)

    logger.info(f"Logger inicializado — archivo: {log_path}")
    return logger


def get_logger(name: str = "orb_nkd") -> logging.Logger:
    """Retorna el logger ya configurado (o lo crea si no existe)."""
    logger = logging.getLogger(name)
    if not logger.handlers:
        return setup_logger(name)
    return logger
