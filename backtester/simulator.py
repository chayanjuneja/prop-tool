"""Prop-firm evaluation simulator enforcing The5ers $2.5k 2-Step constraints."""

from dataclasses import dataclass, field
from datetime import date
from typing import Dict, List, Optional

from agent_service.config import PropFirmConfig
from agent_service.guardrails import PropGuardrailEngine

from .indicators import Bar
from .smc_strategy import SmcConfig, SmcMomentumStrategy
from .strategy import MomentumPullbackStrategy, StrategyConfig


@dataclass
class OpenPosition:
    direction: str
    entry_price: float
    lots: float
    sl: float
    tp: float
    initial_sl_dist: float
    breakeven_done: bool = False


@dataclass
class ClosedTrade:
    direction: str
    entry_price: float
    exit_price: float
    lots: float
    net_pnl: float
    outcome: str  # "TP", "SL", "BE", "GUARDRAIL_CLOSE"


@dataclass
class BacktestReport:
    initial_balance: float
    final_balance: float
    net_profit: float
    return_pct: float
    total_trades: int
    wins: int
    losses: int
    breakevens: int
    win_rate_pct: float
    profit_factor: float
    max_static_dd_pct: float
    max_daily_dd_pct: float
    profitable_days_ge_05pct: int
    the5ers_breached: bool
    ea_account_locked: bool
    trades: List[ClosedTrade] = field(default_factory=list)


def run_simulation(
    m5_bars: List[Bar],
    prop_cfg: Optional[PropFirmConfig] = None,
    strat_cfg: Optional[StrategyConfig] = None,
    smc_cfg: Optional[SmcConfig] = None,
    strategy_mode: str = "smc",
    commission_per_lot_rt: float = 6.0,
    contract_size: float = 100.0,
) -> BacktestReport:
    """Runs bar-by-bar simulation with strict prop-firm equity drawdown tracking."""
    p_cfg = prop_cfg or PropFirmConfig()
    guardrails = PropGuardrailEngine(p_cfg)
    if strategy_mode == "momentum":
        s_cfg = strat_cfg or StrategyConfig()
        strategy = MomentumPullbackStrategy(m5_bars, s_cfg)
    else:
        s_cfg = smc_cfg or SmcConfig()
        strategy = SmcMomentumStrategy(m5_bars, s_cfg)

    balance = p_cfg.initial_balance
    pos: Optional[OpenPosition] = None
    trades: List[ClosedTrade] = []

    daily_closed_pnl: Dict[date, float] = {}
    max_static_dd_pct = 0.0
    max_daily_dd_pct = 0.0
    the5ers_breached = False

    def unrealized_pnl(p: OpenPosition, bar: Bar) -> float:
        if p.direction == "BUY":
            return (bar.close - p.entry_price) * p.lots * contract_size
        return (p.entry_price - bar.close) * p.lots * contract_size

    def worst_bar_equity(p: OpenPosition, bar: Bar) -> float:
        if p.direction == "BUY":
            floating = (bar.low - p.entry_price) * p.lots * contract_size
        else:
            floating = (p.entry_price - bar.high) * p.lots * contract_size
        return balance + floating

    for i, bar in enumerate(m5_bars):
        day = bar.timestamp.date()
        cur_eq = balance + (unrealized_pnl(pos, bar) if pos else 0.0)
        guardrails.rollover_day(day, balance, cur_eq)

        # 1. Check intra-bar position management & exits (worst-case SL checked first)
        if pos is not None:
            # Check worst intra-bar equity for drawdown stats
            worst_eq = worst_bar_equity(pos, bar)
            static_dd = max(0.0, (p_cfg.initial_balance - worst_eq) / p_cfg.initial_balance * 100.0)
            daily_dd = max(
                0.0,
                (guardrails.state.day_start_ref - worst_eq) / guardrails.state.day_start_ref * 100.0,
            )
            max_static_dd_pct = max(max_static_dd_pct, static_dd)
            max_daily_dd_pct = max(max_daily_dd_pct, daily_dd)

            if static_dd >= p_cfg.firm_max_total_loss_pct or daily_dd >= p_cfg.firm_max_daily_loss_pct:
                the5ers_breached = True

            # Hard guardrail check at bar close/worst price
            gr_ok, _ = guardrails.evaluate(worst_eq)
            exited = False

            if not gr_ok and (guardrails.state.daily_locked or guardrails.state.account_locked):
                exit_px = bar.close
                gross = (
                    (exit_px - pos.entry_price) if pos.direction == "BUY" else (pos.entry_price - exit_px)
                ) * pos.lots * contract_size
                net = gross - (pos.lots * commission_per_lot_rt)
                balance += net
                guardrails.record_trade_closed(net)
                daily_closed_pnl[day] = daily_closed_pnl.get(day, 0.0) + net
                trades.append(ClosedTrade(pos.direction, pos.entry_price, exit_px, pos.lots, net, "GUARDRAIL_CLOSE"))
                pos = None
                exited = True

            if not exited and pos is not None:
                half_spread = bar.spread_dollars / 2.0
                if pos.direction == "BUY":
                    hit_sl = bar.low <= pos.sl
                    hit_tp = bar.high >= pos.tp
                    if hit_sl:
                        exit_px = pos.sl
                        outcome = "BE" if pos.breakeven_done and pos.sl >= pos.entry_price else "SL"
                        net = (exit_px - pos.entry_price) * pos.lots * contract_size - (pos.lots * commission_per_lot_rt)
                        balance += net
                        guardrails.record_trade_closed(net)
                        daily_closed_pnl[day] = daily_closed_pnl.get(day, 0.0) + net
                        trades.append(ClosedTrade(pos.direction, pos.entry_price, exit_px, pos.lots, net, outcome))
                        pos = None
                    elif hit_tp:
                        exit_px = pos.tp
                        net = (exit_px - pos.entry_price) * pos.lots * contract_size - (pos.lots * commission_per_lot_rt)
                        balance += net
                        guardrails.record_trade_closed(net)
                        daily_closed_pnl[day] = daily_closed_pnl.get(day, 0.0) + net
                        trades.append(ClosedTrade(pos.direction, pos.entry_price, exit_px, pos.lots, net, "TP"))
                        pos = None
                    elif (
                        not pos.breakeven_done
                        and s_cfg.breakeven_trigger_r > 0
                        and (bar.high - pos.entry_price) >= pos.initial_sl_dist * s_cfg.breakeven_trigger_r
                    ):
                        pos.sl = pos.entry_price + 0.10
                        pos.breakeven_done = True
                else:
                    # Short closes on Ask (~ bar + spread)
                    ask_high = bar.high + bar.spread_dollars
                    ask_low = bar.low + bar.spread_dollars
                    hit_sl = ask_high >= pos.sl
                    hit_tp = ask_low <= pos.tp
                    if hit_sl:
                        exit_px = pos.sl
                        outcome = "BE" if pos.breakeven_done and pos.sl <= pos.entry_price else "SL"
                        net = (pos.entry_price - exit_px) * pos.lots * contract_size - (pos.lots * commission_per_lot_rt)
                        balance += net
                        guardrails.record_trade_closed(net)
                        daily_closed_pnl[day] = daily_closed_pnl.get(day, 0.0) + net
                        trades.append(ClosedTrade(pos.direction, pos.entry_price, exit_px, pos.lots, net, outcome))
                        pos = None
                    elif hit_tp:
                        exit_px = pos.tp
                        net = (pos.entry_price - exit_px) * pos.lots * contract_size - (pos.lots * commission_per_lot_rt)
                        balance += net
                        guardrails.record_trade_closed(net)
                        daily_closed_pnl[day] = daily_closed_pnl.get(day, 0.0) + net
                        trades.append(ClosedTrade(pos.direction, pos.entry_price, exit_px, pos.lots, net, "TP"))
                        pos = None
                    elif (
                        not pos.breakeven_done
                        and s_cfg.breakeven_trigger_r > 0
                        and (pos.entry_price - (bar.low + half_spread)) >= pos.initial_sl_dist * s_cfg.breakeven_trigger_r
                    ):
                        pos.sl = pos.entry_price - 0.10
                        pos.breakeven_done = True

        # 2. Check new entry at bar open if flat
        if pos is None:
            can_trade, _ = guardrails.evaluate(balance)
            if can_trade:
                sig = strategy.evaluate_bar_open(i)
                if sig is not None:
                    lots = guardrails.calculate_xau_lots(balance, sig.sl_distance, contract_size=contract_size)
                    if lots > 0:
                        if sig.direction == "BUY":
                            entry_px = bar.open + bar.spread_dollars
                            sl = entry_px - sig.sl_distance
                            tp = entry_px + sig.tp_distance
                        else:
                            entry_px = bar.open
                            sl = entry_px + sig.sl_distance
                            tp = entry_px - sig.tp_distance
                        pos = OpenPosition(sig.direction, entry_px, lots, sl, tp, sig.sl_distance)
                        guardrails.record_trade_opened()

    wins = sum(1 for t in trades if t.net_pnl > 0)
    losses = sum(1 for t in trades if t.net_pnl < 0)
    breakevens = sum(1 for t in trades if t.outcome == "BE")
    gross_profit = sum(t.net_pnl for t in trades if t.net_pnl > 0)
    gross_loss = abs(sum(t.net_pnl for t in trades if t.net_pnl < 0))
    pf = (gross_profit / gross_loss) if gross_loss > 0 else (float("inf") if gross_profit > 0 else 0.0)
    win_rate = (wins / len(trades) * 100.0) if trades else 0.0
    profitable_days = sum(1 for pnl in daily_closed_pnl.values() if pnl >= p_cfg.initial_balance * 0.005)
    net_profit = balance - p_cfg.initial_balance

    return BacktestReport(
        initial_balance=p_cfg.initial_balance,
        final_balance=round(balance, 2),
        net_profit=round(net_profit, 2),
        return_pct=round(net_profit / p_cfg.initial_balance * 100.0, 2),
        total_trades=len(trades),
        wins=wins,
        losses=losses,
        breakevens=breakevens,
        win_rate_pct=round(win_rate, 2),
        profit_factor=round(pf, 2),
        max_static_dd_pct=round(max_static_dd_pct, 2),
        max_daily_dd_pct=round(max_daily_dd_pct, 2),
        profitable_days_ge_05pct=profitable_days,
        the5ers_breached=the5ers_breached,
        ea_account_locked=guardrails.state.account_locked,
        trades=trades,
    )
