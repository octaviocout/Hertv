"""
ibkr_connector.py — Gestión de conexión a TWS via ib_insync.
Maneja conexión inicial, reconexión automática y obtención del contrato front-month.
"""
import time
from typing import Optional

from ib_insync import IB, Future

import config
from logger_setup import get_logger

logger = get_logger()

# Códigos de error IBKR que son puramente informativos (no errores reales)
_INFO_CODES = {2100, 2103, 2104, 2105, 2106, 2107, 2108, 2158, 2119}


class IBKRConnector:
    """
    Gestiona la conexión a TWS/Gateway de Interactive Brokers.
    Provee el objeto IB y el contrato NKD front-month validado.
    """

    def __init__(self):
        self.ib = IB()
        self.contract: Optional[Future] = None
        self._reconectando = False
        self._registrar_callbacks()

    # ------------------------------------------------------------------
    # Callbacks internos
    # ------------------------------------------------------------------

    def _registrar_callbacks(self):
        self.ib.disconnectedEvent += self._al_desconectar
        self.ib.errorEvent += self._al_error

    def _al_desconectar(self):
        if self._reconectando:
            return
        logger.warning("Conexión con TWS perdida — iniciando reconexión automática...")
        self._reconectando = True
        # La reconexión se delega al loop externo para no bloquear el event loop de ib_insync
        self._intentar_reconexion()

    def _al_error(self, req_id: int, codigo: int, mensaje: str, contrato):
        if codigo in _INFO_CODES:
            logger.debug(f"Info IBKR [{codigo}]: {mensaje}")
        elif codigo == 10167:
            # Datos retrasados — normal en paper trading sin suscripción de market data
            logger.debug(f"Market data retrasada [{codigo}]: {mensaje}")
        else:
            logger.error(f"Error IBKR [req={req_id}] [{codigo}]: {mensaje}")

    # ------------------------------------------------------------------
    # Conexión
    # ------------------------------------------------------------------

    def connect(self) -> bool:
        """
        Conecta a TWS con reintentos.
        Retorna True si la conexión fue exitosa.
        """
        for intento in range(1, config.IBKR_RECONNECT_ATTEMPTS + 1):
            try:
                logger.info(
                    f"Conectando a TWS {config.IBKR_HOST}:{config.IBKR_PORT} "
                    f"(clientId={config.IBKR_CLIENT_ID}, intento {intento}/{config.IBKR_RECONNECT_ATTEMPTS})"
                )
                self.ib.connect(
                    host=config.IBKR_HOST,
                    port=config.IBKR_PORT,
                    clientId=config.IBKR_CLIENT_ID,
                    timeout=config.IBKR_TIMEOUT,
                    readonly=False,
                )
                self._reconectando = False
                self._log_estado_cuenta()
                self.contract = self._obtener_contrato_front_month()
                logger.info("Conexión establecida y contrato validado correctamente.")
                return True

            except Exception as exc:
                logger.error(f"Fallo en intento {intento}: {exc}")
                if intento < config.IBKR_RECONNECT_ATTEMPTS:
                    demora = config.IBKR_RECONNECT_DELAY * intento
                    logger.info(f"Esperando {demora}s antes del próximo intento...")
                    time.sleep(demora)

        logger.critical("No se pudo conectar a TWS después de todos los intentos. Verificar que TWS esté abierto.")
        return False

    def _intentar_reconexion(self):
        """Reconexión con backoff exponencial (llamada desde callback)."""
        demoras = [5, 10, 20, 40, 60]
        for i, demora in enumerate(demoras, 1):
            logger.info(f"Reconexión: intento {i}, esperando {demora}s...")
            time.sleep(demora)
            try:
                self.ib.connect(
                    host=config.IBKR_HOST,
                    port=config.IBKR_PORT,
                    clientId=config.IBKR_CLIENT_ID,
                    timeout=config.IBKR_TIMEOUT,
                    readonly=False,
                )
                self._reconectando = False
                logger.info("Reconexión exitosa.")
                # Re-obtener el contrato por si hubo rollover
                self.contract = self._obtener_contrato_front_month()
                return
            except Exception as exc:
                logger.warning(f"Reconexión fallida (intento {i}): {exc}")

        logger.critical("Reconexión agotada — bot detenido. Reiniciar manualmente.")
        self._reconectando = False

    # ------------------------------------------------------------------
    # Info de cuenta
    # ------------------------------------------------------------------

    def _log_estado_cuenta(self):
        """Loguea estado básico de la cuenta paper al conectar."""
        try:
            cuentas = self.ib.managedAccounts()
            cuenta = cuentas[0] if cuentas else "N/A"
            valores = self.ib.accountValues(cuenta)
            nlv = next(
                (v.value for v in valores if v.tag == "NetLiquidation" and v.currency == "USD"),
                "N/A",
            )
            pnl = next(
                (v.value for v in valores if v.tag == "UnrealizedPnL" and v.currency == "USD"),
                "N/A",
            )
            logger.info(f"Cuenta PAPER: {cuenta} | NLV: ${nlv} | PnL no realizado: ${pnl}")
        except Exception as exc:
            logger.warning(f"No se pudo obtener info de cuenta: {exc}")

    # ------------------------------------------------------------------
    # Contrato
    # ------------------------------------------------------------------

    def _obtener_contrato_front_month(self) -> Future:
        """
        Consulta a IBKR todos los vencimientos disponibles del NKD
        y retorna el contrato front-month (el de vencimiento más próximo).
        """
        contrato_base = Future(
            symbol=config.SYMBOL,
            exchange=config.EXCHANGE,
            currency=config.CURRENCY,
        )
        detalles = self.ib.reqContractDetails(contrato_base)
        if not detalles:
            raise RuntimeError(
                f"IBKR no retornó detalles para {config.SYMBOL}. "
                "Verificar símbolo, exchange y que TWS tenga los permisos necesarios."
            )

        # Ordenar por fecha de vencimiento ascendente
        detalles_ordenados = sorted(
            detalles,
            key=lambda d: d.contract.lastTradeDateOrContractMonth,
        )
        front = detalles_ordenados[0].contract
        self.ib.qualifyContracts(front)

        logger.info(
            f"Contrato front-month: {front.localSymbol} | "
            f"Vencimiento: {front.lastTradeDateOrContractMonth} | "
            f"ConId: {front.conId}"
        )
        return front

    # ------------------------------------------------------------------
    # Estado y desconexión
    # ------------------------------------------------------------------

    def is_connected(self) -> bool:
        return self.ib.isConnected()

    def disconnect(self):
        if self.is_connected():
            self.ib.disconnect()
            logger.info("Desconectado de TWS correctamente.")
