"""
main.py — Punto de entrada del bot ORB-NKD para Interactive Brokers.

Loop principal:
  1. Conectar a TWS (paper account, puerto 7497)
  2. Esperar a las 20:50 ARG
  3. A las 21:00 calcular el rango ORB desde barras históricas
  4. Suscribir barras RT y procesar señales de entrada
  5. Gestionar la posición (TP1, TP2, SL, cierre forzado 23:30)
  6. Registrar en journal y resetear para el día siguiente

Prerequisito: TWS o Gateway deben estar abiertos antes de ejecutar.
"""
from __future__ import annotations

import signal
import sys
import time
import traceback
from datetime import datetime, timedelta
from typing import Optional

from ib_insync import IB

import config
from data_feed import DataFeed
from ibkr_connector import IBKRConnector
from journal import Journal
from logger_setup import setup_logger
from orb_engine import Direccion, EstadoSesion, ORBEngine, Señal
from risk_manager import NivelesRiesgo, RiskManager
from trade_executor import EstadoPosicion, TradeExecutor

logger = setup_logger()


# ---------------------------------------------------------------------------
# Shutdown limpio con Ctrl+C
# ---------------------------------------------------------------------------

_shutdown_flag = False

def _señal_shutdown(sig, frame):
    global _shutdown_flag
    logger.info("Señal de cierre recibida (Ctrl+C) — cerrando bot limpiamente...")
    _shutdown_flag = True

signal.signal(signal.SIGINT, _señal_shutdown)
signal.signal(signal.SIGTERM, _señal_shutdown)


# ---------------------------------------------------------------------------
# Helpers de tiempo
# ---------------------------------------------------------------------------

def _hora_arg(h: str) -> datetime:
    """Construye un datetime 'hoy' a la hora indicada en formato HH:MM en timezone ARG."""
    hoy = datetime.now(config.TZ)
    hh, mm = map(int, h.split(":"))
    return hoy.replace(hour=hh, minute=mm, second=0, microsecond=0)


def _ahora_arg() -> datetime:
    return datetime.now(config.TZ)


def _esperar_hasta(objetivo: datetime, descripcion: str, ib: IB):
    """Duerme hasta el datetime objetivo, procesando el event loop de ib_insync."""
    ahora = _ahora_arg()
    if ahora >= objetivo:
        return
    segundos = (objetivo - ahora).total_seconds()
    logger.info(
        f"Esperando hasta {objetivo.strftime('%H:%M')} ARG para {descripcion} "
        f"({segundos/60:.1f} min)"
    )
    while not _shutdown_flag:
        ahora = _ahora_arg()
        if ahora >= objetivo:
            break
        restante = (objetivo - ahora).total_seconds()
        dormir = min(30.0, restante)
        ib.sleep(dormir)


# ---------------------------------------------------------------------------
# Sesión de trading
# ---------------------------------------------------------------------------

class SesionORB:
    """
    Encapsula una sesión completa de trading: desde el ORB hasta el cierre forzado.
    """

    def __init__(self, connector: IBKRConnector):
        self.connector  = connector
        self.ib         = connector.ib
        self.contrato   = connector.contract
        self.orb_engine = ORBEngine()
        self.risk_mgr   = RiskManager()
        self.executor   = TradeExecutor(self.ib, self.contrato)
        self.feed       = DataFeed(self.ib, self.contrato)
        self.journal    = Journal()
        self.sesion_inicio = _ahora_arg()
        self._nueva_vela_buffer: Optional[dict] = None   # última vela completada por DataFeed

    # ------------------------------------------------------------------
    # Flujo principal de sesión
    # ------------------------------------------------------------------

    def ejecutar(self):
        """Ciclo completo de una sesión de trading."""
        logger.info("=" * 60)
        logger.info(f"NUEVA SESIÓN ORB-NKD — {self.sesion_inicio.strftime('%Y-%m-%d')}")
        logger.info("=" * 60)
        self.sesion_inicio = _ahora_arg()

        # 1. Esperar inicio del ORB
        _esperar_hasta(_hora_arg(config.HORA_ORB_START), "inicio ventana ORB", self.ib)
        if _shutdown_flag:
            return

        # 2. Calcular ORB desde barras históricas
        if not self._calcular_orb():
            logger.warning("ORB inválido — sesión cancelada para hoy.")
            return

        # 3. Suscribir barras RT y esperar señal de entrada
        self.feed.suscribir_barras_rt(self._callback_nueva_vela)
        logger.info(f"Ventana de entrada: {config.HORA_ENTRY_START} – {config.HORA_ENTRY_END} ARG")

        # 4. Loop de monitoreo hasta cierre forzado
        self._loop_monitoreo()

        # 5. Cierre forzado si hay posición abierta
        self._cierre_forzado()

        # 6. Registrar trade en journal si hubo operación
        self._registrar_si_hubo_trade()

        # 7. Cancelar suscripción RT
        self.feed.cancelar_suscripcion()

        logger.info(f"Sesión finalizada — PnL: ${self.executor.trade_state.pnl_realizado_usd:.2f}")
        logger.info("=" * 60)

    # ------------------------------------------------------------------
    # ORB
    # ------------------------------------------------------------------

    def _calcular_orb(self) -> bool:
        """Solicita barras históricas y calcula el rango ORB."""
        orb_start = _hora_arg(config.HORA_ORB_START)
        orb_end   = _hora_arg(config.HORA_ORB_END)

        # Esperar a que cierre la ventana ORB antes de calcular
        _esperar_hasta(orb_end, "cierre de ventana ORB", self.ib)
        if _shutdown_flag:
            return False

        try:
            barras = self.feed.obtener_barras_historicas(orb_start, orb_end)
            orb = self.orb_engine.calcular_orb(barras)
            return orb is not None
        except Exception as exc:
            logger.error(f"Error al calcular ORB: {exc}\n{traceback.format_exc()}")
            return False

    # ------------------------------------------------------------------
    # Callback de nueva vela RT
    # ------------------------------------------------------------------

    def _callback_nueva_vela(self, barra: dict):
        """Llamado por DataFeed cuando se completa una barra de 15 min."""
        ts_vela = barra["ts"]
        ahora   = _ahora_arg()

        # Validar ventana de entrada
        entry_start = _hora_arg(config.HORA_ENTRY_START)
        entry_end   = _hora_arg(config.HORA_ENTRY_END)

        if ahora < entry_start or ahora > entry_end:
            logger.debug(f"Vela {ts_vela.strftime('%H:%M')} fuera de ventana de entrada — ignorada.")
            return

        if self.orb_engine.trade_done:
            return

        # Procesar en el ORB engine
        try:
            señal: Optional[Señal] = self.orb_engine.procesar_vela(barra)
        except Exception as exc:
            logger.error(f"Error en ORB engine al procesar vela: {exc}")
            return

        if señal is None:
            return

        # Calcular niveles de riesgo
        orb = self.orb_engine.orb
        niveles: Optional[NivelesRiesgo] = self.risk_mgr.calcular(
            señal.direccion, señal.precio_ref, orb
        )
        if niveles is None:
            logger.warning("Señal cancelada por R:R insuficiente.")
            self.orb_engine.marcar_trade_completo()
            return

        # Abrir posición
        exito = self.executor.abrir_posicion(señal.direccion, señal.precio_ref, niveles)
        if exito:
            self.orb_engine.marcar_trade_completo()
            self._actualizar_estado_json("EN_TRADE")
        else:
            logger.error("Fallo al abrir posición — sesión finalizada.")
            self.orb_engine.marcar_trade_completo()

    # ------------------------------------------------------------------
    # Loop de monitoreo
    # ------------------------------------------------------------------

    def _loop_monitoreo(self):
        """
        Loop principal que monitorea SL/TP mientras hay posición abierta
        y la sesión no ha terminado.
        """
        hora_cierre = _hora_arg(config.HORA_CLOSE_ALL)
        hora_fin_entrada = _hora_arg(config.HORA_ENTRY_END)

        while not _shutdown_flag:
            ahora = _ahora_arg()

            # Salir si llegó hora de cierre forzado
            if ahora >= hora_cierre:
                break

            # Si no hay posición y ya pasó la ventana de entrada, terminar
            if (
                not self.executor.tiene_posicion_abierta()
                and ahora >= hora_fin_entrada
                and self.orb_engine.trade_done
            ):
                logger.info("Sesión sin trade abierto y ventana de entrada cerrada — fin de sesión.")
                break

            # Monitorear posición abierta
            if self.executor.tiene_posicion_abierta():
                precio = self.feed.precio_actual()
                if precio:
                    cerrado = self.executor.monitorear_posicion(precio)
                    self._actualizar_estado_json(
                        "CERRADA" if cerrado else "EN_TRADE"
                    )
                    if cerrado:
                        break

            self.ib.sleep(10)   # Verificar cada 10 segundos

    # ------------------------------------------------------------------
    # Cierre forzado
    # ------------------------------------------------------------------

    def _cierre_forzado(self):
        """Cierra posición abierta si el horario de 23:30 fue alcanzado."""
        if self.executor.tiene_posicion_abierta():
            precio = self.feed.precio_actual() or 0.0
            self.executor.cerrar_por_horario(precio)
            self._actualizar_estado_json("CERRADA")

    # ------------------------------------------------------------------
    # Journal
    # ------------------------------------------------------------------

    def _registrar_si_hubo_trade(self):
        ts = self.executor.trade_state
        orb = self.orb_engine.orb
        if ts.estado == EstadoPosicion.CERRADA and orb is not None:
            self.journal.registrar_trade(ts, orb, self.sesion_inicio)

    # ------------------------------------------------------------------
    # Estado JSON
    # ------------------------------------------------------------------

    def _actualizar_estado_json(self, estado: str):
        orb = self.orb_engine.orb
        ts  = self.executor.trade_state
        pnl = ts.pnl_realizado_usd
        try:
            self.journal.actualizar_estado(estado, orb, ts, pnl)
        except Exception as exc:
            logger.warning(f"Error al actualizar estado JSON: {exc}")


# ---------------------------------------------------------------------------
# Loop principal del bot
# ---------------------------------------------------------------------------

def main():
    logger.info("=" * 60)
    logger.info("BOT ORB-NKD v1.0 — ARRANCANDO")
    logger.info(f"Instrumento: {config.SYMBOL} @ {config.EXCHANGE}")
    logger.info(f"Cuenta PAPER — Puerto {config.IBKR_PORT}")
    logger.info("=" * 60)

    # Verificar que TWS esté disponible
    logger.info("Prerequisito: TWS debe estar abierto en el puerto configurado.")

    connector = IBKRConnector()

    if not connector.connect():
        logger.critical("No se pudo conectar a TWS. Verificar que TWS esté abierto y configurado.")
        sys.exit(1)

    ib = connector.ib

    try:
        while not _shutdown_flag:
            ahora = _ahora_arg()

            # Calcular próximo wakeup (20:50 ARG)
            wakeup = _hora_arg(config.HORA_WAKEUP)
            if ahora >= wakeup:
                # Si ya pasó el wakeup de hoy, programar para mañana
                wakeup += timedelta(days=1)

            # Esperar al wakeup
            _esperar_hasta(wakeup, "wakeup pre-sesión", ib)
            if _shutdown_flag:
                break

            logger.info(f"Wakeup — preparando sesión del {_ahora_arg().strftime('%Y-%m-%d')}")

            # Verificar conexión antes de la sesión
            if not connector.is_connected():
                logger.warning("Conexión perdida antes de iniciar sesión — reconectando...")
                if not connector.connect():
                    logger.error("No se pudo reconectar. Esperando al próximo wakeup.")
                    ib.sleep(300)
                    continue

            # Ejecutar sesión completa
            try:
                sesion = SesionORB(connector)
                sesion.ejecutar()
            except Exception as exc:
                logger.error(f"Excepción en sesión de trading:\n{traceback.format_exc()}")

            # Pausa antes de buscar el próximo wakeup (evitar bucle tight)
            if not _shutdown_flag:
                logger.info("Sesión completada — bot en espera hasta próximo wakeup.")
                ib.sleep(60)

    except Exception as exc:
        logger.critical(f"Error fatal en loop principal:\n{traceback.format_exc()}")
    finally:
        connector.disconnect()
        logger.info("Bot ORB-NKD detenido.")


if __name__ == "__main__":
    main()
