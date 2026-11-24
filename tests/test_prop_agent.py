"""Unit and integration tests for guardrails, news filter, bridge, and backtester."""

import json
import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path

from agent_service.config import PropFirmConfig
from agent_service.guardrails import PropGuardrailEngine
from agent_service.news_regime import NewsEvent, check_news_blackout
from agent_service.server import BridgeState
from backtester.indicators import Bar, adx, atr, ema, rsi
from backtester.run_backtest import generate_sample_xauusd_m5
from backtester.simulator import run_simulation
from backtester.smc_strategy import SmcMomentumStrategy


class TestGuardrails(unittest.TestCase):
    def test_lot_sizing_caps_at_half_percent_risk(self) -> None:
        gr = PropGuardrailEngine(PropFirmConfig(initial_balance=2500.0, risk_per_trade_pct=0.5))
        # 0.5% of $2,500 = $12.50. With $4.00 SL on XAUUSD (100 oz/lot -> $400/lot), raw=0.03125 -> 0.03 lots
        lots = gr.calculate_xau_lots(balance=2500.0, sl_distance_dollars=4.0)
        self.assertEqual(lots, 0.03)

        # If SL is $15.00, 0.01 lots would risk $15.00 (> $12.50), so must return 0.0 (skip trade)
        too_wide = gr.calculate_xau_lots(balance=2500.0, sl_distance_dollars=15.0)
        self.assertEqual(too_wide, 0.0)

    def test_daily_drawdown_circuit_breaker(self) -> None:
        gr = PropGuardrailEngine(PropFirmConfig(initial_balance=2500.0, ea_max_daily_loss_pct=3.0))
        gr.rollover_day(date(2026, 11, 24), balance=2500.0, equity=2550.0)
        # Day start ref must be max(balance, equity) = 2550.0
        self.assertEqual(gr.state.day_start_ref, 2550.0)

        ok, _ = gr.evaluate(2500.0)
        self.assertTrue(ok)

        # 3% of 2550 = 76.50 -> equity 2473.50 triggers hard daily lock
        ok, reason = gr.evaluate(2473.50)
        self.assertFalse(ok)
        self.assertTrue(gr.state.daily_locked)
        self.assertIn("Daily drawdown cap hit", reason)

        # Next day resets daily lock
        gr.rollover_day(date(2026, 11, 25), balance=2473.50, equity=2473.50)
        self.assertFalse(gr.state.daily_locked)
        ok_next, _ = gr.evaluate(2473.50)
        self.assertTrue(ok_next)

    def test_static_floor_locks_account_permanently(self) -> None:
        gr = PropGuardrailEngine(PropFirmConfig(initial_balance=2500.0, ea_max_total_loss_pct=8.0))
        gr.rollover_day(date(2026, 11, 24), 2500.0, 2500.0)
        ok, reason = gr.evaluate(2300.0)
        self.assertFalse(ok)
        self.assertTrue(gr.state.account_locked)
        self.assertIn("Static drawdown cap hit", reason)

        # Even on a new day, account remains locked
        gr.rollover_day(date(2026, 11, 25), 2300.0, 2300.0)
        ok_next, _ = gr.evaluate(2350.0)
        self.assertFalse(ok_next)

    def test_consecutive_losses_and_daily_profit_lock(self) -> None:
        gr = PropGuardrailEngine(PropFirmConfig(initial_balance=2500.0, max_consecutive_losses=2, daily_profit_lock_pct=1.5))
        gr.rollover_day(date(2026, 11, 24), 2500.0, 2500.0)
        gr.record_trade_closed(-12.5)
        gr.record_trade_closed(-12.5)
        ok, reason = gr.evaluate(2475.0)
        self.assertFalse(ok)
        self.assertIn("Cooldown", reason)

        # New day, hit +1.5% ($37.50) profit lock
        gr.rollover_day(date(2026, 11, 25), 2475.0, 2475.0)
        gr.record_trade_closed(40.0)
        ok_profit, reason_profit = gr.evaluate(2515.0)
        self.assertFalse(ok_profit)
        self.assertIn("Daily profit lock", reason_profit)


class TestNewsAndBridge(unittest.TestCase):
    def test_news_blackout_window(self) -> None:
        ev = NewsEvent(
            title="US Non-Farm Payrolls",
            currency="USD",
            impact="HIGH",
            timestamp_utc=datetime(2026, 11, 6, 13, 30, tzinfo=timezone.utc),
        )
        # 10 mins before -> blocked (15m buffer)
        blocked, _ = check_news_blackout(datetime(2026, 11, 6, 13, 20, tzinfo=timezone.utc), [ev], 15, 10)
        self.assertTrue(blocked)
        # 20 mins after -> allowed (10m buffer)
        blocked_after, _ = check_news_blackout(datetime(2026, 11, 6, 13, 50, tzinfo=timezone.utc), [ev], 15, 10)
        self.assertFalse(blocked_after)

    def test_bridge_state_respects_kill_switch_and_news_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            news_path = Path(tmp) / "news.json"
            news_path.write_text(json.dumps([]), encoding="utf-8")
            state = BridgeState(news_file=news_path)
            res = state.evaluate_regime(use_ai=False)
            self.assertTrue(res["allow"])

            state.guardrails.state.manual_kill_switch = True
            halted = state.evaluate_regime(use_ai=False)
            self.assertFalse(halted["allow"])


class TestIndicatorsAndBacktest(unittest.TestCase):
    def test_indicators_produce_valid_values(self) -> None:
        bars = generate_sample_xauusd_m5(days=10, seed=7)
        closes = [b.close for b in bars]
        e = ema(closes, 20)
        r = rsi(closes, 14)
        a = atr(bars, 14)
        dx = adx(bars, 14)
        self.assertIsNotNone(e[-1])
        self.assertIsNotNone(r[-1])
        self.assertIsNotNone(a[-1])
        self.assertIsNotNone(dx[-1])
        self.assertTrue(0.0 <= r[-1] <= 100.0)  # type: ignore[operator]

    def test_sample_simulation_respects_the5ers_limits(self) -> None:
        bars = generate_sample_xauusd_m5(days=30, seed=42)
        for mode in ("smc", "momentum"):
            report = run_simulation(bars, strategy_mode=mode)
            self.assertFalse(report.the5ers_breached)
            self.assertLess(report.max_daily_dd_pct, 5.0)
            self.assertLess(report.max_static_dd_pct, 10.0)

    def test_smc_structure_bias_and_zones_detected(self) -> None:
        bars = generate_sample_xauusd_m5(days=15, seed=11)
        smc = SmcMomentumStrategy(bars)
        self.assertTrue(any(b != 0 for b in smc.htf_bias))
        self.assertTrue(any(len(z) > 0 for z in smc.zones_by_idx))


if __name__ == "__main__":
    unittest.main()
