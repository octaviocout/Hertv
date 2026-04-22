"""
data_feed.py — Obtención de barras históricas y suscripción a barras en tiempo real.

Dos fuentes de datos:
  1. Históricas: reqHistoricalData() — para construir el ORB al inicio de sesión.
  2. Tiempo real: reqRealTimeBars() (barras de 5s) agregadas manualmente a 15 min.
"""
from __future__ import annotations

import threading
from datetime import datetime, timezone
from typing import Callable, List, Optional

import pandas as pd
from ib_insync import IB, BarData, Future, RealTimeBar, RealTimeBarList

import config
from logger_setup import get_logger

logger = get_logger()


class BarraAgregada:
    """Barra OHLCV parcial construida a partir de barras de 5 segundos."""

    __slots__ = ("open", "high", "low", "close", "volume", "ts_open", "ts_close", "completa")

    def __init__(self):
        self.open: Optional[float] = None
        self.high: Optional[float] = None
        self.low: Optional[float] = None
        self.close: Optional[float] = None
        self.volume: float = 0.0
        self.ts_open: Optional[datetime] = None
        self.ts_close: Optional[datetime] = None
        self.completa: bool = False

    def actualizar(self, barra_5s: RealTimeBar):
        precio = barra_5s.close
        if self.open is None:
            self.open = barra_5s.open
            self.high = barra_5s.high
            self.low = barra_5s.low
            self.ts_open = datetime.fromtimestamp(barra_5s.time, tz=timezone.utc)
        else:
            self.high = max(self.high, barra_5s.high)
            self.low = min(self.low, barra_5s.low)
        self.close = precio
        self.volume += barra_5s.volume
        self.ts_close = datetime.fromtimestamp(barra_5s.time, tz=timezone.utc)

    def a_dict(self) -> dict:
        return {
            "ts":     self.ts_open.astimezone(config.TZ),
            "open":   self.open,
            "high":   self.high,
            "low":    self.low,
            "close":  self.close,
            "volume": self.volume,
        }


# Cuántas barras de 5s caben en 15 minutos
_BARRAS_5S_POR_15MIN = (15 * 60) // config.RT_BAR_SIZE   # 180


class DataFeed:
    """
    Gestiona la obtención de datos de mercado para el bot ORB-NKD.

    Uso típico:
        feed = DataFeed(ib, contrato)
        barras_hist = feed.obtener_barras_historicas(desde_arg, hasta_arg)
        feed.suscribir_barras_rt(callback_nueva_vela_15m)
        ...
        feed.cancelar_suscripcion()
    """

    def __init__(self, ib: IB, contrato: Future):
        self.ib = ib
        self.contrato = contrato
        self._rt_bars: Optional[RealTimeBarList] = None
        self._barra_actual = BarraAgregada()
        self._contador_barras_5s: int = 0
        self._lock = threading.Lock()
        self._callback_nueva_vela: Optional[Callable[[dict], None]] = None

    # ------------------------------------------------------------------
    # Barras históricas
    # ------------------------------------------------------------------

    def obtener_barras_historicas(
        self,
        desde_arg: datetime,
        hasta_arg: datetime,
    ) -> pd.DataFrame:
        """
        Solicita barras históricas de 15 min a IBKR entre las horas indicadas.
        Retorna un DataFrame con columnas: ts, open, high, low, close, volume.
        """
        if not self.ib.isConnected():
            raise ConnectionError("No hay conexión activa con TWS.")

        logger.info(
            f"Solicitando barras históricas {config.HIST_BAR_SIZE} para {self.contrato.localSymbol}..."
        )
        try:
            barras_raw: List[BarData] = self.ib.reqHistoricalData(
                contract=self.contrato,
                endDateTime="",           # vacío = hasta ahora
                durationStr=config.HIST_DURATION,
                barSizeSetting=config.HIST_BAR_SIZE,
                whatToShow=config.HIST_WHAT_TO_SHOW,
                useRTH=False,             # incluir fuera de RTH (NKD es nocturno para ARG)
                formatDate=2,             # timestamps en epoch UTC
                keepUpToDate=False,
            )
        except Exception as exc:
            logger.error(f"Error al obtener barras históricas: {exc}")
            raise

        if not barras_raw:
            raise ValueError("IBKR no retornó barras históricas. Verificar contrato y conexión.")

        df = pd.DataFrame([
            {
                "ts":     pd.Timestamp(b.date, unit="s", tz="UTC").tz_convert(config.TZ),
                "open":   b.open,
                "high":   b.high,
                "low":    b.low,
                "close":  b.close,
                "volume": b.volume,
            }
            for b in barras_raw
        ])

        # Filtrar por ventana ORB (21:00–21:30 ARG)
        mask = (df["ts"] >= desde_arg) & (df["ts"] < hasta_arg)
        df_orb = df.loc[mask].copy().reset_index(drop=True)

        logger.info(
            f"Barras históricas obtenidas: {len(df)} total, "
            f"{len(df_orb)} en ventana ORB ({desde_arg.strftime('%H:%M')}–{hasta_arg.strftime('%H:%M')} ARG)"
        )
        return df_orb

    # ------------------------------------------------------------------
    # Barras en tiempo real (5s → 15m)
    # ------------------------------------------------------------------

    def suscribir_barras_rt(self, callback: Callable[[dict], None]):
        """
        Suscribe a barras de 5s y agrega a 15 min.
        Cuando se completa una barra de 15 min, llama callback(barra_dict).
        """
        if not self.ib.isConnected():
            raise ConnectionError("No hay conexión activa con TWS.")
        if self._rt_bars is not None:
            logger.warning("Ya hay una suscripción de barras RT activa. Ignorando.")
            return

        self._callback_nueva_vela = callback
        self._barra_actual = BarraAgregada()
        self._contador_barras_5s = 0

        logger.info(f"Suscribiendo barras RT 5s para {self.contrato.localSymbol}...")
        self._rt_bars = self.ib.reqRealTimeBars(
            contract=self.contrato,
            barSize=config.RT_BAR_SIZE,
            whatToShow="TRADES",
            useRTH=False,
        )
        self._rt_bars.updateEvent += self._procesar_barra_5s
        logger.info("Suscripción RT activa.")

    def cancelar_suscripcion(self):
        """Cancela la suscripción a barras en tiempo real."""
        if self._rt_bars is not None:
            try:
                self.ib.cancelRealTimeBars(self._rt_bars)
                logger.info("Suscripción RT cancelada.")
            except Exception as exc:
                logger.warning(f"Error al cancelar suscripción RT: {exc}")
            finally:
                self._rt_bars = None

    def _procesar_barra_5s(self, bars: RealTimeBarList, has_new_bar: bool):
        """
        Callback interno: recibe cada barra de 5s y agrega a barra de 15 min.
        Cuando se acumulan 180 barras de 5s (= 15 min), emite la barra completa.
        """
        if not has_new_bar or not bars:
            return

        barra_5s: RealTimeBar = bars[-1]

        with self._lock:
            self._barra_actual.actualizar(barra_5s)
            self._contador_barras_5s += 1

            if self._contador_barras_5s >= _BARRAS_5S_POR_15MIN:
                self._barra_actual.completa = True
                barra_dict = self._barra_actual.a_dict()
                logger.debug(
                    f"Barra 15m completa: {barra_dict['ts'].strftime('%H:%M')} "
                    f"O={barra_dict['open']} H={barra_dict['high']} "
                    f"L={barra_dict['low']} C={barra_dict['close']}"
                )
                # Resetear para la próxima vela
                self._barra_actual = BarraAgregada()
                self._contador_barras_5s = 0

                # Notificar al ORB engine
                if self._callback_nueva_vela:
                    try:
                        self._callback_nueva_vela(barra_dict)
                    except Exception as exc:
                        logger.error(f"Error en callback de nueva vela: {exc}")

    # ------------------------------------------------------------------
    # Precio de mercado actual (para monitoreo de SL/TP)
    # ------------------------------------------------------------------

    def precio_actual(self) -> Optional[float]:
        """Retorna el último precio de mercado disponible (midpoint o last)."""
        try:
            ticker = self.ib.reqMktData(self.contrato, "", False, False)
            self.ib.sleep(0.5)
            precio = ticker.last if ticker.last and ticker.last > 0 else ticker.close
            self.ib.cancelMktData(self.contrato)
            return precio
        except Exception as exc:
            logger.warning(f"No se pudo obtener precio actual: {exc}")
            return None
