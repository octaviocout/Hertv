"""
orb_engine.py — Lógica central de la estrategia ORB-15 sobre NKD.

Responsabilidades:
  - Calcular el rango ORB (High/Low/Bias) a partir de barras históricas
  - Detectar rupturas de la primera vela de 15 min post-ORB
  - Gestionar la lógica de retesteo y fallback
  - Emitir señales de entrada (LONG / SHORT / NADA)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto
from typing import List, Optional

import pandas as pd

import config
from logger_setup import get_logger

logger = get_logger()


# ---------------------------------------------------------------------------
# Tipos
# ---------------------------------------------------------------------------

class Direccion(Enum):
    LONG  = auto()
    SHORT = auto()


class EstadoSesion(Enum):
    ESPERANDO_ORB        = auto()   # Antes de las 21:00
    ORB_EN_PROGRESO      = auto()   # 21:00 – 21:30
    BUSCANDO_RUPTURA     = auto()   # 21:30 – 23:00, sin ruptura aún
    ESPERANDO_RETESTEO   = auto()   # Ruptura confirmada, esperando retesteo
    EN_TRADE             = auto()   # Posición abierta
    SESION_FINALIZADA    = auto()   # Fuera de horario o trade_done


@dataclass
class RangoORB:
    high:      float
    low:       float
    rango:     float
    bias:      Direccion
    orb_open:  float
    orb_close: float


@dataclass
class Señal:
    """Señal de entrada generada por el ORB engine."""
    direccion: Direccion
    precio_ref: float       # Precio de referencia para la orden
    motivo: str             # Descripción legible del motivo


# ---------------------------------------------------------------------------
# Motor ORB
# ---------------------------------------------------------------------------

class ORBEngine:
    """
    Máquina de estados que procesa velas de 15 min y emite señales de trading.

    Ciclo de vida por sesión:
        calcular_orb()
        → [por cada nueva vela] procesar_vela(barra_dict) → Señal | None
        → reset_sesion() al finalizar
    """

    def __init__(self):
        self.estado: EstadoSesion = EstadoSesion.ESPERANDO_ORB
        self.orb: Optional[RangoORB] = None
        self.trade_done: bool = False

        # Estado de ruptura
        self._ruptura_dir: Optional[Direccion] = None
        self._ruptura_precio: Optional[float] = None
        self._velas_post_ruptura: int = 0

    # ------------------------------------------------------------------
    # Cálculo del rango ORB
    # ------------------------------------------------------------------

    def calcular_orb(self, barras_orb: pd.DataFrame) -> Optional[RangoORB]:
        """
        Recibe barras de 15 min dentro de la ventana 21:00–21:30 ARG.
        Calcula High, Low, Bias y valida el rango.
        Retorna RangoORB si es válido, None si debe saltarse la sesión.
        """
        if barras_orb.empty:
            logger.warning("No hay barras en la ventana ORB 21:00–21:30. Sesión saltada.")
            self._marcar_sesion_invalida("Sin barras ORB")
            return None

        orb_high  = float(barras_orb["high"].max())
        orb_low   = float(barras_orb["low"].min())
        rango     = orb_high - orb_low

        # Bias: se determina por la vela de 21:00 completa (primera de la ventana)
        primera_vela = barras_orb.iloc[0]
        orb_open  = float(primera_vela["open"])
        orb_close = float(barras_orb.iloc[-1]["close"])   # close de la última barra del período
        bias = Direccion.LONG if orb_close >= orb_open else Direccion.SHORT

        # Validación de rango
        if rango < config.ORB_MIN_RANGE:
            motivo = f"Rango ORB muy pequeño: {rango:.0f} pts (mínimo {config.ORB_MIN_RANGE})"
            logger.warning(f"ORB inválido — {motivo}. Sesión saltada.")
            self._marcar_sesion_invalida(motivo)
            return None

        if rango > config.ORB_MAX_RANGE:
            motivo = f"Rango ORB muy grande: {rango:.0f} pts (máximo {config.ORB_MAX_RANGE})"
            logger.warning(f"ORB inválido — {motivo}. Sesión saltada.")
            self._marcar_sesion_invalida(motivo)
            return None

        self.orb = RangoORB(
            high=orb_high,
            low=orb_low,
            rango=rango,
            bias=bias,
            orb_open=orb_open,
            orb_close=orb_close,
        )
        self.estado = EstadoSesion.BUSCANDO_RUPTURA

        dir_label = "ALCISTA" if bias == Direccion.LONG else "BAJISTA"
        logger.info(
            f"ORB definido — High: {orb_high:.0f}  Low: {orb_low:.0f}  "
            f"Rango: {rango:.0f} pts"
        )
        logger.info(
            f"Bias: {dir_label} (vela {'verde' if bias == Direccion.LONG else 'roja'}) — "
            f"solo {'LONG' if bias == Direccion.LONG else 'SHORT'} habilitado"
        )
        return self.orb

    # ------------------------------------------------------------------
    # Procesamiento de cada vela RT de 15 min
    # ------------------------------------------------------------------

    def procesar_vela(self, barra: dict) -> Optional[Señal]:
        """
        Evalúa una nueva vela de 15 min cerrada.
        Retorna una Señal si se cumplen las condiciones de entrada, o None.
        """
        if self.orb is None:
            return None
        if self.trade_done:
            return None
        if self.estado == EstadoSesion.SESION_FINALIZADA:
            return None

        o = barra["open"]
        h = barra["high"]
        l = barra["low"]
        c = barra["close"]
        ts = barra["ts"]

        # ---- Filtro de cuerpo ----
        rango_vela = h - l
        if rango_vela == 0:
            logger.debug(f"Vela {ts.strftime('%H:%M')} ignorada — rango cero.")
            return None

        ratio_cuerpo = abs(c - o) / rango_vela
        if ratio_cuerpo <= config.BODY_FILTER_RATIO:
            logger.debug(
                f"Vela {ts.strftime('%H:%M')} — cuerpo insuficiente "
                f"({ratio_cuerpo:.2f} ≤ {config.BODY_FILTER_RATIO})"
            )

        # ---- Máquina de estados ----
        if self.estado == EstadoSesion.BUSCANDO_RUPTURA:
            return self._evaluar_ruptura(barra, ratio_cuerpo)

        if self.estado == EstadoSesion.ESPERANDO_RETESTEO:
            return self._evaluar_retesteo(barra)

        return None

    # ------------------------------------------------------------------
    # Ruptura
    # ------------------------------------------------------------------

    def _evaluar_ruptura(self, barra: dict, ratio_cuerpo: float) -> Optional[Señal]:
        """Detecta si la vela cierra por fuera del rango ORB con cuerpo válido."""
        c   = barra["close"]
        ts  = barra["ts"]

        ruptura_long  = (
            c > self.orb.high
            and self.orb.bias == Direccion.LONG
            and ratio_cuerpo > config.BODY_FILTER_RATIO
        )
        ruptura_short = (
            c < self.orb.low
            and self.orb.bias == Direccion.SHORT
            and ratio_cuerpo > config.BODY_FILTER_RATIO
        )

        if ruptura_long:
            self._ruptura_dir = Direccion.LONG
            self._ruptura_precio = self.orb.high
            self._velas_post_ruptura = 0
            self.estado = EstadoSesion.ESPERANDO_RETESTEO
            logger.info(f"Ruptura LONG confirmada sobre {self.orb.high:.0f} — vela {ts.strftime('%H:%M')}")
            logger.info("Activando modo espera de retesteo...")
            return None   # Esperar retesteo antes de entrar

        if ruptura_short:
            self._ruptura_dir = Direccion.SHORT
            self._ruptura_precio = self.orb.low
            self._velas_post_ruptura = 0
            self.estado = EstadoSesion.ESPERANDO_RETESTEO
            logger.info(f"Ruptura SHORT confirmada bajo {self.orb.low:.0f} — vela {ts.strftime('%H:%M')}")
            logger.info("Activando modo espera de retesteo...")
            return None

        return None

    # ------------------------------------------------------------------
    # Retesteo
    # ------------------------------------------------------------------

    def _evaluar_retesteo(self, barra: dict) -> Optional[Señal]:
        """
        Evalúa si el precio retestea la zona ORB o si se activa el fallback.
        Retorna Señal si corresponde entrar.
        """
        h   = barra["high"]
        l   = barra["low"]
        c   = barra["close"]
        ts  = barra["ts"]

        self._velas_post_ruptura += 1
        tol = config.RETEST_TOLERANCE

        if self._ruptura_dir == Direccion.LONG:
            # Retesteo: precio baja y toca la zona ORB High (±tol puntos) desde arriba
            zona_min = self.orb.high - tol
            zona_max = self.orb.high + tol
            retesteo_ok = (l <= zona_max) and (l >= zona_min - tol) and (c > self.orb.high - tol)

            if retesteo_ok:
                logger.info(f"Retesteo LONG detectado en {l:.0f} (zona {zona_min:.0f}–{zona_max:.0f}) — vela {ts.strftime('%H:%M')}")
                return self._crear_señal(Direccion.LONG, c, "retesteo")

            # Precio sigue subiendo sin retestear → fallback tras N velas
            precio_sigue_arriba = c > self.orb.high
            if self._velas_post_ruptura >= config.RETEST_FALLBACK_BARS and precio_sigue_arriba:
                logger.info(
                    f"Fallback LONG activado — {self._velas_post_ruptura} velas sin retesteo, "
                    f"precio sigue sobre ORB High — entrando a mercado en {c:.0f}"
                )
                return self._crear_señal(Direccion.LONG, c, "fallback")

        elif self._ruptura_dir == Direccion.SHORT:
            zona_min = self.orb.low - tol
            zona_max = self.orb.low + tol
            retesteo_ok = (h >= zona_min) and (h <= zona_max + tol) and (c < self.orb.low + tol)

            if retesteo_ok:
                logger.info(f"Retesteo SHORT detectado en {h:.0f} (zona {zona_min:.0f}–{zona_max:.0f}) — vela {ts.strftime('%H:%M')}")
                return self._crear_señal(Direccion.SHORT, c, "retesteo")

            precio_sigue_abajo = c < self.orb.low
            if self._velas_post_ruptura >= config.RETEST_FALLBACK_BARS and precio_sigue_abajo:
                logger.info(
                    f"Fallback SHORT activado — {self._velas_post_ruptura} velas sin retesteo, "
                    f"precio sigue bajo ORB Low — entrando a mercado en {c:.0f}"
                )
                return self._crear_señal(Direccion.SHORT, c, "fallback")

        return None

    def _crear_señal(self, direccion: Direccion, precio: float, motivo: str) -> Señal:
        self.estado = EstadoSesion.EN_TRADE
        return Señal(direccion=direccion, precio_ref=precio, motivo=motivo)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _marcar_sesion_invalida(self, motivo: str):
        self.estado = EstadoSesion.SESION_FINALIZADA
        self.trade_done = True

    def marcar_trade_completo(self):
        self.trade_done = True
        self.estado = EstadoSesion.SESION_FINALIZADA
        logger.info("Trade completado — sesión finalizada para hoy.")

    def reset_sesion(self):
        """Resetea el estado para una nueva sesión (llamar al inicio del día)."""
        self.estado = EstadoSesion.ESPERANDO_ORB
        self.orb = None
        self.trade_done = False
        self._ruptura_dir = None
        self._ruptura_precio = None
        self._velas_post_ruptura = 0
        logger.info("ORB Engine reseteado para nueva sesión.")


# ---------------------------------------------------------------------------
# Tests unitarios básicos
# ---------------------------------------------------------------------------

def _run_tests():
    """Tests unitarios embebidos. Ejecutar: python orb_engine.py"""
    import sys
    from datetime import datetime, timezone
    import pytz

    tz = pytz.timezone("America/Argentina/Buenos_Aires")
    errores: List[str] = []

    def assert_eq(desc, got, expected):
        if got != expected:
            errores.append(f"FAIL [{desc}]: esperado={expected}, obtenido={got}")
        else:
            print(f"  OK  [{desc}]")

    def assert_true(desc, cond):
        if not cond:
            errores.append(f"FAIL [{desc}]: condición falsa")
        else:
            print(f"  OK  [{desc}]")

    # ---- Helpers para crear barras ----
    def barra(open_, high, low, close, h=21, m=45):
        ts = tz.localize(datetime(2024, 1, 15, h, m))
        return {"ts": ts, "open": open_, "high": high, "low": low, "close": close, "volume": 1000}

    def df_orb(open_, high, low, close):
        ts = tz.localize(datetime(2024, 1, 15, 21, 0))
        return pd.DataFrame([{
            "ts": ts, "open": open_, "high": high, "low": low, "close": close, "volume": 1000
        }])

    print("\n=== TEST 1: ORB válido con bias LONG ===")
    eng = ORBEngine()
    orb = eng.calcular_orb(df_orb(38200, 38420, 38285, 38410))
    assert_true("ORB no es None", orb is not None)
    assert_eq("ORB High", orb.high, 38420)
    assert_eq("ORB Low",  orb.low, 38285)
    assert_eq("Bias LONG", orb.bias, Direccion.LONG)
    assert_eq("Rango", orb.rango, 135.0)

    print("\n=== TEST 2: ORB válido con bias SHORT ===")
    eng2 = ORBEngine()
    orb2 = eng2.calcular_orb(df_orb(38420, 38450, 38250, 38300))
    assert_eq("Bias SHORT", orb2.bias, Direccion.SHORT)

    print("\n=== TEST 3: Rango ORB demasiado pequeño ===")
    eng3 = ORBEngine()
    orb3 = eng3.calcular_orb(df_orb(38300, 38350, 38320, 38340))  # rango = 30 pts
    assert_true("ORB inválido retorna None", orb3 is None)
    assert_eq("Estado FINALIZADA", eng3.estado, EstadoSesion.SESION_FINALIZADA)

    print("\n=== TEST 4: Rango ORB demasiado grande ===")
    eng4 = ORBEngine()
    orb4 = eng4.calcular_orb(df_orb(38000, 38500, 38050, 38450))  # rango = 450 pts
    assert_true("ORB inválido retorna None", orb4 is None)

    print("\n=== TEST 5: Ruptura LONG detectada ===")
    eng5 = ORBEngine()
    eng5.calcular_orb(df_orb(38200, 38420, 38285, 38410))
    # Vela con cuerpo sólido cerrando sobre ORB High
    señal = eng5.procesar_vela(barra(38400, 38500, 38390, 38490, h=21, m=45))  # cierra 38490 > 38420
    assert_eq("Estado ESPERANDO_RETESTEO", eng5.estado, EstadoSesion.ESPERANDO_RETESTEO)
    assert_true("Señal es None (esperando retesteo)", señal is None)

    print("\n=== TEST 6: Fallback LONG tras 3 velas sin retesteo ===")
    eng6 = ORBEngine()
    eng6.calcular_orb(df_orb(38200, 38420, 38285, 38410))
    eng6.procesar_vela(barra(38400, 38500, 38390, 38490, h=21, m=45))  # ruptura
    # 3 velas post-ruptura sin retestear, precio sigue arriba
    for i in range(3):
        señal = eng6.procesar_vela(barra(38490, 38550, 38470, 38530, h=22, m=0 + i*15))
    assert_true("Fallback retorna señal", señal is not None)
    assert_eq("Dirección LONG", señal.direccion, Direccion.LONG)
    assert_eq("Motivo fallback", señal.motivo, "fallback")

    print("\n=== TEST 7: Bias SHORT bloquea LONG ===")
    eng7 = ORBEngine()
    eng7.calcular_orb(df_orb(38420, 38450, 38250, 38300))  # bias SHORT
    # Intenta romper al alza — no debe generar señal
    eng7.procesar_vela(barra(38400, 38500, 38390, 38490, h=21, m=45))
    assert_eq("Estado sigue BUSCANDO_RUPTURA", eng7.estado, EstadoSesion.BUSCANDO_RUPTURA)

    print("\n=== TEST 8: Reset de sesión ===")
    eng8 = ORBEngine()
    eng8.calcular_orb(df_orb(38200, 38420, 38285, 38410))
    eng8.marcar_trade_completo()
    eng8.reset_sesion()
    assert_eq("Estado ESPERANDO_ORB", eng8.estado, EstadoSesion.ESPERANDO_ORB)
    assert_true("ORB reseteado", eng8.orb is None)
    assert_true("trade_done reseteado", not eng8.trade_done)

    if errores:
        print(f"\n{'='*50}")
        print(f"FALLOS ({len(errores)}):")
        for e in errores:
            print(f"  {e}")
        sys.exit(1)
    else:
        print(f"\nTodos los tests pasaron correctamente ({8} suites).")


if __name__ == "__main__":
    _run_tests()
