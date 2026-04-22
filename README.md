# Bot ORB-NKD — Estrategia Opening Range Breakout sobre Nikkei 225 Micro Futures

Bot de trading algorítmico que implementa la estrategia **ORB-15** sobre el contrato **NKD** (Nikkei 225 Micro Futures, CME), conectado a **Interactive Brokers** vía `ib_insync` en cuenta **PAPER (demo)**.

---

## Estructura del proyecto

```
orb-nkd/
├── main.py              ← Punto de entrada, loop principal
├── config.py            ← Todos los parámetros configurables
├── ibkr_connector.py    ← Conexión TWS con reconexión automática
├── data_feed.py         ← Barras históricas y RT (5s → 15m)
├── orb_engine.py        ← Lógica ORB: rango, bias, señales + tests
├── risk_manager.py      ← SL, TP, R:R, validaciones
├── trade_executor.py    ← Órdenes IBKR, gestión de posición
├── journal.py           ← CSV de trades + JSON de estado
├── logger_setup.py      ← Logging con timestamp en hora ARG
├── requirements.txt
├── logs/                ← orb_YYYY-MM-DD.txt (rotación diaria)
└── data/
    ├── trades_journal.csv
    └── estado_bot.json
```

---

## Prerequisitos

1. **Python 3.11+** instalado
2. **TWS (Trader Workstation)** o **IB Gateway** abierto y configurado para paper trading
3. TWS configurado para aceptar conexiones en `127.0.0.1:7497`

### Configurar TWS para conexiones API

En TWS: `File → Global Configuration → API → Settings`
- Activar: **Enable ActiveX and Socket Clients**
- Socket port: `7497`
- Activar: **Allow connections from localhost only**

---

## Instalación

```bash
# Crear entorno virtual
python -m venv venv

# Windows
venv\Scripts\activate

# Linux/Mac
source venv/bin/activate

# Instalar dependencias
pip install -r requirements.txt
```

---

## Configuración

Todos los parámetros se editan en `config.py`. Los más relevantes:

| Parámetro | Valor default | Descripción |
|-----------|--------------|-------------|
| `IBKR_PORT` | `7497` | Puerto paper trading (live = 7496) |
| `IBKR_CLIENT_ID` | `1` | ID de cliente API |
| `ORB_MIN_RANGE` | `100` | Rango mínimo ORB en puntos |
| `ORB_MAX_RANGE` | `350` | Rango máximo ORB en puntos |
| `SL_BUFFER` | `40` | Buffer del Stop Loss desde nivel ORB |
| `TP1_MULTIPLIER` | `1.0` | TP1 = entrada ± 1× rango |
| `TP2_MULTIPLIER` | `1.5` | TP2 = entrada ± 1.5× rango |
| `MIN_RR_RATIO` | `2.0` | R:R mínimo requerido |
| `RETEST_TOLERANCE` | `25` | Tolerancia zona retesteo en puntos |
| `RETEST_FALLBACK_BARS` | `3` | Velas antes de entrada directa (fallback) |
| `POINT_VALUE` | `5.0` | USD por punto (NKD Micro = $5/pt) |

---

## Uso

```bash
# Verificar que TWS esté abierto primero, luego:
python main.py
```

El bot:
1. Conecta a TWS y muestra el estado de la cuenta paper
2. Espera hasta las **20:50 ARG** (configurable)
3. A las **21:30 ARG** calcula el rango ORB con barras históricas
4. Suscribe barras en tiempo real y busca ruptura + retesteo
5. Si opera: gestiona TP1/TP2/SL automáticamente
6. Cierra todo a las **23:30 ARG** si hay posición abierta
7. Registra el trade en `data/trades_journal.csv`

Para detener el bot: `Ctrl+C` (cierre limpio)

---

## Tests unitarios

```bash
python orb_engine.py
```

Ejecuta 8 suites de tests que validan:
- Cálculo correcto de ORB High/Low/Rango/Bias
- Rechazo de rangos fuera de límites
- Detección de rupturas LONG y SHORT
- Bloqueo de señales contra el bias
- Activación del fallback tras N velas sin retesteo
- Reset de sesión

---

## Estrategia ORB-15 — Resumen de reglas

### Horario (hora Argentina, UTC-3)

| Evento | Hora |
|--------|------|
| Bot despierta | 20:50 |
| Inicio ventana ORB | 21:00 |
| Fin ORB / inicio búsqueda | 21:30 |
| Fin ventana entrada | 23:00 |
| Cierre forzado posiciones | 23:30 |

### Construcción del rango ORB
- Se toman barras históricas de 15 min entre 21:00 y 21:30 ARG
- **ORB High** = máximo del período
- **ORB Low** = mínimo del período
- Rango válido: entre 100 y 350 puntos

### Filtro de Bias
- Si la vela 21:00–21:30 cierra **por encima** del open → bias **ALCISTA** → solo LONG
- Si la vela cierra **por debajo** del open → bias **BAJISTA** → solo SHORT

### Condición de ruptura
- LONG: nueva vela de 15 min cierra sobre el ORB High con cuerpo > 50%
- SHORT: nueva vela cierra bajo el ORB Low con cuerpo > 50%

### Entrada con retesteo
1. Tras ruptura: activar modo espera de retesteo
2. LONG: precio vuelve a tocar ORB High ±25 pts → entrar en confirmación
3. Si no hay retesteo en 3 velas → entrada directa (fallback)

### Gestión de riesgo
- SL: ORB High/Low ± 40 puntos
- TP1: entrada ± 1× rango ORB → cerrar 50%, SL a breakeven
- TP2: entrada ± 1.5× rango ORB → cerrar 50% restante
- R:R mínimo: 2.0× (si no se cumple, no operar)

---

## Outputs

### Log diario (`logs/orb_YYYY-MM-DD.txt`)
```
[21:31:00] [INFO    ] ORB definido — High: 38420  Low: 38285  Rango: 135 pts
[21:31:00] [INFO    ] Bias: ALCISTA (vela verde) — solo LONG habilitado
[21:45:00] [INFO    ] Ruptura LONG confirmada sobre 38420 — vela 21:45
[21:52:00] [INFO    ] Retesteo LONG detectado en 38418 — vela 21:52
[21:52:00] [INFO    ] Orden enviada — Dirección: LONG | Entry: 38418 | SL: 38380 | TP1: 38553 | TP2: 38621
[22:10:00] [INFO    ] TP1 alcanzado en 38555 — cerrando 50%, moviendo SL a breakeven
[22:35:00] [INFO    ] TP2 alcanzado en 38625 — cerrando posición restante
[22:35:00] [INFO    ] Posición CERRADA [TP2] @ 38625 | PnL total: +207 pts (+$1035.00)
```

### Journal CSV (`data/trades_journal.csv`)
Columnas: `fecha`, `sesion_inicio`, `direccion`, `orb_high`, `orb_low`, `rango_pts`, `bias_vela`, `precio_entrada`, `sl`, `tp1`, `tp2`, `precio_salida`, `resultado_pts`, `resultado_usd`, `toco_tp1`, `toco_tp2`, `motivo_salida`, `r_r_real`

### Estado JSON (`data/estado_bot.json`)
Snapshot actualizado en cada barra con: timestamp, estado, posicion_abierta, orb_high, orb_low, bias, pnl_sesion, trade_done.

---

## Notas importantes

- **PAPER TRADING ONLY**: el bot está configurado para puerto 7497 (paper). Para live trading cambiar a puerto 7496 en `config.py` y revisar TODOS los parámetros de riesgo.
- El tamaño de posición está fijado en **1 contrato** (NKD = $5/punto). Sin gestión dinámica de tamaño.
- Cada punto del NKD vale **$5 USD**. Un rango ORB de 135 pts implica un riesgo de ≈ $200 y un potencial de TP2 de ≈ $1,012.
- El bot **no abre TWS** por sí solo. TWS debe estar corriendo antes de ejecutar `main.py`.

---

## Valor del NKD por punto

| Métrica | Valor |
|---------|-------|
| Valor por punto | $5 USD |
| Riesgo típico (SL 40pts) | $200 |
| TP1 típico (135pts × 1.0) | $675 |
| TP2 típico (135pts × 1.5) | $1,012 |
| Margen inicial aprox. | ~$1,200 |
