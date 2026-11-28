"""CLI runner for backtesting GoldPropGuard on MT5 CSV exports or sample XAUUSD data."""

import argparse
import csv
import math
import random
from datetime import datetime, timedelta
from pathlib import Path
from typing import List

from .indicators import Bar
from .simulator import run_simulation


def load_mt5_csv(csv_path: Path) -> List[Bar]:
    """Loads standard CSV or MT5 tab-delimited M5 OHLC history."""
    bars: List[Bar] = []
    with csv_path.open("r", encoding="utf-8") as f:
        sample = f.read(2048)
        f.seek(0)
        delimiter = "\t" if "\t" in sample else ","
        reader = csv.DictReader(f, delimiter=delimiter)
        for row in reader:
            norm = {k.strip("<> ").lower(): v.strip() for k, v in row.items() if k}
            if "date" in norm and "time" in norm:
                dt_str = f"{norm['date'].replace('.', '-')} {norm['time']}"
                dt = datetime.fromisoformat(dt_str)
            elif "timestamp" in norm:
                dt = datetime.fromisoformat(norm["timestamp"].replace("Z", ""))
            elif "datetime" in norm:
                dt = datetime.fromisoformat(norm["datetime"].replace("Z", ""))
            else:
                raise ValueError(f"Unsupported CSV columns: {list(norm.keys())}")

            spread_pts = float(norm.get("spread", 25))
            # MT5 exports spread in points (2-digit XAUUSD: 25 pts = $0.25)
            spread_dollars = spread_pts * 0.01 if spread_pts > 2.0 else spread_pts
            vol_val = float(norm.get("volume", norm.get("tickvol", norm.get("vol", 0.0))) or 0.0)
            bars.append(
                Bar(
                    timestamp=dt,
                    open=float(norm["open"]),
                    high=float(norm["high"]),
                    low=float(norm["low"]),
                    close=float(norm["close"]),
                    spread_dollars=spread_dollars,
                    volume=vol_val,
                )
            )
    return bars


def generate_sample_xauusd_m5(days: int = 45, seed: int = 42) -> List[Bar]:
    """Generates deterministic trending/pullback M5 Gold bars for smoke-testing."""
    rng = random.Random(seed)
    bars: List[Bar] = []
    start = datetime(2026, 9, 1, 0, 0)
    price = 2650.0
    total_bars = days * 288

    for i in range(total_bars):
        ts = start + timedelta(minutes=5 * i)
        if ts.weekday() >= 5:
            continue
        # Regime drift + pullback cycles during London/NY
        in_session = 8 <= ts.hour < 20
        prime_ny = 12 <= ts.hour < 17
        macro_trend = 0.06 * math.sin(i / 420.0) + 0.03 * math.cos(i / 170.0)
        pullback_wave = -0.18 * math.sin(i / 18.0) if in_session else 0.0
        vol = 1.65 if in_session else 0.55
        delta = macro_trend + pullback_wave + rng.gauss(0.0, vol * 0.65)
        o = price
        c = max(1800.0, o + delta)
        wick_up = abs(rng.gauss(0.0, vol * 0.55))
        wick_dn = abs(rng.gauss(0.0, vol * 0.55))
        h = max(o, c) + wick_up
        l = min(o, c) - wick_dn
        base_vol = 1100.0 if prime_ny else (550.0 if in_session else 260.0)
        bar_vol = max(50.0, base_vol * (1.0 + abs(delta) / 2.5) * rng.uniform(0.6, 1.5))
        bars.append(Bar(timestamp=ts, open=o, high=h, low=l, close=c, spread_dollars=0.25, volume=round(bar_vol, 0)))
        price = c
    return bars


def main() -> None:
    parser = argparse.ArgumentParser(description="Backtest GoldPropGuardEA on XAUUSD M5 bars.")
    parser.add_argument("--csv", type=Path, help="Path to MT5 exported XAUUSD M5 CSV/TSV file")
    parser.add_argument("--days", type=int, default=45, help="Sample days if --csv is omitted")
    parser.add_argument("--strategy", choices=["smc", "momentum"], default="smc", help="Strategy mode")
    args = parser.parse_args()

    if args.csv:
        bars = load_mt5_csv(args.csv)
        source_label = str(args.csv)
    else:
        bars = generate_sample_xauusd_m5(days=args.days)
        source_label = f"Built-in synthetic XAUUSD sample ({args.days} days, {len(bars)} M5 bars)"

    report = run_simulation(bars, strategy_mode=args.strategy)
    print(f"=== GoldPropGuard Backtest Report [{args.strategy.upper()}] ({source_label}) ===")
    print(f"Initial Balance : ${report.initial_balance:,.2f}")
    print(f"Final Balance   : ${report.final_balance:,.2f} ({report.return_pct:+.2f}%)")
    print(f"Total Trades    : {report.total_trades} (W:{report.wins} / L:{report.losses} / BE:{report.breakevens})")
    print(f"Win Rate        : {report.win_rate_pct:.2f}%")
    print(f"Profit Factor   : {report.profit_factor:.2f}")
    print(f"Max Daily DD    : {report.max_daily_dd_pct:.2f}% (The5ers limit: 5.00%, EA cap: 3.00%)")
    print(f"Max Static DD   : {report.max_static_dd_pct:.2f}% (The5ers limit: 10.00%, EA cap: 8.00%)")
    print(f"Profitable Days : {report.profitable_days_ge_05pct} (days >= +0.5% closed profit; min 3 needed)")
    print(f"The5ers Breach  : {'YES - FAILED' if report.the5ers_breached else 'NO (SAFE)'}")
    print(f"EA Acc Locked   : {'YES' if report.ea_account_locked else 'NO'}")


if __name__ == "__main__":
    main()
