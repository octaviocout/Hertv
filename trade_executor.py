"""
trade_executor.py — Envío de órdenes a IBKR y gestión del ciclo de vida de la posición.

Gestiona:
  - Entrada con orden a mercado
  - Cierre parcial al TP1 (50%) y SL a breakeven
  - Cierre total al TP2 o al SL
  - Cierre forzado por horario (23:30 ARG)
  - Monitoreo de la posición via precios de mercado en tiempo real
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Optional

from ib_insync import IB, Future, MarketOrder, LimitOrder, Order, Trade

import config
from logger_setup import get_logger
from orb_engine import Direccion
from risk_manager import NivelesRiesgo, RiskManager

logger = get_logger()


class EstadoPosicion(Enum):
    SIN_POSICION  = auto()
    ABIERTA       = auto()    # 1 contrato completo
    PARCIAL       = auto()    # 50% cerrado, 50% corriendo
    CERRADA       = auto()


@dataclass
class EstadoTrade:
    """Snapshot mutable del trade en curso."""
    estado:          EstadoPosicion = EstadoPosicion.SIN_POSICION
    direccion:       Optional[Direccion] = None
    niveles:         Optional[NivelesRiesgo] = None
    contratos_abiertos: int = 0
    precio_entrada:  float = 0.0
    tp1_tocado:      bool = False
    tp2_tocado:      bool = False
    pnl_realizado_pts: float = 0.0
    pnl_realizado_usd: float = 0.0
    motivo_salida:   str = ""


class TradeExecutor:
    """
    Envía órdenes a IBKR y gestiona el ciclo completo de la posición.

    Usa exclusivamente órdenes a mercado (MKT) para garantizar ejecución.
    En paper trading, las órdenes se ejecutan inmediatamente.
    """

    def __init__(self, ib: IB, contrato: Future):
        self.ib = ib
        self.contrato = contrato
        self._risk = RiskManager()
        self.trade_state = EstadoTrade()

    # ------------------------------------------------------------------
    # Entrada
    # ------------------------------------------------------------------

    def abrir_posicion(
        self,
        direccion: Direccion,
        precio_ref: float,
        niveles: NivelesRiesgo,
    ) -> bool:
        """
        Envía una orden de entrada a mercado.
        Retorna True si la orden fue enviada y confirmada.
        """
        if self.trade_state.estado != EstadoPosicion.SIN_POSICION:
            logger.warning("Intento de abrir posición cuando ya hay una activa. Ignorado.")
            return False

        accion = "BUY" if direccion == Direccion.LONG else "SELL"
        orden  = MarketOrder(accion, config.POSITION_SIZE)
        orden.transmit = True

        logger.info(
            f"Enviando orden entrada {accion} {config.POSITION_SIZE} contrato(s) "
            f"{self.contrato.localSymbol} @ mercado (ref: {precio_ref:.0f})"
        )

        try:
            trade: Trade = self.ib.placeOrder(self.contrato, orden)
            self.ib.sleep(1)   # Dar tiempo a TWS para procesar

            precio_llenado = self._precio_llenado(trade, precio_ref)
            self.trade_state = EstadoTrade(
                estado=EstadoPosicion.ABIERTA,
                direccion=direccion,
                niveles=niveles,
                contratos_abiertos=config.POSITION_SIZE,
                precio_entrada=precio_llenado,
            )

            logger.info(
                f"Posición ABIERTA — {accion} {config.POSITION_SIZE} @ {precio_llenado:.0f} | "
                f"SL: {niveles.stop_loss:.0f} | "
                f"TP1: {niveles.tp1:.0f} | "
                f"TP2: {niveles.tp2:.0f}"
            )
            return True

        except Exception as exc:
            logger.error(f"Error al enviar orden de entrada: {exc}")
            return False

    # ------------------------------------------------------------------
    # Monitoreo y gestión de TP/SL
    # ------------------------------------------------------------------

    def monitorear_posicion(self, precio_actual: float) -> bool:
        """
        Evalúa el precio actual contra SL/TP1/TP2.
        Ejecuta cierres parciales o totales según corresponda.
        Retorna True si la posición fue cerrada (parcial o total).
        """
        ts = self.trade_state
        if ts.estado not in (EstadoPosicion.ABIERTA, EstadoPosicion.PARCIAL):
            return False

        niveles = ts.niveles

        # ---- SL ----
        if self._risk.verificar_sl_tocado(precio_actual, niveles):
            logger.info(f"Stop Loss alcanzado en {precio_actual:.0f} — cerrando posición completa.")
            self._cerrar_todo(precio_actual, "stop_loss")
            return True

        # ---- TP1 ----
        if not ts.tp1_tocado and self._risk.verificar_tp1_tocado(precio_actual, niveles):
            logger.info(
                f"TP1 alcanzado en {precio_actual:.0f} — "
                f"cerrando 50% de la posición, moviendo SL a breakeven."
            )
            self._cerrar_parcial_tp1(precio_actual)
            return False   # Posición sigue abierta (50% restante)

        # ---- TP2 ----
        if ts.tp1_tocado and self._risk.verificar_tp2_tocado(precio_actual, niveles):
            logger.info(f"TP2 alcanzado en {precio_actual:.0f} — cerrando posición restante.")
            self._cerrar_todo(precio_actual, "tp2")
            return True

        return False

    def _cerrar_parcial_tp1(self, precio: float):
        """Cierra 50% de la posición al tocar TP1 y mueve SL a breakeven."""
        ts = self.trade_state
        contratos_a_cerrar = max(1, ts.contratos_abiertos // 2)
        accion = "SELL" if ts.direccion == Direccion.LONG else "BUY"

        try:
            orden = MarketOrder(accion, contratos_a_cerrar)
            orden.transmit = True
            trade = self.ib.placeOrder(self.contrato, orden)
            self.ib.sleep(1)

            precio_llenado = self._precio_llenado(trade, precio)
            pts, usd = self._risk.calcular_pnl(
                ts.direccion, ts.precio_entrada, precio_llenado, contratos_a_cerrar
            )
            ts.pnl_realizado_pts += pts
            ts.pnl_realizado_usd += usd
            ts.contratos_abiertos -= contratos_a_cerrar
            ts.tp1_tocado = True
            ts.estado = EstadoPosicion.PARCIAL

            # Mover SL a breakeven en los niveles para el monitoreo futuro
            ts.niveles = self._risk.sl_a_breakeven(ts.niveles)

            logger.info(
                f"TP1: cerrado {contratos_a_cerrar} contrato(s) @ {precio_llenado:.0f} | "
                f"PnL parcial: +{pts:.0f} pts (${usd:.2f}) | "
                f"SL movido a breakeven ({ts.precio_entrada:.0f})"
            )

        except Exception as exc:
            logger.error(f"Error al cerrar parcial en TP1: {exc}")

    def _cerrar_todo(self, precio: float, motivo: str):
        """Cierra todos los contratos restantes a mercado."""
        ts = self.trade_state
        if ts.contratos_abiertos <= 0:
            ts.estado = EstadoPosicion.CERRADA
            return

        accion = "SELL" if ts.direccion == Direccion.LONG else "BUY"
        try:
            orden = MarketOrder(accion, ts.contratos_abiertos)
            orden.transmit = True
            trade = self.ib.placeOrder(self.contrato, orden)
            self.ib.sleep(1)

            precio_llenado = self._precio_llenado(trade, precio)
            pts, usd = self._risk.calcular_pnl(
                ts.direccion, ts.precio_entrada, precio_llenado, ts.contratos_abiertos
            )
            ts.pnl_realizado_pts += pts
            ts.pnl_realizado_usd += usd
            ts.contratos_abiertos = 0
            ts.estado = EstadoPosicion.CERRADA
            ts.tp2_tocado = (motivo == "tp2")
            ts.motivo_salida = motivo

            pnl_total_pts = ts.pnl_realizado_pts
            pnl_total_usd = ts.pnl_realizado_usd
            signo = "+" if pnl_total_usd >= 0 else ""
            logger.info(
                f"Posición CERRADA [{motivo.upper()}] @ {precio_llenado:.0f} | "
                f"PnL total: {signo}{pnl_total_pts:.0f} pts ({signo}${pnl_total_usd:.2f})"
            )

        except Exception as exc:
            logger.error(f"Error al cerrar posición [{motivo}]: {exc}")

    # ------------------------------------------------------------------
    # Cierre forzado por horario
    # ------------------------------------------------------------------

    def cerrar_por_horario(self, precio_actual: float):
        """
        Cierra toda posición abierta por llegada del horario de cierre forzado (23:30 ARG).
        """
        ts = self.trade_state
        if ts.estado not in (EstadoPosicion.ABIERTA, EstadoPosicion.PARCIAL):
            return
        logger.info(f"Cierre forzado por horario (23:30 ARG) @ precio ~{precio_actual:.0f}")
        self._cerrar_todo(precio_actual, "cierre_horario")

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _precio_llenado(self, trade: Trade, precio_ref: float) -> float:
        """
        Obtiene el precio de llenado real de la orden.
        Si no está disponible (paper/delay), usa el precio de referencia.
        """
        try:
            fills = trade.fills
            if fills:
                return fills[-1].execution.avgPrice
        except Exception:
            pass
        logger.debug(f"Precio de llenado no disponible — usando precio referencia: {precio_ref:.0f}")
        return precio_ref

    def tiene_posicion_abierta(self) -> bool:
        return self.trade_state.estado in (EstadoPosicion.ABIERTA, EstadoPosicion.PARCIAL)

    def resetear(self):
        """Resetea el estado del executor para una nueva sesión."""
        self.trade_state = EstadoTrade()
