# GoldPropGuard — XAUUSD Prop-Firm Trading System (The5ers $2.5k 2-Step)

A risk-first automated trading system built around **The5ers $2,500 2-Step High Stakes** rules on **MetaTrader 5 (MT5)** for **XAUUSD (Gold)**.

> **Important:** Automated trading involves substantial risk. Never deploy this on a paid prop-firm challenge until you have backtested on real broker tick data in MT5 Strategy Tester and forward-tested on a free demo account.

---

## Project Structure

- [`mt5_ea/GoldSMCPropEA.mq5`](mt5_ea/GoldSMCPropEA.mq5): **Recommended** LuxAlgo-style Smart Money Concepts EA (M15 BOS/CHoCH + M5 Order Blocks & Fair Value Gaps + chart boxes + all The5ers `$2.5k` guardrails).
- [`mt5_ea/GoldPropGuardEA.mq5`](mt5_ea/GoldPropGuardEA.mq5): M5/M15 EMA/ADX/RSI momentum-pullback alternative EA with the same hard guardrails.
- [`agent_service/`](agent_service/): Optional local Python HTTP bridge (`http://127.0.0.1:8787`) for external USD news blackout schedules, manual kill switch, and optional Gemini AI macro-shock veto.
- [`backtester/`](backtester/): Pure-Python simulator (`--strategy smc` or `--strategy momentum`) for testing MT5 exported `XAUUSD` M5 CSV history against The5ers rules.
- [`tests/test_prop_agent.py`](tests/test_prop_agent.py): Unit tests covering risk sizing, drawdown locks, news blackouts, and simulator invariants.

---

## Built-In Guardrails (The5ers $2.5k 2-Step Alignment)

| Rule | The5ers Hard Limit | EA Guardrail Default | Behavior on Trigger |
| :--- | :--- | :--- | :--- |
| **Risk per Trade** | Flexible | **0.50%** of balance | Auto-calculated XAUUSD lot size; skips trade if min `0.01` lot exceeds 0.50% risk |
| **Daily Drawdown** | 5% of `max(balance, equity)` at `00:00` server time | **3.00%** | Closes all EA trades immediately and locks until next server day (persisted via `GlobalVariable`) |
| **Static Max Drawdown** | 10% (`$2,250` floor) | **8.00%** (`$2,300` floor) | Closes all EA trades and permanently locks EA until manual reset |
| **News Restriction** | No open/close ±2 min around High-Impact news | **-15m / +10m** buffer | Blocks new entries using MT5 Economic Calendar (`USD`) + optional Python calendar |
| **Daily Overtrading** | — | **Max 4 trades/day**, **2 consec. losses cooldown**, **+1.5% daily profit lock** | Prevents revenge-trading or giving back green days |
| **Spread & Volatility** | — | Max spread **$0.45**, ATR bounds `$0.80–$12.00` | Avoids rollover/news spread spikes |

---

## Step 1: Test Locally in Python (Backtester + Unit Tests)

From `/Users/mac/.gemini/antigravity/scratch/gold-prop-agent`:

```bash
# Run unit tests
python3 -m unittest discover -s tests -v

# Run sample simulation (synthetic data)
python3 -m backtester.run_backtest --days 45

# Run on real M5 bars exported from MT5 (View -> Symbols -> Bars -> Export Bars)
python3 -m backtester.run_backtest --csv /path/to/XAUUSD_M5.csv
```

---

## Step 2: Compile & Backtest in MetaTrader 5 on macOS

1. Open **MetaTrader 5** on your Mac.
2. Open **MetaEditor** (`Tools -> MetaQuotes Language Editor` or `F4`).
3. Copy [`mt5_ea/GoldPropGuardEA.mq5`](mt5_ea/GoldPropGuardEA.mq5) into MT5's `MQL5/Experts/` folder and click **Compile** (`F7`) — confirm `0 errors`.
4. Open **Strategy Tester** (`View -> Strategy Tester`):
   - **Expert:** `GoldPropGuardEA`
   - **Symbol:** `XAUUSD`
   - **Timeframe:** `M5`
   - **Deposit:** `2500` USD
   - **Modeling:** `Every tick based on real ticks`
   - **Date range:** At least 6–12 months of recent Gold data.
5. **Tuning tip:** Compare `InpBreakevenTriggerR = 0.0` (pure 1:1.15 bracket) vs `0.85`–`0.95` on real XAUUSD ticks—moving SL to breakeven too early (`0.75R`) can clip valid winners on Gold pullbacks.

---

## Step 3: Optional Python AI / News Bridge

If you want the EA to also poll the Python regime & kill-switch service:

1. Copy [`agent_service/news_events.sample.json`](agent_service/news_events.sample.json) to `agent_service/news_events.json` and keep high-impact USD events updated.
2. (Optional) Export a `GEMINI_API_KEY` environment variable if you want AI macro-shock veto checks:
   ```bash
   export GEMINI_API_KEY="your-key"
   python3 -m agent_service.server
   ```
3. In MT5: `Tools -> Options -> Expert Advisors -> Allow WebRequest for listed URL` and add `http://127.0.0.1:8787`.
4. Set `InpUsePythonBridge = true` in the EA inputs.
5. Emergency remote kill switch at any time:
   ```bash
   curl -X POST http://127.0.0.1:8787/kill -H "Content-Type: application/json" -d '{"halt": true}'
   ```

---

## Step 4: Forward Test & Move to Windows VPS

1. Run the compiled EA on a **free MT5 Demo account** ($2,500 balance) for 2–4 weeks during London/NY sessions to verify fills, spreads, and news blackouts.
2. When ready for 24/5 execution without keeping your Mac awake, move the compiled `.ex5` (and optional Python service) to a low-latency **Windows VPS** near your broker's server.
