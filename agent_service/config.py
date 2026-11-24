"""Configuration defaults matching The5ers $2.5k 2-Step High Stakes rules."""

from dataclasses import dataclass


@dataclass(frozen=True)
class PropFirmConfig:
    """Hard risk parameters and safety buffers for The5ers $2.5k 2-Step."""

    initial_balance: float = 2500.0
    firm_max_total_loss_pct: float = 10.0   # The5ers static max DD: 10% ($2,250 floor)
    firm_max_daily_loss_pct: float = 5.0    # The5ers daily loss limit: 5%
    ea_max_total_loss_pct: float = 8.0      # Bot hard stop at 8% ($2,300 floor)
    ea_max_daily_loss_pct: float = 3.0      # Bot hard stop at 3% daily loss
    risk_per_trade_pct: float = 0.50        # Target risk per trade: 0.5%
    daily_profit_lock_pct: float = 1.50     # Lock green day at +1.5%
    max_trades_per_day: int = 4
    max_consecutive_losses: int = 2
    news_blackout_before_mins: int = 15     # The5ers bans +-2m; bot uses +-15m/10m buffer
    news_blackout_after_mins: int = 10
    bridge_host: str = "127.0.0.1"
    bridge_port: int = 8787
