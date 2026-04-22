"""
risk_manager.py — Cálculo de niveles de riesgo y validación R:R para el bot ORB-NKD.

Calcula:
  - Stop Loss (con buffer desde el nivel ORB)
  - TP1 (1x rango ORB)
  - TP2 (1.5x rango ORB)
  - Relación Riesgo:Beneficio real
  - Validación de R:R mínimo antes de entrar
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import config
from logger_setup import get_logger
from orb_engine import Direccion, RangoORB

logger = get_logger()


@dataclass
class NivelesRiesgo:
    """Contiene todos los niveles calculados para una operación."""
    direccion:      Direccion
    precio_entrada: float
    stop_loss:      float
    tp1:            float
    tp2:            float
    rr_ratio:       float
    riesgo_pts:     float
    riesgo_usd:     float
    tp1_pts:        float
    tp2_pts:        float

    def log_resumen(self):
        dir_label = "LONG" if self.direccion == Direccion.LONG else "SHORT"
        logger.info(
            f"Orden enviada — Dirección: {dir_label} | "
            f"Entry: {self.precio_entrada:.0f} | "
            f"SL: {self.stop_loss:.0f} | "
            f"TP1: {self.tp1:.0f} | "
            f"TP2: {self.tp2:.0f}"
        )
        logger.info(
            f"Riesgo: {self.riesgo_pts:.0f} pts (${self.riesgo_usd:.2f}) | "
            f"TP1: +{self.tp1_pts:.0f} pts | "
            f"R:R efectivo: {self.rr_ratio:.2f}x"
        )


class RiskManager:
    """
    Calcula y valida los niveles de riesgo antes de cada operación.

    Fórmulas:
        LONG:
            SL  = precio_entrada - (precio_entrada - (orb_high - SL_BUFFER))
                = orb_high - SL_BUFFER  (fijo en el nivel ORB, no en la entrada)
            TP1 = precio_entrada + rango * TP1_MULTIPLIER
            TP2 = precio_entrada + rango * TP2_MULTIPLIER

        SHORT:
            SL  = orb_low + SL_BUFFER
            TP1 = precio_entrada - rango * TP1_MULTIPLIER
            TP2 = precio_entrada - rango * TP2_MULTIPLIER
    """

    def calcular(
        self,
        direccion: Direccion,
        precio_entrada: float,
        orb: RangoORB,
    ) -> Optional[NivelesRiesgo]:
        """
        Calcula SL/TP y valida el R:R.
        Retorna NivelesRiesgo si es operable, None si el R:R es insuficiente.
        """
        rango = orb.rango

        if direccion == Direccion.LONG:
            sl  = orb.high - config.SL_BUFFER
            tp1 = precio_entrada + rango * config.TP1_MULTIPLIER
            tp2 = precio_entrada + rango * config.TP2_MULTIPLIER
        else:  # SHORT
            sl  = orb.low + config.SL_BUFFER
            tp1 = precio_entrada - rango * config.TP1_MULTIPLIER
            tp2 = precio_entrada - rango * config.TP2_MULTIPLIER

        riesgo_pts = abs(precio_entrada - sl)
        tp1_pts    = abs(tp1 - precio_entrada)
        tp2_pts    = abs(tp2 - precio_entrada)

        if riesgo_pts <= 0:
            logger.warning(
                f"Riesgo calculado = 0 pts. "
                f"Entrada={precio_entrada:.0f} SL={sl:.0f} — operación cancelada."
            )
            return None

        rr_ratio   = tp1_pts / riesgo_pts
        riesgo_usd = riesgo_pts * config.POINT_VALUE * config.POSITION_SIZE

        if rr_ratio < config.MIN_RR_RATIO:
            logger.warning(
                f"R:R insuficiente: {rr_ratio:.2f}x < {config.MIN_RR_RATIO}x mínimo — "
                f"operación bloqueada. "
                f"(Entrada={precio_entrada:.0f} SL={sl:.0f} TP1={tp1:.0f})"
            )
            return None

        niveles = NivelesRiesgo(
            direccion=direccion,
            precio_entrada=precio_entrada,
            stop_loss=sl,
            tp1=tp1,
            tp2=tp2,
            rr_ratio=rr_ratio,
            riesgo_pts=riesgo_pts,
            riesgo_usd=riesgo_usd,
            tp1_pts=tp1_pts,
            tp2_pts=tp2_pts,
        )
        niveles.log_resumen()
        return niveles

    # ------------------------------------------------------------------
    # Monitoreo de posición abierta
    # ------------------------------------------------------------------

    def verificar_sl_tocado(self, precio: float, niveles: NivelesRiesgo) -> bool:
        """Retorna True si el precio actual alcanzó el Stop Loss."""
        if niveles.direccion == Direccion.LONG:
            return precio <= niveles.stop_loss
        else:
            return precio >= niveles.stop_loss

    def verificar_tp1_tocado(self, precio: float, niveles: NivelesRiesgo) -> bool:
        """Retorna True si el precio actual alcanzó TP1."""
        if niveles.direccion == Direccion.LONG:
            return precio >= niveles.tp1
        else:
            return precio <= niveles.tp1

    def verificar_tp2_tocado(self, precio: float, niveles: NivelesRiesgo) -> bool:
        """Retorna True si el precio actual alcanzó TP2."""
        if niveles.direccion == Direccion.LONG:
            return precio >= niveles.tp2
        else:
            return precio <= niveles.tp2

    def sl_a_breakeven(self, niveles: NivelesRiesgo) -> NivelesRiesgo:
        """
        Crea una copia de NivelesRiesgo con el SL movido al precio de entrada (breakeven).
        """
        from dataclasses import replace
        nuevo_sl = niveles.precio_entrada
        nuevo_riesgo = 0.0
        logger.info(f"SL movido a breakeven: {nuevo_sl:.0f}")
        return replace(
            niveles,
            stop_loss=nuevo_sl,
            riesgo_pts=nuevo_riesgo,
            riesgo_usd=0.0,
        )

    # ------------------------------------------------------------------
    # Cálculo de PnL
    # ------------------------------------------------------------------

    def calcular_pnl(
        self,
        direccion: Direccion,
        precio_entrada: float,
        precio_salida: float,
        contratos: int = None,
    ) -> tuple[float, float]:
        """
        Calcula PnL en puntos y USD para la posición cerrada.
        Retorna (resultado_pts, resultado_usd).
        """
        n = contratos if contratos is not None else config.POSITION_SIZE
        if direccion == Direccion.LONG:
            pts = precio_salida - precio_entrada
        else:
            pts = precio_entrada - precio_salida

        usd = pts * config.POINT_VALUE * n
        return pts, usd
