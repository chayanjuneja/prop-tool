"""Deterministic prop-firm guardrail engine shared by live bridge and backtester."""

import math
from dataclasses import dataclass, field
from datetime import date
from typing import Optional, Tuple

from .config import PropFirmConfig


@dataclass
class AccountRiskState:
    """Mutable daily and account-level risk state."""

    current_day: Optional[date] = None
    day_start_ref: float = 2500.0
    trades_today: int = 0
    realized_pnl_today: float = 0.0
    consecutive_losses_today: int = 0
    daily_locked: bool = False
    account_locked: bool = False
    lock_reason: str = ""
    manual_kill_switch: bool = False
    trade_log: list = field(default_factory=list)


class PropGuardrailEngine:
    """Enforces The5ers $2.5k 2-Step safety rules with conservative EA buffers."""

    def __init__(self, config: Optional[PropFirmConfig] = None) -> None:
        self.cfg = config or PropFirmConfig()
        self.state = AccountRiskState(day_start_ref=self.cfg.initial_balance)

    @property
    def static_equity_floor(self) -> float:
        return self.cfg.initial_balance * (1.0 - self.cfg.ea_max_total_loss_pct / 100.0)

    def rollover_day(self, day: date, balance: float, equity: float) -> None:
        """Resets daily counters at server midnight using max(balance, equity)."""
        if self.state.current_day == day:
            return
        self.state.current_day = day
        self.state.day_start_ref = max(balance, equity)
        self.state.trades_today = 0
        self.state.realized_pnl_today = 0.0
        self.state.consecutive_losses_today = 0
        self.state.daily_locked = False
        if not self.state.account_locked:
            self.state.lock_reason = ""

    def record_trade_opened(self) -> None:
        self.state.trades_today += 1

    def record_trade_closed(self, net_pnl: float) -> None:
        self.state.realized_pnl_today += net_pnl
        if net_pnl < 0:
            self.state.consecutive_losses_today += 1
        elif net_pnl > 0:
            self.state.consecutive_losses_today = 0

    def evaluate(self, equity: float) -> Tuple[bool, str]:
        """Returns (allow_new_trades, reason) based on current equity & daily stats."""
        if self.state.manual_kill_switch:
            return False, "Manual kill switch active"

        if self.state.account_locked:
            return False, self.state.lock_reason or "Account permanently locked"

        if equity <= self.static_equity_floor:
            self.state.account_locked = True
            self.state.lock_reason = (
                f"Static drawdown cap hit: equity ${equity:.2f} <= floor ${self.static_equity_floor:.2f}"
            )
            return False, self.state.lock_reason

        if self.state.daily_locked:
            return False, self.state.lock_reason or "Daily loss limit locked"

        if self.state.day_start_ref > 0:
            daily_dd_pct = (self.state.day_start_ref - equity) / self.state.day_start_ref * 100.0
            if daily_dd_pct >= self.cfg.ea_max_daily_loss_pct:
                self.state.daily_locked = True
                self.state.lock_reason = (
                    f"Daily drawdown cap hit: {daily_dd_pct:.2f}% >= {self.cfg.ea_max_daily_loss_pct:.2f}%"
                )
                return False, self.state.lock_reason

        if self.cfg.daily_profit_lock_pct > 0 and self.state.day_start_ref > 0:
            profit_target = self.state.day_start_ref * (self.cfg.daily_profit_lock_pct / 100.0)
            if self.state.realized_pnl_today >= profit_target:
                return False, f"Daily profit lock reached (+${self.state.realized_pnl_today:.2f})"

        if self.state.consecutive_losses_today >= self.cfg.max_consecutive_losses:
            return False, f"Cooldown after {self.state.consecutive_losses_today} consecutive losses"

        if self.state.trades_today >= self.cfg.max_trades_per_day:
            return False, f"Max daily trades reached ({self.state.trades_today}/{self.cfg.max_trades_per_day})"

        return True, "OK"

    def calculate_xau_lots(
        self,
        balance: float,
        sl_distance_dollars: float,
        contract_size: float = 100.0,
        min_lot: float = 0.01,
        max_lot: float = 50.0,
        lot_step: float = 0.01,
    ) -> float:
        """Calculates XAUUSD lots strictly capped at risk_per_trade_pct (floors to step)."""
        if balance <= 0 or sl_distance_dollars <= 0:
            return 0.0
        risk_money = balance * (self.cfg.risk_per_trade_pct / 100.0)
        loss_per_lot = sl_distance_dollars * contract_size
        if loss_per_lot <= 0:
            return 0.0
        raw_lots = risk_money / loss_per_lot
        stepped = math.floor(raw_lots / lot_step) * lot_step
        if stepped < min_lot:
            return 0.0
        return min(max_lot, round(stepped, 2))
