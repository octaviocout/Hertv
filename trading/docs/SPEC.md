# SPEC — Sistema de señales iFVG/OIM para MNQ

**Estado:** BORRADOR v0.4 (Tradeify 50K eval · solo MNQZ2026) — pendiente de aprobación. No se escribe código hasta aprobar Spec → Arquitectura → Plan.
**Convención:** todo valor marcado `⟦PENDIENTE: …⟧` lo tiene que definir el trader. El sistema **no arranca** (falla en validación de config) si queda algún pendiente sin completar.

---

## 0. Cuenta y reglas de la firma (bloqueante)

**Firma:** Tradeify · **Cuenta:** 50K · **Fase:** evaluación · **Modo:** solo alertas, el trader ejecuta manualmente.
**Instrumento único:** Micro E-mini Nasdaq-100 (MNQ), CME. Contrato activo hoy: **MNQZ2026** (diciembre 2026). Tick 0,25 = USD 0,50 · punto = USD 2. ES/MES quedan fuera de alcance.

**Roll en vivo:** el sistema lee el contrato activo de `config/contracts.yaml` (símbolo + fecha de roll que carga el trader) y avisa por Telegram ⟦PENDIENTE: N días, sugerido 5⟧ días antes del roll. Las alertas siempre indican el símbolo exacto (p. ej. `MNQZ2026`). Si la fecha de roll ya pasó y el contrato no se actualizó, **no salen alertas**. Fecha de roll a MNQH2027 ⟦PENDIENTE: confirmala con el calendario de CME y la práctica de tu plataforma⟧.
**Plan:** ⟦PENDIENTE: nombre del plan (Select / Growth / Lightning)⟧.

Valores informados por el trader el 2026-10-06. Se cargan en `config/firm_rules.yaml` con fecha de verificación.

| Regla | Valor | Estado |
|---|---|---|
| Drawdown máximo | USD 2.000 | Confirmado (interpretación: "2000" = drawdown máximo) |
| Tipo de drawdown | Trailing EOD, ¿se congela? ¿a qué saldo? | ⟦PENDIENTE⟧ |
| DLL de la firma | USD 1.200 por día | Confirmado |
| Consistencia (eval) | 40 %: el mejor día ≤ 40 % de la ganancia total | Confirmado |
| Contratos máximos | 20 micros MNQ | Confirmado |
| Cierre obligatorio | 16:59, hora de Nueva York | Confirmado (zona horaria asumida: NY) |
| Profit target | — | ⟦PENDIENTE: ¿USD 3.000?⟧ |
| Días mínimos | — | ⟦PENDIENTE⟧ |
| Corte del día de trading | Se asume 18:00 NY (reapertura CME): la sesión Asia 20:00–00:00 cuenta para el **día siguiente** en DLL, consistencia y máximo de trades | ⟦PENDIENTE: confirmar⟧ |
| Automatización | Irrelevante: **el sistema no envía órdenes** por decisión del trader | — |
| Plataforma | Tradovate | Confirmado |

El sistema compara la fecha de verificación con la fecha actual y **bloquea las alertas** si pasaron más de ⟦PENDIENTE: días, sugerido 30⟧ días sin volver a verificar.

---

## 1. Alcance

### Incluido
- Detección determinística de setups (modelos OIM, Continuación, Retroceso) portada desde Pine Script v5.
- Estado de mercado por vela de 1 minuto (sin lookahead).
- Motor de riesgo y tamaño en código.
- Backtest walk-forward con holdout bloqueado, costos, deflated Sharpe, simulación de reglas de la firma y Monte Carlo.
- Filtro opcional (solo veto / baja de prioridad), sujeto a ablación y calibración.
- Alertas por Telegram, dashboard local, journal, CSV para TradeZella, reporte diario, propuestas semanales.

### Excluido (explícitamente)
- **Cualquier envío de órdenes a la cuenta de la firma.** No existe módulo de broker con capacidad de orden.
- Datos de order book / L2 (no hay histórico → no se usa).
- Kelly o tamaño dinámico por confianza.
- Que un modelo (filtro o Claude) genere señales, defina tamaño o modifique límites.

---

## 2. Modos de ejecución

`EXECUTION_MODE ∈ {"alert_only", "paper"}`. Cualquier otro valor → el proceso no arranca.

| Modo | Qué hace | Conexión a la cuenta Tradeify |
|---|---|---|
| `alert_only` | Detecta, filtra, aplica riesgo, calcula tamaño/stop/TP y envía alerta. El trader ejecuta manualmente en Tradovate y registra la ejecución. | Ninguna |
| `paper` | Igual que arriba + simula el fill con las mismas reglas de costos del backtest y lleva su propia curva. | Ninguna |

**Garantías verificadas por tests (deben fallar si se viola):**
1. `test_no_order_path`: escaneo estático del código — falla si aparece cualquier import/cliente/endpoint de envío de órdenes (Tradovate `order/placeorder`, `placeOSO`, `placeOCO`, `modifyorder`, `liquidateposition`, SDKs de brokers, etc.).
2. `test_network_allowlist`: todo acceso de red pasa por un único cliente HTTP con allowlist de hosts (proveedor de datos, Telegram, y opcionalmente Ollama en localhost). Falla si un host no listado es contactado o si se instancia un cliente HTTP fuera de ese módulo.
3. `test_execution_mode_enum`: cualquier valor distinto de los dos permitidos aborta el arranque.
4. `test_no_credentials_for_broker`: la config no acepta variables de credenciales de broker/plataforma (Tradovate, NinjaTrader, Rithmic, Tradeify); si existen en `.env`, el proceso aborta.

---

## 3. Estrategia (a completar con el código Pine)

Fuente de verdad: indicadores Pine Script v5 del trader ⟦PENDIENTE: código⟧. `strategy.md` se redacta **después** de leer el código; esta sección define qué debe contener sin ambigüedad.

Por cada modelo (OIM, Continuación, Retroceso):

| Campo | Definición exigida |
|---|---|
| Timeframes | **Bias y FVG de contexto:** 4H, 30m, 15m (solo velas cerradas). **Ejecución:** 1m, 30s o 15s — regla exacta de cuál se usa en cada caso ⟦PENDIENTE: lo define el Pine⟧ |
| Bias | Regla exacta por TF y cómo se combinan los tres (p. ej. 4H manda; 30m/15m deben coincidir) ⟦PENDIENTE⟧ |
| FVG HTF como POI | Qué FVG de 4H/30m/15m cuentan (abiertos, mitigados al 50 %, invalidados por cierre) y cuántos se conservan ⟦PENDIENTE⟧ |
| Definición de FVG | Velas involucradas, tamaño mínimo (ticks/ATR), si cuenta mecha o cuerpo |
| Definición de iFVG | Qué cierre/penetración invierte el FVG (cierre más allá del borde vs. mecha) |
| Definición de OIM | ⟦PENDIENTE: la define el código Pine⟧ |
| Entrada | Tipo (límite en borde/CE del gap, o mercado al cierre de vela), precio exacto |
| Stop | Regla exacta (p. ej. extremo de la vela X ± N ticks) |
| Take profit | Regla exacta (R múltiple fijo, liquidez opuesta, etc.) y si hay parciales |
| Vigencia de la orden | Cuántas velas vale el setup antes de cancelarse |
| Invalidación | Condición exacta que anula el setup antes de la entrada |
| Killzones habilitadas | **NY AM** y **Asia**, en hora de Nueva York (America/New_York, con DST). **NY AM 09:30–11:00** y **Asia 20:00–00:00**. Las alertas solo se emiten dentro de esas ventanas |
| Gestión al terminar la killzone | Qué pasa con una posición abierta al cierre de la ventana (sobre todo Asia, que termina a medianoche): ¿cierre a una hora fija, o se deja correr hasta stop/TP? ⟦PENDIENTE⟧. En cualquier caso, cierre forzoso antes de las 16:59 NY |

**Paridad Pine ↔ Python (test obligatorio):**
- Período común ⟦PENDIENTE⟧, mismo feed exportado desde TradingView (o mismas velas) para eliminar diferencias de datos.
- Se compara por señal: timestamp, dirección, entrada, stop, TP.
- Se listan **todas** las diferencias con causa (dato distinto, redondeo, semántica de `barstate`, repintado). Tolerancia de precio: 0 ticks. Meta: 100 % de coincidencia o cada diferencia explicada y aceptada por el trader.
- Atención: si el Pine usa `request.security` con lookahead o datos de vela no cerrada, el indicador **repinta**; eso se documenta y en Python se usa la versión sin lookahead.

---

## 4. Estado de mercado (snapshot por vela de ejecución: 1m / 30s / 15s)

Calculado al **cierre** de la vela `t`, usando solo datos con timestamp `< t_decisión`. Todos los campos numéricos.

| Campo | Definición |
|---|---|
| `price` | Cierre de la vela |
| `rv_n` | Volatilidad realizada (desvío de retornos log de 1 m, ventana ⟦PENDIENTE, sugerido 30⟧) |
| `atr_n` | ATR en puntos (ventana ⟦PENDIENTE⟧) |
| `bias_4h`, `bias_30m`, `bias_15m` | −1/0/+1 por TF según la regla de bias del Pine, solo con velas HTF **cerradas** |
| `dist_fvg_htf_*` | Distancia al FVG abierto más cercano de 4H/30m/15m (arriba y abajo) y si el precio está dentro |
| `kz_id`, `kz_min_from_start`, `kz_min_to_end` | Posición respecto de la killzone activa |
| `min_to_cutoff` | Minutos al horario de corte de la firma o propio |
| `dist_pdh`, `dist_pdl` | Distancia en puntos a máximo/mínimo del día previo |
| `dist_asia_h/l`, `dist_london_h/l` | Distancia a máximos/mínimos de sesiones (si se habilitan) |
| `dist_swing_h/l` | Distancia al último swing confirmado (confirmación con N velas a la derecha → sin lookahead) |

Test: `test_no_lookahead` — se trunca el dataset en `t` y el snapshot en `t` debe ser idéntico al calculado con el dataset completo.

---

## 5. Tamaño

**Riesgo por trade:** 0,5 % a 1 % de la cuenta, calculado sobre el **saldo inicial de USD 50.000** (no sobre el saldo actual): USD 250 a USD 500.

| Nivel | USD | Trades perdedores seguidos hasta agotar un DD de USD 2.000 | Con DD de USD 2.500 |
|---|---|---|---|
| 0,5 % | 250 | 8 | 10 |
| 1 % | 500 | 4 | 5 |

**Regla para elegir 0,5 % o 1 %:** 1 % solo en un "setup muy claro". Para que el código lo decida sin interpretar, "muy claro" se define como una condición verificable. ⟦PENDIENTE — propuesta: bias de 4H, 30m y 15m alineados **y** entrada desde un FVG de HTF abierto. Si tu Pine ya tiene una calificación de setup (A+/A/B), se usa esa⟧. El backtest compara "siempre 0,5 %" contra la regla; cada variante suma al contador del deflated Sharpe. La regla no la puede cambiar ningún modelo.

**Cálculo:**
- `presupuesto = min(nivel_usd, F × (saldo − piso_drawdown))`, con `F` ⟦PENDIENTE, sugerido 0,25⟧.
- `contratos = floor(presupuesto / (stop_pts × valor_punto + costos_por_contrato))`, con tope en `MAX_CONTRATOS`.
- Si `contratos < 1` → **setup descartado** y se registra el motivo. Nunca se achica el stop.
- Valor por punto MNQ: USD 2.
- Ejemplo: stop de 20 puntos = USD 40 por contrato + costos → con USD 250, 6 contratos; con USD 500, 12 (si el máximo lo permite).
- Límite: **20 micros**. Se activa con stops menores a ~6 puntos al 0,5 % y ~12 puntos al 1 %. En ese caso se usan 20 contratos y el riesgo real queda por debajo del nivel; no se agranda el stop.

**Peor caso con estos parámetros** (2 trades por día, drawdown de USD 2.000):

| Riesgo | Peor día (2 pérdidas) | % del drawdown | Días malos seguidos hasta quemar |
|---|---|---|---|
| 0,5 % siempre | USD 500 | 25 % | 4 |
| 1 % siempre | USD 1.000 | 50 % | 2 |

El slippage en el stop puede empeorar estos números. El Monte Carlo los mide con la distribución real de trades.

Nota: esto reemplaza "contratos fijos" de la v0.1. Ahora los contratos varían con el stop para mantener el riesgo en USD constante. Sigue sin haber Kelly ni tamaño por confianza.

---

## 6. Reglas de riesgo (código, antes de cada alerta, inmutables por modelos)

Se evalúan en orden fijo; la primera que falla veta y se registra.

| # | Regla | Parámetro |
|---|---|---|
| R0 | Kill switch activo → nada sale | archivo/flag + comando Telegram `/kill` (solo chat_id autorizado) |
| R1 | Reglas de la firma verificadas y vigentes (sección 0) | días máx. sin verificar |
| R2 | Dentro de killzone habilitada | killzones |
| R3 | No a menos de N minutos del corte ni del fin de la killzone | `CUTOFF_BUFFER_MIN` ⟦PENDIENTE⟧ |
| R3b | Consistencia 40 %: tope de ganancia diaria = 40 % × max(profit target, ganancia total acumulada). Si `P&L del día + ganancia al TP` supera el tope, el setup se marca ⟦PENDIENTE: bloquear, o alertar con advertencia⟧. El backtest compara ambas opciones | 40 % |
| R4 | Contratos ≤ máximo propio ≤ 20 micros | `MAX_CONTRATOS` = 20 |
| R5 | Pérdida del día < DLL propio (más conservador que el de la firma, USD 1.200) | `DLL_PROPIO_USD` = USD 1.000 ⟦PENDIENTE: confirmar⟧. Además, un setup se descarta si su pérdida al stop + slippage llevaría el día por debajo del DLL propio |
| R6 | Trades del día < máximo | `MAX_TRADES_DIA` = 2 |
| R7 | Pérdidas seguidas < K (bloqueo hasta el día siguiente) | `K` = 2 ⟦PENDIENTE: confirmar; con 2 trades por día, K=2 equivale a cerrar el día tras 2 pérdidas⟧ |
| R8 | Riesgo del trade dentro de límites de tamaño (sección 5) | — |
| R9 | Sin posición abierta / alerta pendiente sin resolver | ⟦PENDIENTE: ¿se permite más de una posición simultánea?⟧ |
| R10 | Filtro opcional (solo puede vetar o bajar prioridad) | — |

- La configuración de riesgo se carga una vez, se valida (`DLL_PROPIO < DLL de la firma` si existe, `MAX_CONTRATOS ≤ máx de la firma`, etc.) y queda **inmutable** en memoria. Cambiarla exige reinicio y queda registrado.
- Noticias/headlines: si se ingieren, son **datos** (p. ej. bandera "evento de alto impacto en ±X min" desde un calendario) y solo pueden vetar. Nunca se interpreta texto como instrucción. ⟦PENDIENTE: ¿querés veto por calendario económico? ¿fuente?⟧
- Para R5–R7 en `alert_only`, el sistema necesita saber qué ejecutaste y el resultado → ver sección 10.

---

## 7. Backtest

| Ítem | Especificación |
|---|---|
| Datos | MNQ, ≥ 2 años, con regímenes distintos. **Hace falta resolución sub-minuto** (ticks u OHLCV de 1 s) para construir velas de 30 s y 15 s y resolver stop/TP dentro de la vela. Con datos de 1 m solo se puede backtestear la ejecución en 1 m. Proveedor ⟦PENDIENTE⟧, período ⟦PENDIENTE⟧ |
| Latencia humana | Entre la alerta y tu orden pasan segundos. Se modela un retraso de ⟦PENDIENTE: s, sugerido medirlo en paper⟧ y la entrada se toma al precio posterior a ese retraso. Una entrada límite que el precio ya pasó cuenta como no ejecutada. En 15 s este efecto puede cambiar el resultado |
| Contratos continuos | Serie de contratos trimestrales de MNQ (H/M/U/Z) unidos con la misma regla de roll que usás en vivo; precios **sin ajustar** dentro de cada contrato y sin señales que crucen el roll (los FVG HTF se recalculan sobre el contrato nuevo) ⟦PENDIENTE: confirmar regla — sugerido roll por fecha fija, ~8 días antes del vencimiento⟧ |
| Costos | Comisión por lado ⟦PENDIENTE USD/contrato⟧ + slippage ⟦PENDIENTE ticks⟧ en entrada y en stop (TP límite sin slippage, pero solo se llena si el precio **cruza** el TP, no si lo toca — ⟦PENDIENTE: confirmar⟧) |
| Ambigüedad intrabarra | Stop y TP en la misma vela de la menor resolución disponible → stop. Entrada y stop en la misma vela → stop |
| Walk-forward | Ventanas de ajuste/validación ⟦PENDIENTE: sugerido 6 m / 2 m, rolling⟧ |
| Holdout | Últimos `N` meses ⟦PENDIENTE: sugerido 6⟧, bloqueado por hash; se evalúa **una vez**; el uso queda registrado y un segundo intento falla |
| Registro de variantes | Cada corrida con parámetros distintos suma al contador de pruebas → Deflated Sharpe (Bailey & López de Prado) |
| Métricas | Expectancy USD/trade (neta), payoff ratio, N trades, máx. DD, peor racha; win rate solo junto al payoff; Sharpe y DSR |
| Simulación de la firma | Drawdown trailing EOD con congelamiento, DLL si aplica, máx. contratos, consistencia, días mínimos, cierre forzado al corte. Se evalúa el **pase de la evaluación**: llegar a USD 3.000 (a confirmar) cumpliendo consistencia antes de tocar el piso |
| Monte Carlo | ≥ 10.000 permutaciones/bootstraps del orden de trades → P(pasar la eval), P(quemar), trades y días esperados hasta el resultado |
| Aceptación | Expectancy > 0 neta en holdout, con ≥ `MIN_TRADES` ⟦PENDIENTE⟧, P(quemar) < `X %` ⟦PENDIENTE⟧ y P(pasar) reportada. Si no cumple: se reporta como **NO APTO**, sin ajustes posteriores sobre el holdout |

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

Necesario para dashboard, reglas R5–R7 y journal. Se adopta la opción A salvo que indiques otra.
- **A (recomendada):** botones en Telegram "Tomé / No tomé" + import del CSV de fills de tu plataforma al final del día para conciliar precios reales.
- **B:** carga manual en el dashboard.
- Se descarta cualquier conexión API a la cuenta Tradeify, incluso de solo lectura, salvo que la autorices explícitamente.

---

## 11. Journal y exportación

- Cada señal (tomada o no): snapshot, resultado de cada regla/filtro con motivo, alerta enviada, ejecución, resultado (y resultado hipotético si no se tomó).
- Almacenamiento: SQLite local.
- CSV en formato Tradovate para TradeZella, con P&L por FIFO. ⟦PENDIENTE: pasame un CSV de ejemplo exportado de Tradovate (Performance u Orders), sin datos sensibles, para copiar las columnas exactas⟧.

---

## 12. Dashboard local

Por señal: detectada → reglas/filtros (pasó/no y por qué) → alerta → ejecución → resultado. Vista diaria con P&L, expectancy acumulada, mayor pérdida, estado de reglas y kill switch, calibración del filtro (si existe). Solo escucha en `127.0.0.1` (acceso remoto vía túnel SSH).

---

## 13. Reportes y mejora continua

- **Diario:** señales, trades ejecutados, P&L, expectancy acumulada, mayor pérdida, calibración del filtro.
- **Semanal:** causa raíz de cada pérdida → `proposals/AAAA-MM-DD-*.md` con evidencia. Nunca se modifica `strategy.md` sin aprobación; todo cambio repite walk-forward; el holdout usado no se reutiliza.

---

## 14. Deploy

VPS ⟦PENDIENTE: proveedor/SO⟧ con `systemd` (reinicio automático) y timers que lo activan solo en NY AM y Asia (con margen previo para cargar el contexto HTF de 4H/30m/15m). Alertas por Telegram ante caída/reinicio.

---

## 15. Datos operativos en vivo (faltante crítico del brief)

Para `alert_only`/`paper` hace falta un **feed en tiempo real de ticks** (o de 1 s) de MNQ para armar velas de 15 s y 30 s. ⟦PENDIENTE: fuente en vivo y si tenés licencia de datos CME para uso no-display/API⟧. Sin esto el sistema solo puede backtestear.
