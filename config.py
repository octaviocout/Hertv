"""
config.py — Parámetros centralizados del bot ORB-NKD.
Todos los valores configurables del sistema viven aquí.
"""
import pytz

# ---------------------------------------------------------------------------
# CONEXIÓN IBKR
# ---------------------------------------------------------------------------
IBKR_HOST = "127.0.0.1"
IBKR_PORT = 7497               # Paper trading (live = 7496)
IBKR_CLIENT_ID = 1
IBKR_RECONNECT_ATTEMPTS = 5
IBKR_RECONNECT_DELAY = 10      # Segundos entre intentos de reconexión
IBKR_TIMEOUT = 20              # Segundos timeout al conectar

# ---------------------------------------------------------------------------
# INSTRUMENTO
# ---------------------------------------------------------------------------
SYMBOL = "NKD"
SEC_TYPE = "FUT"
EXCHANGE = "CME"
CURRENCY = "USD"

# ---------------------------------------------------------------------------
# TIMEZONE Y HORARIOS (todo en hora Argentina UTC-3)
# ---------------------------------------------------------------------------
TZ = pytz.timezone("America/Argentina/Buenos_Aires")

HORA_WAKEUP      = "20:50"   # Bot se activa para prepararse
HORA_ORB_START   = "21:00"   # Inicio ventana ORB
HORA_ORB_END     = "21:30"   # Fin ventana ORB / inicio búsqueda de entrada
HORA_ENTRY_START = "21:30"   # Ventana de entrada válida comienza
HORA_ENTRY_END   = "23:00"   # Ventana de entrada válida termina
HORA_CLOSE_ALL   = "23:30"   # Cierre forzado de posiciones

# ---------------------------------------------------------------------------
# PARÁMETROS ORB
# ---------------------------------------------------------------------------
ORB_TIMEFRAME_MINS = 15       # Duración de cada barra
ORB_MIN_RANGE = 100           # Rango mínimo válido en puntos
ORB_MAX_RANGE = 350           # Rango máximo válido en puntos

# ---------------------------------------------------------------------------
# FILTRO DE CUERPO DE VELA
# ---------------------------------------------------------------------------
BODY_FILTER_RATIO = 0.5       # |close-open| / (high-low) debe superar este valor

# ---------------------------------------------------------------------------
# SISTEMA DE ENTRADA CON RETESTEO
# ---------------------------------------------------------------------------
RETEST_TOLERANCE = 25         # Puntos de tolerancia para zona de retesteo
RETEST_FALLBACK_BARS = 3      # Velas sin retesteo antes de entrada directa

# ---------------------------------------------------------------------------
# PARÁMETROS DE RIESGO
# ---------------------------------------------------------------------------
SL_BUFFER       = 40          # Puntos de buffer para el Stop Loss
TP1_MULTIPLIER  = 1.0         # Multiplicador del rango ORB para TP1
TP2_MULTIPLIER  = 1.5         # Multiplicador del rango ORB para TP2
MIN_RR_RATIO    = 2.0         # Relación Riesgo:Beneficio mínima requerida
POSITION_SIZE   = 1           # Contratos a operar (paper trading)

# ---------------------------------------------------------------------------
# VALOR DEL INSTRUMENTO
# ---------------------------------------------------------------------------
POINT_VALUE = 5.0             # USD por punto (NKD Micro Nikkei = $5/punto)

# ---------------------------------------------------------------------------
# RUTAS DE ARCHIVOS
# ---------------------------------------------------------------------------
LOG_DIR      = "logs"
DATA_DIR     = "data"
JOURNAL_FILE = "data/trades_journal.csv"
STATE_FILE   = "data/estado_bot.json"

# ---------------------------------------------------------------------------
# DATOS HISTÓRICOS
# ---------------------------------------------------------------------------
# Cuántas horas de historia pedir al calcular el ORB
HIST_DURATION   = "2 D"       # Duración de la solicitud histórica
HIST_BAR_SIZE   = "15 mins"   # Tamaño de barra histórica
HIST_WHAT_TO_SHOW = "TRADES"  # Tipo de dato: TRADES = precios reales

# Tamaño de barra en tiempo real (ib_insync solo soporta 5 segundos)
RT_BAR_SIZE = 5               # Segundos (constante de IBKR, no modificar)
