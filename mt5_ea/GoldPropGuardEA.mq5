//+------------------------------------------------------------------+
//|                                              GoldPropGuardEA.mq5 |
//|     High-Win-Rate XAUUSD Momentum Pullback + The5ers Guardrails  |
//+------------------------------------------------------------------+
#property copyright "Gold Prop Guard"
#property version   "1.00"
#property description "XAUUSD Trend + Momentum Pullback EA built for The5ers $2.5k 2-Step Rules"

#include <Trade\Trade.mqh>

//--- Inputs: Prop Firm Account & Hard Guardrails
input group "=== Prop Firm Guardrails (The5ers $2.5k Safe Defaults) ==="
input double InpInitialBalance      = 2500.0;   // Account initial balance ($)
input double InpRiskPerTradePct     = 0.50;     // Risk per trade (% of balance)
input double InpDailyLossCapPct     = 3.00;     // EA hard daily loss cap (% - The5ers limit is 5%)
input double InpTotalLossCapPct     = 8.00;     // EA hard static DD cap (% - The5ers limit is 10%)
input double InpDailyProfitLockPct  = 1.50;     // Stop trading for the day after +X% closed profit (0=off)
input int    InpMaxTradesPerDay     = 4;        // Max new entries per server day
input int    InpMaxConsecLosses     = 2;        // Pause for the day after N consecutive losses

//--- Inputs: Session, Spread & News Protection
input group "=== Execution, Session & News Filters ==="
input int    InpSessionStartHour    = 8;        // Session start hour (MT5 Server Time, e.g. London open)
input int    InpSessionEndHour      = 20;       // Session end hour (MT5 Server Time, e.g. NY mid/late)
input double InpMaxSpreadDollars    = 0.45;     // Max XAUUSD spread in $ (e.g. 0.45 = 45 cents)
input bool   InpUseMt5NewsFilter    = true;     // Block entries around High-Impact USD news
input int    InpNewsBeforeMins      = 15;       // Minutes before high-impact news to block new trades
input int    InpNewsAfterMins       = 10;       // Minutes after high-impact news to block new trades

//--- Inputs: Strategy Parameters (Trend + Momentum Pullback)
input group "=== XAUUSD Momentum Pullback Strategy ==="
input ENUM_TIMEFRAMES InpLtf        = PERIOD_M5;   // Execution timeframe
input ENUM_TIMEFRAMES InpHtf        = PERIOD_M15;  // Higher-timeframe trend filter
input int    InpLtfFastEma          = 20;       // LTF Fast EMA
input int    InpLtfSlowEma          = 50;       // LTF Slow EMA
input int    InpHtfFastEma          = 50;       // HTF Fast EMA
input int    InpHtfSlowEma          = 200;      // HTF Slow EMA
input int    InpAdxPeriod           = 14;       // HTF ADX period
input double InpMinAdx              = 20.0;     // Min HTF ADX to confirm trend
input int    InpRsiPeriod           = 14;       // LTF RSI period
input double InpRsiLongMin          = 45.0;     // Min RSI for Long momentum confirmation
input double InpRsiLongMax          = 68.0;     // Max RSI for Long (avoid buying exhausted tops)
input double InpRsiShortMin         = 32.0;     // Min RSI for Short (avoid selling exhausted bottoms)
input double InpRsiShortMax         = 55.0;     // Max RSI for Short momentum confirmation
input int    InpAtrPeriod           = 14;       // LTF ATR period
input double InpAtrSlMult           = 1.50;     // Stop Loss = ATR * multiplier
input double InpRewardRiskRatio     = 1.15;     // Take Profit = SL * R:R (high win-rate profile)
input double InpBreakevenTriggerR   = 0.75;     // Move SL to breakeven at +X * R (0=disabled)
input double InpMinAtrDollars       = 0.80;     // Skip dead/choppy market if M5 ATR < $0.80
input double InpMaxAtrDollars       = 12.00;    // Skip extreme spike volatility if M5 ATR > $12.00

//--- Inputs: Optional Python AI / Regime Bridge
input group "=== Optional Python AI / Regime Bridge ==="
input bool   InpUsePythonBridge     = false;    // Poll local Python regime server via WebRequest
input string InpBridgeUrl           = "http://127.0.0.1:8787/regime";
input bool   InpFailClosedOnBridge  = false;    // If true, block trades when bridge is unreachable
input int    InpBridgePollSecs      = 60;       // Bridge polling interval (seconds)

//--- Inputs: General
input ulong  InpMagicNumber         = 502501;   // Unique Magic Number for this EA

//--- Global State
CTrade   g_trade;
int      g_hLtfFastEma = INVALID_HANDLE;
int      g_hLtfSlowEma = INVALID_HANDLE;
int      g_hHtfFastEma = INVALID_HANDLE;
int      g_hHtfSlowEma = INVALID_HANDLE;
int      g_hHtfAdx     = INVALID_HANDLE;
int      g_hLtfRsi     = INVALID_HANDLE;
int      g_hLtfAtr     = INVALID_HANDLE;

datetime g_lastBarTime       = 0;
datetime g_currentDayDate    = 0;
double   g_dayStartRef       = 0.0;
bool     g_dailyLocked       = false;
bool     g_accountLocked     = false;
string   g_lockReason        = "";

datetime g_lastBridgePoll    = 0;
bool     g_bridgeAllowTrade  = true;
string   g_bridgeReason      = "Bridge disabled";

//+------------------------------------------------------------------+
//| Helper: GlobalVariable prefix for persistence across restarts    |
//+------------------------------------------------------------------+
string GvKey(const string suffix)
{
   return StringFormat("GPG_%I64u_%I64u_%s", AccountInfoInteger(ACCOUNT_LOGIN), InpMagicNumber, suffix);
}

datetime GetServerDateOnly(const datetime dt)
{
   MqlDateTime s;
   TimeToStruct(dt, s);
   s.hour = 0;
   s.min  = 0;
   s.sec  = 0;
   return StructToTime(s);
}

//+------------------------------------------------------------------+
//| Initialization                                                   |
//+------------------------------------------------------------------+
int OnInit()
{
   if(StringFind(_Symbol, "XAU") < 0 && StringFind(_Symbol, "GOLD") < 0)
      Print("Warning: GoldPropGuardEA is tuned for XAUUSD, currently attached to ", _Symbol);

   g_trade.SetExpertMagicNumber(InpMagicNumber);
   g_trade.SetDeviationInPoints(30);
   g_trade.SetTypeFillingBySymbol(_Symbol);

   g_hLtfFastEma = iMA(_Symbol, InpLtf, InpLtfFastEma, 0, MODE_EMA, PRICE_CLOSE);
   g_hLtfSlowEma = iMA(_Symbol, InpLtf, InpLtfSlowEma, 0, MODE_EMA, PRICE_CLOSE);
   g_hHtfFastEma = iMA(_Symbol, InpHtf, InpHtfFastEma, 0, MODE_EMA, PRICE_CLOSE);
   g_hHtfSlowEma = iMA(_Symbol, InpHtf, InpHtfSlowEma, 0, MODE_EMA, PRICE_CLOSE);
   g_hHtfAdx     = iADX(_Symbol, InpHtf, InpAdxPeriod);
   g_hLtfRsi     = iRSI(_Symbol, InpLtf, InpRsiPeriod, PRICE_CLOSE);
   g_hLtfAtr     = iATR(_Symbol, InpLtf, InpAtrPeriod);

   if(g_hLtfFastEma == INVALID_HANDLE || g_hLtfSlowEma == INVALID_HANDLE ||
      g_hHtfFastEma == INVALID_HANDLE || g_hHtfSlowEma == INVALID_HANDLE ||
      g_hHtfAdx     == INVALID_HANDLE || g_hLtfRsi     == INVALID_HANDLE ||
      g_hLtfAtr     == INVALID_HANDLE)
   {
      Print("Error creating indicator handles: ", GetLastError());
      return INIT_FAILED;
   }

   InitOrRolloverDay(true);

   if(GlobalVariableCheck(GvKey("ACC_LOCKED")))
      g_accountLocked = (GlobalVariableGet(GvKey("ACC_LOCKED")) > 0.5);

   return INIT_SUCCEEDED;
}

void OnDeinit(const int reason)
{
   if(g_hLtfFastEma != INVALID_HANDLE) IndicatorRelease(g_hLtfFastEma);
   if(g_hLtfSlowEma != INVALID_HANDLE) IndicatorRelease(g_hLtfSlowEma);
   if(g_hHtfFastEma != INVALID_HANDLE) IndicatorRelease(g_hHtfFastEma);
   if(g_hHtfSlowEma != INVALID_HANDLE) IndicatorRelease(g_hHtfSlowEma);
   if(g_hHtfAdx     != INVALID_HANDLE) IndicatorRelease(g_hHtfAdx);
   if(g_hLtfRsi     != INVALID_HANDLE) IndicatorRelease(g_hLtfRsi);
   if(g_hLtfAtr     != INVALID_HANDLE) IndicatorRelease(g_hLtfAtr);
   Comment("");
}

//+------------------------------------------------------------------+
//| Daily Rollover & Persistence (Matches 00:00 MT5 Server Time)     |
//+------------------------------------------------------------------+
void InitOrRolloverDay(const bool onInit)
{
   datetime today = GetServerDateOnly(TimeCurrent());

   if(onInit && GlobalVariableCheck(GvKey("DAY_DATE")))
   {
      datetime savedDate = (datetime)GlobalVariableGet(GvKey("DAY_DATE"));
      if(savedDate == today && GlobalVariableCheck(GvKey("DAY_REF")))
      {
         g_currentDayDate = savedDate;
         g_dayStartRef    = GlobalVariableGet(GvKey("DAY_REF"));
         g_dailyLocked    = GlobalVariableCheck(GvKey("DAY_LOCKED")) &&
                            (GlobalVariableGet(GvKey("DAY_LOCKED")) > 0.5);
         return;
      }
   }

   if(today != g_currentDayDate)
   {
      g_currentDayDate = today;
      // The5ers uses max(start-of-day balance, start-of-day equity)
      g_dayStartRef = MathMax(AccountInfoDouble(ACCOUNT_BALANCE), AccountInfoDouble(ACCOUNT_EQUITY));
      g_dailyLocked = false;
      g_lockReason  = "";

      GlobalVariableSet(GvKey("DAY_DATE"), (double)g_currentDayDate);
      GlobalVariableSet(GvKey("DAY_REF"),  g_dayStartRef);
      GlobalVariableSet(GvKey("DAY_LOCKED"), 0.0);
   }
}

//+------------------------------------------------------------------+
//| Close All EA Positions (Emergency Circuit Breaker)               |
//+------------------------------------------------------------------+
void CloseAllEaPositions(const string reason)
{
   Print("[GUARDRAIL] Closing all EA positions: ", reason);
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0) continue;
      if(PositionGetString(POSITION_SYMBOL) != _Symbol) continue;
      if((ulong)PositionGetInteger(POSITION_MAGIC) != InpMagicNumber) continue;

      if(!g_trade.PositionClose(ticket))
         Print("Failed to close ticket ", ticket, " err=", GetLastError());
   }
}

int CountOpenEaPositions()
{
   int count = 0;
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0) continue;
      if(PositionGetString(POSITION_SYMBOL) != _Symbol) continue;
      if((ulong)PositionGetInteger(POSITION_MAGIC) != InpMagicNumber) continue;
      count++;
   }
   return count;
}

//+------------------------------------------------------------------+
//| Count Today's Trades, Realized PnL, and Consecutive Losses       |
//+------------------------------------------------------------------+
void GetTodayStats(int &tradesOpened, double &realizedPnl, int &consecLosses)
{
   tradesOpened = 0;
   realizedPnl  = 0.0;
   consecLosses = 0;

   datetime dayStart = g_currentDayDate;
   if(!HistorySelect(dayStart, TimeCurrent() + 60))
      return;

   int total = HistoryDealsTotal();
   for(int i = 0; i < total; i++)
   {
      ulong dealTicket = HistoryDealGetTicket(i);
      if(dealTicket == 0) continue;
      if((ulong)HistoryDealGetInteger(dealTicket, DEAL_MAGIC) != InpMagicNumber) continue;
      if(HistoryDealGetString(dealTicket, DEAL_SYMBOL) != _Symbol) continue;

      ENUM_DEAL_ENTRY entryType = (ENUM_DEAL_ENTRY)HistoryDealGetInteger(dealTicket, DEAL_ENTRY);
      if(entryType == DEAL_ENTRY_IN)
      {
         tradesOpened++;
      }
      else if(entryType == DEAL_ENTRY_OUT || entryType == DEAL_ENTRY_INOUT)
      {
         double pnl = HistoryDealGetDouble(dealTicket, DEAL_PROFIT)
                    + HistoryDealGetDouble(dealTicket, DEAL_SWAP)
                    + HistoryDealGetDouble(dealTicket, DEAL_COMMISSION);
         realizedPnl += pnl;
         if(pnl < 0.0)
            consecLosses++;
         else if(pnl > 0.0)
            consecLosses = 0;
      }
   }
}

//+------------------------------------------------------------------+
//| Hard Guardrails Check (Runs Every Tick)                          |
//+------------------------------------------------------------------+
bool CheckHardGuardrails()
{
   double equity = AccountInfoDouble(ACCOUNT_EQUITY);

   // 1. Static Max Drawdown Floor (The5ers $2.5k 10% = $2250 -> EA stops at $2300)
   double staticFloor = InpInitialBalance * (1.0 - InpTotalLossCapPct / 100.0);
   if(equity <= staticFloor)
   {
      if(!g_accountLocked)
      {
         g_accountLocked = true;
         g_lockReason = StringFormat("STATIC DD FLOOR HIT ($%.2f <= $%.2f)", equity, staticFloor);
         GlobalVariableSet(GvKey("ACC_LOCKED"), 1.0);
         CloseAllEaPositions(g_lockReason);
      }
      return false;
   }
   if(g_accountLocked)
      return false;

   // 2. Daily Drawdown Limit (The5ers 5% -> EA hard stops at 3%)
   double dailyLossPct = (g_dayStartRef > 0.0) ? ((g_dayStartRef - equity) / g_dayStartRef * 100.0) : 0.0;
   if(dailyLossPct >= InpDailyLossCapPct)
   {
      if(!g_dailyLocked)
      {
         g_dailyLocked = true;
         g_lockReason = StringFormat("DAILY DD CAP HIT (%.2f%% >= %.2f%%)", dailyLossPct, InpDailyLossCapPct);
         GlobalVariableSet(GvKey("DAY_LOCKED"), 1.0);
         CloseAllEaPositions(g_lockReason);
      }
      return false;
   }

   if(g_dailyLocked)
      return false;

   // 3. Daily Profit Lock / Trade Cap / Consecutive Loss Cooldown (for new entries)
   int tradesToday = 0, consecLosses = 0;
   double realizedPnl = 0.0;
   GetTodayStats(tradesToday, realizedPnl, consecLosses);

   if(InpDailyProfitLockPct > 0.0)
   {
      double targetMoney = g_dayStartRef * (InpDailyProfitLockPct / 100.0);
      if(realizedPnl >= targetMoney)
      {
         g_lockReason = StringFormat("Daily profit target locked (+$%.2f)", realizedPnl);
         return false;
      }
   }

   if(InpMaxConsecLosses > 0 && consecLosses >= InpMaxConsecLosses)
   {
      g_lockReason = StringFormat("Cooldown: %d consecutive losses today", consecLosses);
      return false;
   }

   if(InpMaxTradesPerDay > 0 && tradesToday >= InpMaxTradesPerDay)
   {
      g_lockReason = StringFormat("Max daily trades reached (%d/%d)", tradesToday, InpMaxTradesPerDay);
      return false;
   }

   return true;
}

//+------------------------------------------------------------------+
//| Breakeven Trade Management                                       |
//+------------------------------------------------------------------+
void ManageOpenPositions()
{
   if(InpBreakevenTriggerR <= 0.0)
      return;

   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0) continue;
      if(PositionGetString(POSITION_SYMBOL) != _Symbol) continue;
      if((ulong)PositionGetInteger(POSITION_MAGIC) != InpMagicNumber) continue;

      ENUM_POSITION_TYPE posType = (ENUM_POSITION_TYPE)PositionGetInteger(POSITION_TYPE);
      double openPrice = PositionGetDouble(POSITION_PRICE_OPEN);
      double sl        = PositionGetDouble(POSITION_SL);
      double tp        = PositionGetDouble(POSITION_TP);
      if(sl <= 0.0 || tp <= 0.0) continue;

      double totalTargetDist = MathAbs(tp - openPrice);
      double slDistEst       = totalTargetDist / InpRewardRiskRatio;
      double triggerDist     = slDistEst * InpBreakevenTriggerR;

      double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
      double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
      int digits = (int)SymbolInfoInteger(_Symbol, SYMBOL_DIGITS);
      double beBuffer = 0.10; // $0.10 past entry to help cover spread/commissions

      if(posType == POSITION_TYPE_BUY)
      {
         if((bid - openPrice) >= triggerDist && sl < openPrice)
         {
            double newSl = NormalizeDouble(openPrice + beBuffer, digits);
            if(newSl < bid)
               g_trade.PositionModify(ticket, newSl, tp);
         }
      }
      else if(posType == POSITION_TYPE_SELL)
      {
         if((openPrice - ask) >= triggerDist && (sl > openPrice || sl == 0.0))
         {
            double newSl = NormalizeDouble(openPrice - beBuffer, digits);
            if(newSl > ask)
               g_trade.PositionModify(ticket, newSl, tp);
         }
      }
   }
}

//+------------------------------------------------------------------+
//| News Filter via MT5 Built-in Economic Calendar                   |
//+------------------------------------------------------------------+
bool IsHighImpactNewsWindow()
{
   if(!InpUseMt5NewsFilter)
      return false;

   // Built-in CalendarValueHistory is unavailable in Strategy Tester
   if(MQLInfoInteger(MQL_TESTER))
      return false;

   datetime now = TimeCurrent();
   datetime fromTime = now - InpNewsAfterMins * 60;
   datetime toTime   = now + InpNewsBeforeMins * 60;

   MqlCalendarValue values[];
   if(!CalendarValueHistory(values, fromTime, toTime, NULL, "USD"))
      return false;

   for(int i = 0; i < ArraySize(values); i++)
   {
      MqlCalendarEvent ev;
      if(CalendarEventById(values[i].event_id, ev))
      {
         if(ev.importance == CALENDAR_IMPORTANCE_HIGH)
            return true;
      }
   }
   return false;
}

//+------------------------------------------------------------------+
//| Optional Local Python AI/Regime Bridge Poll                      |
//+------------------------------------------------------------------+
void PollPythonBridge()
{
   if(!InpUsePythonBridge || MQLInfoInteger(MQL_TESTER))
   {
      g_bridgeAllowTrade = true;
      return;
   }

   datetime now = TimeCurrent();
   if(now - g_lastBridgePoll < InpBridgePollSecs)
      return;
   g_lastBridgePoll = now;

   char postData[];
   char result[];
   string resultHeaders;
   ResetLastError();
   int code = WebRequest("GET", InpBridgeUrl, "", 2000, postData, result, resultHeaders);
   if(code == 200)
   {
      string body = CharArrayToString(result);
      g_bridgeAllowTrade = (StringFind(body, "\"allow\":true") >= 0 || StringFind(body, "\"allow\": true") >= 0);
      g_bridgeReason = g_bridgeAllowTrade ? "Bridge: ALLOW" : "Bridge: HALT";
   }
   else
   {
      g_bridgeAllowTrade = !InpFailClosedOnBridge;
      g_bridgeReason = StringFormat("Bridge unreachable (HTTP %d, err %d)", code, GetLastError());
   }
}

//+------------------------------------------------------------------+
//| Session & Spread Filter                                          |
//+------------------------------------------------------------------+
bool IsSessionAndSpreadValid()
{
   MqlDateTime tm;
   TimeToStruct(TimeCurrent(), tm);
   if(tm.hour < InpSessionStartHour || tm.hour >= InpSessionEndHour)
      return false;

   double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
   double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
   if((ask - bid) > InpMaxSpreadDollars)
      return false;

   return true;
}

//+------------------------------------------------------------------+
//| Exact 0.5% Risk Position Sizing for XAUUSD                       |
//+------------------------------------------------------------------+
double CalculateLotSize(const double slDistance)
{
   if(slDistance <= 0.0)
      return 0.0;

   double balance   = AccountInfoDouble(ACCOUNT_BALANCE);
   double riskMoney = balance * (InpRiskPerTradePct / 100.0);

   double tickSize  = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE);
   double tickValue = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_VALUE_LOSS);
   if(tickValue <= 0.0)
      tickValue = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_VALUE);

   if(tickSize <= 0.0 || tickValue <= 0.0)
      return 0.0;

   double lossPerLot = (slDistance / tickSize) * tickValue;
   if(lossPerLot <= 0.0)
      return 0.0;

   double rawLots = riskMoney / lossPerLot;
   double minLot  = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
   double maxLot  = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MAX);
   double step    = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);

   double stepped = MathFloor(rawLots / step) * step;
   if(stepped < minLot)
      return 0.0; // Skip trade rather than over-risking above 0.5% on a small account

   return MathMin(maxLot, NormalizeDouble(stepped, 2));
}

//+------------------------------------------------------------------+
//| Helper to Read Closed Bar Buffer Values                          |
//+------------------------------------------------------------------+
bool GetBufferValue(const int handle, const int bufferIdx, const int shift, double &val)
{
   double buf[1];
   if(CopyBuffer(handle, bufferIdx, shift, 1, buf) != 1)
      return false;
   val = buf[0];
   return true;
}

//+------------------------------------------------------------------+
//| Main Tick Handler                                                |
//+------------------------------------------------------------------+
void OnTick()
{
   InitOrRolloverDay(false);

   // Hard guardrails run on EVERY tick
   bool guardrailsOk = CheckHardGuardrails();

   // Always manage breakeven on open positions
   ManageOpenPositions();

   PollPythonBridge();

   // Update chart dashboard
   Comment(StringFormat(
      "GoldPropGuardEA | Ref: $%.2f | Equity: $%.2f | Guardrails: %s | Bridge: %s",
      g_dayStartRef,
      AccountInfoDouble(ACCOUNT_EQUITY),
      guardrailsOk ? "OK" : ("LOCKED - " + g_lockReason),
      g_bridgeReason
   ));

   if(!guardrailsOk)
      return;

   // Only 1 open trade at a time
   if(CountOpenEaPositions() > 0)
      return;

   // Evaluate new entries strictly on new LTF bar open (using closed bar [1])
   datetime barTime = iTime(_Symbol, InpLtf, 0);
   if(barTime == 0 || barTime == g_lastBarTime)
      return;
   g_lastBarTime = barTime;

   if(!g_bridgeAllowTrade)
      return;

   if(!IsSessionAndSpreadValid())
      return;

   if(IsHighImpactNewsWindow())
   {
      Print("[NEWS GUARD] Skipping bar due to high-impact USD news window.");
      return;
   }

   //--- Read HTF Trend & Strength (closed HTF bar [1])
   double htfFast, htfSlow, htfAdx;
   if(!GetBufferValue(g_hHtfFastEma, 0, 1, htfFast) ||
      !GetBufferValue(g_hHtfSlowEma, 0, 1, htfSlow) ||
      !GetBufferValue(g_hHtfAdx,     0, 1, htfAdx))
      return;

   if(htfAdx < InpMinAdx)
      return;

   bool htfBull = (htfFast > htfSlow);
   bool htfBear = (htfFast < htfSlow);

   //--- Read LTF Momentum Pullback Setup (closed bars [1] and [2])
   double ltfFast1, ltfSlow1, rsi1, rsi2, atr1;
   if(!GetBufferValue(g_hLtfFastEma, 0, 1, ltfFast1) ||
      !GetBufferValue(g_hLtfSlowEma, 0, 1, ltfSlow1) ||
      !GetBufferValue(g_hLtfRsi,     0, 1, rsi1)     ||
      !GetBufferValue(g_hLtfRsi,     0, 2, rsi2)     ||
      !GetBufferValue(g_hLtfAtr,     0, 1, atr1))
      return;

   if(atr1 < InpMinAtrDollars || atr1 > InpMaxAtrDollars)
      return;

   double open1  = iOpen(_Symbol, InpLtf, 1);
   double close1 = iClose(_Symbol, InpLtf, 1);
   double high1  = iHigh(_Symbol, InpLtf, 1);
   double low1   = iLow(_Symbol, InpLtf, 1);
   double close2 = iClose(_Symbol, InpLtf, 2);

   // Long: HTF Uptrend + LTF EMA stack bullish + pullback touched fast EMA zone + bullish reclaim + healthy RSI rising
   bool longPullback  = (low1 <= ltfFast1 * 1.0005 && low1 >= ltfSlow1 * 0.9985);
   bool longTrigger   = (close1 > open1 && close1 > ltfFast1 && close2 <= ltfFast1 * 1.001);
   bool longMomentum  = (rsi1 >= InpRsiLongMin && rsi1 <= InpRsiLongMax && rsi1 > rsi2);

   // Short: HTF Downtrend + LTF EMA stack bearish + pullback touched fast EMA zone + bearish rejection + healthy RSI falling
   bool shortPullback = (high1 >= ltfFast1 * 0.9995 && high1 <= ltfSlow1 * 1.0015);
   bool shortTrigger  = (close1 < open1 && close1 < ltfFast1 && close2 >= ltfFast1 * 0.999);
   bool shortMomentum = (rsi1 >= InpRsiShortMin && rsi1 <= InpRsiShortMax && rsi1 < rsi2);

   double slDist = atr1 * InpAtrSlMult;
   double tpDist = slDist * InpRewardRiskRatio;
   double lots   = CalculateLotSize(slDist);
   if(lots <= 0.0)
      return;

   int digits = (int)SymbolInfoInteger(_Symbol, SYMBOL_DIGITS);

   if(htfBull && ltfFast1 > ltfSlow1 && longPullback && longTrigger && longMomentum)
   {
      double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
      double sl  = NormalizeDouble(ask - slDist, digits);
      double tp  = NormalizeDouble(ask + tpDist, digits);
      g_trade.Buy(lots, _Symbol, ask, sl, tp, "GoldMomentum_Long");
   }
   else if(htfBear && ltfFast1 < ltfSlow1 && shortPullback && shortTrigger && shortMomentum)
   {
      double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
      double sl  = NormalizeDouble(bid + slDist, digits);
      double tp  = NormalizeDouble(bid - tpDist, digits);
      g_trade.Sell(lots, _Symbol, bid, sl, tp, "GoldMomentum_Short");
   }
}
//+------------------------------------------------------------------+
