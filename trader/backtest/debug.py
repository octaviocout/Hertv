"""
Diagnostic script — run this to inspect what the SMC engine detects.

Usage:
    python -m trader.backtest.debug
"""

import warnings
warnings.filterwarnings("ignore")

import logging
logging.basicConfig(level=logging.WARNING)

from trader.data.feed import fetch_nq_es, align_bars, session_bars
from trader.smc.fvg import detect_fvgs
from trader.smc.smt import detect_smt_divergence, find_swing_lows, find_swing_highs
from trader.smc.structure import detect_structure
from trader.smc.liquidity import build_presession_levels


def run():
    print("Fetching NQ + ES data (1m, 5d)...")
    nq_1m, es_1m = fetch_nq_es("1m", period="5d")
    nq_df, es_df = align_bars(nq_1m, es_1m)
    nq_s = session_bars(nq_df)
    es_s = session_bars(es_df)
    common = nq_s.index.intersection(es_s.index)
    nq_s = nq_s.loc[common]
    es_s = es_s.loc[common]

    print(f"\n{'='*50}")
    print(f"  1m session bars: {len(nq_s)}")
    print(f"  Date range: {nq_s.index[0]} → {nq_s.index[-1]}")
    print(f"{'='*50}")

    # --- Swing detection ---
    nq_sl = find_swing_lows(nq_s["low"], lookback=3)
    es_sl = find_swing_lows(es_s["low"], lookback=3)
    nq_sh = find_swing_highs(nq_s["high"], lookback=3)
    es_sh = find_swing_highs(es_s["high"], lookback=3)
    print(f"\n[Swings] NQ lows={nq_sl.sum()} highs={nq_sh.sum()} | ES lows={es_sl.sum()} highs={es_sh.sum()}")

    # --- SMT detection ---
    smt_signals = detect_smt_divergence(nq_s, es_s, swing_lookback=3)
    print(f"\n[SMT] Signals found: {len(smt_signals)}")
    for s in smt_signals[:10]:
        print(f"  {s}")

    # --- FVG detection ---
    fvgs = detect_fvgs(nq_s, "1m", atr_multiplier=0.25)
    bullish = [f for f in fvgs if f.type.value == "bullish"]
    bearish = [f for f in fvgs if f.type.value == "bearish"]
    print(f"\n[FVG] Total={len(fvgs)} | Bullish={len(bullish)} Bearish={len(bearish)}")
    for f in fvgs[:5]:
        print(f"  {f}")

    # --- Structure ---
    structure = detect_structure(nq_s, "1m", swing_lookback=3)
    chochs = [e for e in structure if e.is_choch]
    bos = [e for e in structure if e.is_bos]
    print(f"\n[Structure] Total={len(structure)} | ChoCh={len(chochs)} BOS={len(bos)}")
    for e in chochs[:5]:
        print(f"  {e}")

    # --- Pre-session levels ---
    nq_5m, _ = fetch_nq_es("5m", period="5d")
    nq_15m, _ = fetch_nq_es("15m", period="5d")
    first_ts = nq_s.index[0]
    presession = build_presession_levels(
        df_15m=nq_15m.df,
        df_5m=nq_5m.df,
        session_date=first_ts,
        swing_lookback=3,
    )
    print(f"\n[Pre-session] Highs={len(presession.highs)} Lows={len(presession.lows)}")
    for l in presession.all_levels:
        print(f"  {l}")

    print(f"\n{'='*50}")
    print("If SMT > 0 and FVG > 0 and ChoCh > 0, the backtest should produce trades.")
    print("If SMT = 0, the swing detection or divergence logic needs adjustment.")
    print(f"{'='*50}\n")


if __name__ == "__main__":
    run()
