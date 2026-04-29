"""
journal.py — Registro de trades en CSV y estado del bot en JSON.

Mantiene dos archivos de salida:
  - data/trades_journal.csv   (historial completo de operaciones)
  - data/estado_bot.json      (snapshot actualizado en cada barra)
"""
from __future__ import annotations

import csv
import json
import os
from datetime import datetime
from typing import Any, Dict, Optional

import config
from logger_setup import get_logger
from orb_engine import Direccion, RangoORB
from risk_manager import NivelesRiesgo
from trade_executor import EstadoPosicion, EstadoTrade

logger = get_logger()

# Columnas del CSV (orden fijo)
_COLUMNAS_CSV = [
    "fecha",
    "sesion_inicio",
    "direccion",
    "orb_high",
    "orb_low",
    "rango_pts",
    "bias_vela",
    "precio_entrada",
    "sl",
    "tp1",
    "tp2",
    "precio_salida",
    "resultado_pts",
    "resultado_usd",
    "toco_tp1",
    "toco_tp2",
    "motivo_salida",
    "r_r_real",
]


class Journal:
    """
    Registra eventos del bot en CSV y JSON.

    Uso:
        journal = Journal()
        journal.registrar_trade(trade_state, orb, sesion_inicio)
        journal.actualizar_estado(snapshot)
    """

    def __init__(self):
        os.makedirs(config.DATA_DIR, exist_ok=True)
        self._inicializar_csv()

    # ------------------------------------------------------------------
    # CSV
    # ------------------------------------------------------------------

    def _inicializar_csv(self):
        """Crea el CSV con encabezados si no existe."""
        if not os.path.exists(config.JOURNAL_FILE):
            try:
                with open(config.JOURNAL_FILE, "w", newline="", encoding="utf-8") as f:
                    writer = csv.DictWriter(f, fieldnames=_COLUMNAS_CSV)
                    writer.writeheader()
                logger.info(f"Journal CSV creado: {config.JOURNAL_FILE}")
            except Exception as exc:
                logger.error(f"No se pudo crear el journal CSV: {exc}")

    def registrar_trade(
        self,
        ts: EstadoTrade,
        orb: RangoORB,
        sesion_inicio: datetime,
    ):
        """
        Escribe una fila en el journal CSV con el resultado de la operación cerrada.
        Llamar solo cuando el trade esté CERRADO.
        """
        if ts.estado != EstadoPosicion.CERRADA:
            logger.warning("registrar_trade llamado con posición no cerrada. Ignorado.")
            return

        pnl_pts = ts.pnl_realizado_pts
        pnl_usd = ts.pnl_realizado_usd
        rr_real = (
            round(abs(pnl_pts) / ts.niveles.riesgo_pts, 2)
            if ts.niveles and ts.niveles.riesgo_pts > 0 else 0.0
        )

        # Precio de salida: aproximar desde el PnL realizado
        if ts.niveles and ts.precio_entrada:
            if ts.direccion == Direccion.LONG:
                precio_salida = ts.precio_entrada + pnl_pts
            else:
                precio_salida = ts.precio_entrada - pnl_pts
        else:
            precio_salida = 0.0

        fila = {
            "fecha":          sesion_inicio.strftime("%Y-%m-%d"),
            "sesion_inicio":  sesion_inicio.strftime("%Y-%m-%d %H:%M:%S"),
            "direccion":      "LONG" if ts.direccion == Direccion.LONG else "SHORT",
            "orb_high":       f"{orb.high:.0f}",
            "orb_low":        f"{orb.low:.0f}",
            "rango_pts":      f"{orb.rango:.0f}",
            "bias_vela":      "ALCISTA" if orb.bias == Direccion.LONG else "BAJISTA",
            "precio_entrada": f"{ts.precio_entrada:.0f}",
            "sl":             f"{ts.niveles.stop_loss:.0f}" if ts.niveles else "",
            "tp1":            f"{ts.niveles.tp1:.0f}"       if ts.niveles else "",
            "tp2":            f"{ts.niveles.tp2:.0f}"       if ts.niveles else "",
            "precio_salida":  f"{precio_salida:.0f}",
            "resultado_pts":  f"{pnl_pts:.0f}",
            "resultado_usd":  f"{pnl_usd:.2f}",
            "toco_tp1":       "SI" if ts.tp1_tocado else "NO",
            "toco_tp2":       "SI" if ts.tp2_tocado else "NO",
            "motivo_salida":  ts.motivo_salida,
            "r_r_real":       f"{rr_real:.2f}",
        }

        try:
            with open(config.JOURNAL_FILE, "a", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=_COLUMNAS_CSV)
                writer.writerow(fila)
            signo = "+" if pnl_usd >= 0 else ""
            logger.info(
                f"Trade registrado en journal — "
                f"Resultado: {signo}{pnl_pts:.0f} pts ({signo}${pnl_usd:.2f}) | "
                f"R:R real: {rr_real:.2f}x | "
                f"Motivo: {ts.motivo_salida}"
            )
        except Exception as exc:
            logger.error(f"Error al escribir en journal CSV: {exc}")

    # ------------------------------------------------------------------
    # JSON de estado
    # ------------------------------------------------------------------

    def actualizar_estado(
        self,
        estado_bot: str,
        orb: Optional[RangoORB],
        trade_state: EstadoTrade,
        pnl_sesion: float,
    ):
        """
        Escribe el snapshot actual del bot en estado_bot.json.
        Llamar en cada nueva barra o cambio de estado.
        """
        ahora = datetime.now(config.TZ)

        snapshot: Dict[str, Any] = {
            "timestamp":        ahora.strftime("%Y-%m-%d %H:%M:%S %Z"),
            "estado":           estado_bot,
            "posicion_abierta": trade_state.estado.name,
            "orb_high":         orb.high   if orb else None,
            "orb_low":          orb.low    if orb else None,
            "orb_rango":        orb.rango  if orb else None,
            "bias":             (
                "ALCISTA" if orb and orb.bias == Direccion.LONG
                else ("BAJISTA" if orb else None)
            ),
            "precio_entrada":   trade_state.precio_entrada if trade_state.precio_entrada else None,
            "sl":               trade_state.niveles.stop_loss if trade_state.niveles else None,
            "tp1":              trade_state.niveles.tp1       if trade_state.niveles else None,
            "tp2":              trade_state.niveles.tp2       if trade_state.niveles else None,
            "tp1_tocado":       trade_state.tp1_tocado,
            "pnl_sesion_usd":   round(pnl_sesion, 2),
            "trade_done":       trade_state.estado == EstadoPosicion.CERRADA,
        }

        try:
            with open(config.STATE_FILE, "w", encoding="utf-8") as f:
                json.dump(snapshot, f, indent=2, ensure_ascii=False)
        except Exception as exc:
            logger.error(f"Error al actualizar estado JSON: {exc}")
