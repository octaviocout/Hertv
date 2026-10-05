# SPEC — Sistema de señales iFVG/OIM para MNQ/ES

**Estado:** BORRADOR v0.1 — pendiente de aprobación. No se escribe código hasta aprobar Spec → Arquitectura → Plan.
**Convención:** todo valor marcado `⟦PENDIENTE: …⟧` lo tiene que definir el trader. El sistema **no arranca** (falla en validación de config) si queda algún pendiente sin completar.

---

## 0. Requisito previo (bloqueante)

Antes de fijar cualquier parámetro de riesgo, el trader verifica en el sitio oficial de Apex Trader Funding las reglas **vigentes** y las registra en `config/apex_rules.yaml` con fecha de verificación y URL:

| Regla | Qué verificar | Valor |
|---|---|---|
| Automatización | Qué está permitido en PA/Live (alertas, herramientas semiautomáticas, copiadores) | ⟦PENDIENTE⟧ |
| Trailing drawdown | Monto, tipo (intradía sobre equity no realizada vs. EOD), cuándo se congela | ⟦PENDIENTE⟧ |
| Límite diario (DLL) | Si aplica a tu tipo de cuenta y cómo se mide | ⟦PENDIENTE⟧ |
| Consistencia | % máximo de un día sobre el total, y si afecta evaluación o solo retiros | ⟦PENDIENTE⟧ |
| Contratos máximos | Por tamaño de cuenta, y si escala (p. ej. mitad hasta superar un umbral) | ⟦PENDIENTE⟧ |
| Horario de cierre | Hora límite de posiciones abiertas (zona horaria) | ⟦PENDIENTE⟧ |
| Otras | Noticias, hedging, overnight, etc. | ⟦PENDIENTE⟧ |

El sistema compara la fecha de verificación con la fecha actual y **bloquea las alertas** si pasaron más de ⟦PENDIENTE: días, sugerido 30⟧ días sin re-verificar.

---

## 1. Alcance

### Incluido
- Detección determinística de setups (modelos OIM, Continuación, Retroceso) portada desde Pine Script v5.
- Estado de mercado por vela de 1 minuto (sin lookahead).
- Motor de riesgo y tamaño en código.
- Backtest walk-forward con holdout bloqueado, costos, deflated Sharpe, simulación de reglas Apex y Monte Carlo.
- Filtro opcional (solo veto / baja de prioridad), sujeto a ablación y calibración.
- Alertas por Telegram, dashboard local, journal, CSV para TradeZella, reporte diario, propuestas semanales.

### Excluido (explícitamente)
- **Cualquier envío de órdenes a la cuenta Apex.** No existe módulo de broker con capacidad de orden.
- Datos de order book / L2 (no hay histórico → no se usa).
- Kelly o tamaño dinámico por confianza.
- Que un modelo (filtro o Claude) genere señales, defina tamaño o modifique límites.

---

## 2. Modos de ejecución

`EXECUTION_MODE ∈ {"alert_only", "paper"}`. Cualquier otro valor → el proceso no arranca.

| Modo | Qué hace | Conexión a cuenta Apex |
|---|---|---|
| `alert_only` | Detecta, filtra, aplica riesgo, calcula tamaño/stop/TP y envía alerta. El trader ejecuta manualmente en Tradovate y registra la ejecución. | Ninguna |
| `paper` | Igual que arriba + simula el fill con las mismas reglas de costos del backtest y lleva su propia curva. | Ninguna |

**Garantías verificadas por tests (deben fallar si se viola):**
1. `test_no_order_path`: escaneo estático del código — falla si aparece cualquier import/cliente/endpoint de envío de órdenes (Tradovate `order/placeorder`, `placeOSO`, `placeOCO`, `modifyorder`, `liquidateposition`, SDKs de brokers, etc.).
2. `test_network_allowlist`: todo acceso de red pasa por un único cliente HTTP con allowlist de hosts (proveedor de datos, Telegram, y opcionalmente Ollama en localhost). Falla si un host no listado es contactado o si se instancia un cliente HTTP fuera de ese módulo.
3. `test_execution_mode_enum`: cualquier valor distinto de los dos permitidos aborta el arranque.
4. `test_no_credentials_for_broker`: la config no acepta variables de credenciales de Tradovate/Apex; si existen en `.env`, el proceso aborta.

---

## 3. Estrategia (a completar con el código Pine)

Fuente de verdad: indicadores Pine Script v5 del trader ⟦PENDIENTE: código⟧. `strategy.md` se redacta **después** de leer el código; esta sección define qué debe contener sin ambigüedad.

Por cada modelo (OIM, Continuación, Retroceso):

| Campo | Definición exigida |
|---|---|
| Timeframe(s) | TF de detección y TF de contexto ⟦PENDIENTE⟧ |
| Definición de FVG | Velas involucradas, tamaño mínimo (ticks/ATR), si cuenta mecha o cuerpo |
| Definición de iFVG | Qué cierre/penetración invierte el FVG (cierre más allá del borde vs. mecha) |
| Definición de OIM | ⟦PENDIENTE: la define el código Pine⟧ |
| Entrada | Tipo (límite en borde/CE del gap, o mercado al cierre de vela), precio exacto |
| Stop | Regla exacta (p. ej. extremo de la vela X ± N ticks) |
| Take profit | Regla exacta (R múltiple fijo, liquidez opuesta, etc.) y si hay parciales |
| Vigencia de la orden | Cuántas velas vale el setup antes de cancelarse |
| Invalidación | Condición exacta que anula el setup antes de la entrada |
| Killzones habilitadas | Por modelo, en hora de Nueva York (America/New_York, con DST) ⟦PENDIENTE⟧ |

**Paridad Pine ↔ Python (test obligatorio):**
- Período común ⟦PENDIENTE⟧, mismo feed exportado desde TradingView (o mismas velas) para eliminar diferencias de datos.
- Se compara por señal: timestamp, dirección, entrada, stop, TP.
- Se listan **todas** las diferencias con causa (dato distinto, redondeo, semántica de `barstate`, repintado). Tolerancia de precio: 0 ticks. Meta: 100 % de coincidencia o cada diferencia explicada y aceptada por el trader.
- Atención: si el Pine usa `request.security` con lookahead o datos de vela no cerrada, el indicador **repinta**; eso se documenta y en Python se usa la versión sin lookahead.

---

## 4. Estado de mercado (snapshot por vela de 1 m)

Calculado al **cierre** de la vela `t`, usando solo datos con timestamp `< t_decisión`. Todos los campos numéricos.

| Campo | Definición |
|---|---|
| `price` | Cierre de la vela |
| `rv_n` | Volatilidad realizada (desvío de retornos log de 1 m, ventana ⟦PENDIENTE, sugerido 30⟧) |
| `atr_n` | ATR en puntos (ventana ⟦PENDIENTE⟧) |
| `htf_trend` | −1/0/+1 en TF superior ⟦PENDIENTE: TF y regla, p. ej. estructura HH/HL en 15 m o pendiente de EMA⟧, solo velas HTF **cerradas** |
| `kz_id`, `kz_min_from_start`, `kz_min_to_end` | Posición respecto de la killzone activa |
| `min_to_cutoff` | Minutos al horario de corte Apex/propio |
| `dist_pdh`, `dist_pdl` | Distancia en puntos a máximo/mínimo del día previo |
| `dist_asia_h/l`, `dist_london_h/l` | Distancia a máximos/mínimos de sesiones (si se habilitan) |
| `dist_swing_h/l` | Distancia al último swing confirmado (confirmación con N velas a la derecha → sin lookahead) |

Test: `test_no_lookahead` — se trunca el dataset en `t` y el snapshot en `t` debe ser idéntico al calculado con el dataset completo.

---

## 5. Tamaño

- Contratos fijos por instrumento: `CONTRACTS_MNQ` ⟦PENDIENTE⟧, `CONTRACTS_ES` ⟦PENDIENTE⟧.
- `riesgo_usd = stop_pts × valor_punto × contratos + costos_estimados` (MNQ USD 2/pt, ES USD 50/pt; tick 0,25 → MNQ USD 0,50, ES USD 12,50).
- Condición: `riesgo_usd ≤ min(RIESGO_MAX_USD, F × distancia_actual_al_trailing_DD)`.
- Si no se cumple → **setup descartado** (motivo registrado). Nunca se achica el stop ni se baja contratos automáticamente.
  - ⟦PENDIENTE: confirmá si querés que tampoco se bajen contratos (lectura literal de "contratos fijos") — es lo que asumo⟧.

Parámetros: `RIESGO_MAX_USD` ⟦PENDIENTE⟧, `F` ⟦PENDIENTE, sugerido ≤ 0,25⟧.

---

## 6. Reglas de riesgo (código, antes de cada alerta, inmutables por modelos)

Se evalúan en orden fijo; la primera que falla veta y se registra.

| # | Regla | Parámetro |
|---|---|---|
| R0 | Kill switch activo → nada sale | archivo/flag + comando Telegram `/kill` (solo chat_id autorizado) |
| R1 | Reglas Apex verificadas y vigentes (sección 0) | días máx. sin verificar |
| R2 | Dentro de killzone habilitada | killzones |
| R3 | No a menos de N minutos del corte | `CUTOFF_BUFFER_MIN` ⟦PENDIENTE⟧ |
| R4 | Contratos ≤ máximo propio ≤ máximo Apex | `MAX_CONTRATOS` ⟦PENDIENTE⟧ |
| R5 | Pérdida del día < DLL propio (más conservador que Apex) | `DLL_PROPIO_USD` ⟦PENDIENTE⟧ |
| R6 | Trades del día < máximo | `MAX_TRADES_DIA` ⟦PENDIENTE⟧ |
| R7 | Pérdidas seguidas < K (bloqueo hasta el día siguiente) | `K` ⟦PENDIENTE⟧ |
| R8 | Riesgo del trade dentro de límites de tamaño (sección 5) | — |
| R9 | Sin posición abierta / alerta pendiente sin resolver | ⟦PENDIENTE: ¿se permite más de una posición simultánea?⟧ |
| R10 | Filtro opcional (solo puede vetar o bajar prioridad) | — |

- La configuración de riesgo se carga una vez, se valida (`DLL_PROPIO < DLL_APEX`, `MAX_CONTRATOS ≤ máx Apex`, etc.) y queda **inmutable** en memoria. Cambiarla exige reinicio y queda registrado.
- Noticias/headlines: si se ingieren, son **datos** (p. ej. bandera "evento de alto impacto en ±X min" desde un calendario) y solo pueden vetar. Nunca se interpreta texto como instrucción. ⟦PENDIENTE: ¿querés veto por calendario económico? ¿fuente?⟧
- Para R5–R7 en `alert_only`, el sistema necesita saber qué ejecutaste y el resultado → ver sección 10.

---

## 7. Backtest

| Ítem | Especificación |
|---|---|
| Datos | 1 m MNQ y ES, ≥ 2 años, con regímenes distintos. Proveedor ⟦PENDIENTE⟧, período ⟦PENDIENTE⟧ |
| Contratos continuos | Regla de roll ⟦PENDIENTE: sugerido roll por volumen / fecha fija, precios sin ajustar dentro de cada contrato⟧ |
| Costos | Comisión por lado ⟦PENDIENTE USD/contrato⟧ + slippage ⟦PENDIENTE ticks⟧ en entrada y en stop (TP límite sin slippage, pero solo se llena si el precio **cruza** el TP, no si lo toca — ⟦PENDIENTE: confirmar⟧) |
| Ambigüedad intrabarra | Stop y TP en la misma vela → stop. Entrada y stop en la misma vela → stop |
| Walk-forward | Ventanas de ajuste/validación ⟦PENDIENTE: sugerido 6 m / 2 m, rolling⟧ |
| Holdout | Últimos `N` meses ⟦PENDIENTE: sugerido 6⟧, bloqueado por hash; se evalúa **una vez**; el uso queda registrado y un segundo intento falla |
| Registro de variantes | Cada corrida con parámetros distintos suma al contador de pruebas → Deflated Sharpe (Bailey & López de Prado) |
| Métricas | Expectancy USD/trade (neta), payoff ratio, N trades, máx. DD, peor racha; win rate solo junto al payoff; Sharpe y DSR |
| Simulación Apex | Trailing DD, DLL, máx. contratos, consistencia, cierre forzado al corte, sobre la curva por trade **y** por equity intradía (MAE) si el trailing es intradía |
| Monte Carlo | ≥ 10.000 permutaciones/bootstraps del orden de trades → P(sobrevivir), P(alcanzar objetivo ⟦PENDIENTE: profit target⟧) |
| Aceptación | Expectancy > 0 neta en holdout, con ≥ `MIN_TRADES` ⟦PENDIENTE⟧ y P(quemar) < `X %` ⟦PENDIENTE⟧. Si no cumple: se reporta como **NO APTO**, sin ajustes posteriores sobre el holdout |

Los números los produce el harness; Claude no califica resultados.

---

## 8. Filtro opcional

- Candidatos: ⟦PENDIENTE: ¿qué es "Jev"? ¿API externa o modelo propio? / modelo Ollama de TradingLab: nombre y versión⟧.
- Entrada: snapshot numérico del setup ya validado. Salida estructurada (JSON con schema); cualquier salida inválida = **sin efecto** (no veta, no aprueba; se registra error).
- Una pregunta = un factor (p. ej. "régimen: tendencia/rango/volátil" como elección; "calidad del setup 0–1" como puntaje).
- Acción permitida: `veto` o `baja_prioridad`. No puede cambiar entrada, stop, TP ni tamaño.
- Ablación base vs. base+filtro en mismos datos; se incorpora solo si mejora expectancy o reduce DD sin empeorar la otra (criterio exacto ⟦PENDIENTE⟧).
- Calibración: Brier score + curva de confiabilidad sobre tus datos; si está descalibrado → isotónica/Platt en código, o se descarta.
- Nota de método: si el filtro se ajusta/recalibra, eso consume datos del walk-forward, **no** del holdout. Un LLM no es determinístico: se fija temperatura 0, semilla y versión de modelo, y se cachean respuestas para que el backtest sea reproducible.

---

## 9. Alertas (Telegram)

Cada alerta incluye: instrumento, modelo, dirección, entrada, stop, TP, contratos, riesgo USD, R:R, killzone, vigencia (hasta qué hora vale), ID de señal.
Tipos: setup válido · regla de riesgo activada · error · kill switch on/off · heartbeat de inicio/fin de sesión.
Token y chat_id solo en `.env`; los logs enmascaran secretos (test que lo verifica).

---

## 10. Registro de ejecuciones manuales

Necesario para dashboard, reglas R5–R7 y journal. ⟦PENDIENTE: elegí una⟧
- **A (recomendada):** botones en Telegram "Tomé / No tomé" + import del CSV de fills de Tradovate al final del día para conciliar precios reales.
- **B:** carga manual en el dashboard.
- Se descarta cualquier conexión API a la cuenta Apex, incluso de solo lectura, salvo que la autorices explícitamente y Apex lo permita.

---

## 11. Journal y exportación

- Cada señal (tomada o no): snapshot, resultado de cada regla/filtro con motivo, alerta enviada, ejecución, resultado (y resultado hipotético si no se tomó).
- Almacenamiento: SQLite local.
- CSV formato Tradovate para TradeZella con P&L por FIFO. ⟦PENDIENTE: pasame un CSV de ejemplo exportado de tu Tradovate (sin datos sensibles) para replicar columnas exactas⟧.

---

## 12. Dashboard local

Por señal: detectada → reglas/filtros (pasó/no y por qué) → alerta → ejecución → resultado. Vista diaria con P&L, expectancy acumulada, mayor pérdida, estado de reglas y kill switch, calibración del filtro (si existe). Solo escucha en `127.0.0.1` (acceso remoto vía túnel SSH).

---

## 13. Reportes y mejora continua

- **Diario:** señales, trades ejecutados, P&L, expectancy acumulada, mayor pérdida, calibración del filtro.
- **Semanal:** causa raíz de cada pérdida → `proposals/AAAA-MM-DD-*.md` con evidencia. Nunca se modifica `strategy.md` sin aprobación; todo cambio repite walk-forward; el holdout usado no se reutiliza.

---

## 14. Deploy

VPS ⟦PENDIENTE: proveedor/SO⟧ con `systemd` (restart automático) y timers que activan el proceso solo en killzones. Alertas por Telegram ante caída/reinicio.

---

## 15. Datos operativos en vivo (faltante crítico del brief)

Para `alert_only`/`paper` hace falta un **feed en tiempo real de 1 m** (o ticks) de MNQ/ES. ⟦PENDIENTE: fuente en vivo y si tenés licencia de datos CME para uso no-display/API⟧. Sin esto el sistema solo puede backtestear.
