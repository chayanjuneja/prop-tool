//+------------------------------------------------------------------+
//|                                                GoldSMCPropEA.mq5 |
//|   LuxAlgo-Style Smart Money Concepts (BOS/CHoCH + OB + FVG)      |
//|   Engineered for XAUUSD & The5ers $2.5k 2-Step Guardrails        |
//+------------------------------------------------------------------+
#property copyright "Gold Prop Guard"
#property version   "1.00"
#property description "Non-repainting M15 BOS/CHoCH + M5 OB/FVG pullback EA with The5ers $2.5k guardrails"

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
input int    InpSessionStartHour    = 8;        // Session start hour (MT5 Server Time)
input int    InpSessionEndHour      = 20;       // Session end hour (MT5 Server Time)
input double InpMaxSpreadDollars    = 0.45;     // Max XAUUSD spread in $
input bool   InpUseMt5NewsFilter    = true;     // Block entries around High-Impact USD news
input int    InpNewsBeforeMins      = 15;       // Minutes before high-impact news to block
input int    InpNewsAfterMins       = 10;       // Minutes after high-impact news to block

//--- Inputs: LuxAlgo-Style SMC Parameters
input group "=== SMC Structure, Order Blocks & FVG ==="
input ENUM_TIMEFRAMES InpLtf        = PERIOD_M5;   // Execution timeframe
input ENUM_TIMEFRAMES InpHtf        = PERIOD_M15;  // Higher-timeframe structure (BOS/CHoCH)
input int    InpHtfSwingLen         = 5;        // M15 Swing pivot length (confirmed right-side)
input double InpMinFvgAtrRatio      = 0.20;     // Min FVG height as multiple of ATR
input double InpObDisplacementAtr   = 1.10;     // Min candle body multiple of ATR for OB
input int    InpObLookbackBars      = 6;        // Bars to scan back for opposing OB candle
input int    InpZoneTtlMinutes      = 225;      // Expire unmitigated OB/FVG after N mins (~45 M5 bars)
input int    InpAtrPeriod           = 14;       // M5 ATR period
input double InpMinAtrDollars       = 0.80;     // Skip dead chop if ATR < $0.80
input double InpMaxAtrDollars       = 8.50;     // Skip extreme volatility (keeps 0.01 lot <= 0.5%)
input int    InpRsiPeriod           = 14;       // M5 RSI confirmation period
input double InpSlAtrBuffer         = 0.35;     // SL padding beyond OB/FVG boundary
input double InpMinSlAtrMult        = 1.10;     // Minimum SL distance (ATR multiple)
input double InpMaxSlAtrMult        = 2.20;     // Maximum SL distance (ATR multiple)
input double InpRewardRiskRatio     = 1.15;     // Take Profit = SL * R:R (high win-rate profile)
input double InpBreakevenTriggerR   = 0.00;     // Move SL to breakeven at +X * R (0=off; avoids clipping Gold pullbacks)
input bool   InpDrawZones           = true;     // Draw OB & FVG boxes on chart

//--- Inputs: Optional Python AI / Regime Bridge
input group "=== Optional Python AI / Regime Bridge ==="
input bool   InpUsePythonBridge     = false;
input string InpBridgeUrl           = "http://127.0.0.1:8787/regime";
input bool   InpFailClosedOnBridge  = false;
input int    InpBridgePollSecs      = 60;

input ulong  InpMagicNumber         = 502502;   // Unique Magic Number

//--- Structures
struct SmcZone
{
   int      dir;      // +1 = Bullish, -1 = Bearish
   string   kind;     // "OB" or "FVG"
   double   low;
   double   high;
   datetime created;
   string   objName;
};

//--- Globals
CTrade   g_trade;
int      g_hLtfAtr = INVALID_HANDLE;
int      g_hLtfRsi = INVALID_HANDLE;

datetime g_lastLtfBar        = 0;
datetime g_lastHtfBar        = 0;
datetime g_currentDayDate    = 0;
double   g_dayStartRef       = 0.0;
bool     g_dailyLocked       = false;
bool     g_accountLocked     = false;
string   g_lockReason        = "";

//--- M15 Structure State
double   g_htfSwingHigh      = 0.0;
double   g_htfSwingLow       = 0.0;
int      g_htfBias           = 0;       // +1 Bullish, -1 Bearish, 0 Neutral
string   g_lastStructureTag  = "NEUTRAL";

//--- Active M5 Zones
SmcZone  g_zones[];
int      g_zoneSeq           = 0;

//--- Bridge State
datetime g_lastBridgePoll    = 0;
bool     g_bridgeAllowTrade  = true;
string   g_bridgeReason      = "Bridge disabled";

string GvKey(const string suffix)
{
   return StringFormat("GSM_%I64u_%I64u_%s", AccountInfoInteger(ACCOUNT_LOGIN), InpMagicNumber, suffix);
}

datetime GetServerDateOnly(const datetime dt)
{
   MqlDateTime s;
   TimeToStruct(dt, s);
   s.hour = 0; s.min = 0; s.sec = 0;
   return StructToTime(s);
}

//+------------------------------------------------------------------+
//| Initialization                                                   |
//+------------------------------------------------------------------+
int OnInit()
{
   g_trade.SetExpertMagicNumber(InpMagicNumber);
   g_trade.SetDeviationInPoints(30);
   g_trade.SetTypeFillingBySymbol(_Symbol);

   g_hLtfAtr = iATR(_Symbol, InpLtf, InpAtrPeriod);
   g_hLtfRsi = iRSI(_Symbol, InpLtf, InpRsiPeriod, PRICE_CLOSE);
   if(g_hLtfAtr == INVALID_HANDLE || g_hLtfRsi == INVALID_HANDLE)
   {
      Print("Error creating indicator handles: ", GetLastError());
      return INIT_FAILED;
   }

   InitOrRolloverDay(true);
   if(GlobalVariableCheck(GvKey("ACC_LOCKED")))
      g_accountLocked = (GlobalVariableGet(GvKey("ACC_LOCKED")) > 0.5);

   SeedHtfStructure();
   return INIT_SUCCEEDED;
}

void OnDeinit(const int reason)
{
   if(g_hLtfAtr != INVALID_HANDLE) IndicatorRelease(g_hLtfAtr);
   if(g_hLtfRsi != INVALID_HANDLE) IndicatorRelease(g_hLtfRsi);
   ObjectsDeleteAll(0, "GSMC_");
   Comment("");
}

//+------------------------------------------------------------------+
//| Seed M15 BOS/CHoCH Structure from Recent Bars                    |
//+------------------------------------------------------------------+
void SeedHtfStructure()
{
   int total = MathMin(Bars(_Symbol, InpHtf) - 1, 250);
   for(int shift = total - InpHtfSwingLen; shift >= 1; shift--)
      UpdateHtfStructureAtShift(shift);
}

void UpdateHtfStructureAtShift(const int closedShift)
{
   // Confirmed pivot sits at `closedShift + InpHtfSwingLen`
   int p = closedShift + InpHtfSwingLen;
   if(p + InpHtfSwingLen >= Bars(_Symbol, InpHtf))
      return;

   double pHigh = iHigh(_Symbol, InpHtf, p);
   double pLow  = iLow(_Symbol, InpHtf, p);
   bool isSwingHigh = true;
   bool isSwingLow  = true;

   for(int k = p - InpHtfSwingLen; k <= p + InpHtfSwingLen; k++)
   {
      if(k == p) continue;
      if(iHigh(_Symbol, InpHtf, k) > pHigh) isSwingHigh = false;
      if(iLow(_Symbol, InpHtf, k)  < pLow)  isSwingLow  = false;
   }

   if(isSwingHigh) g_htfSwingHigh = pHigh;
   if(isSwingLow)  g_htfSwingLow  = pLow;

   double htfClose = iClose(_Symbol, InpHtf, closedShift);
   if(g_htfSwingHigh > 0.0 && htfClose > g_htfSwingHigh)
   {
      g_lastStructureTag = (g_htfBias == -1) ? "BULL CHoCH" : "BULL BOS";
      g_htfBias = 1;
   }
   else if(g_htfSwingLow > 0.0 && htfClose < g_htfSwingLow)
   {
      g_lastStructureTag = (g_htfBias == 1) ? "BEAR CHoCH" : "BEAR BOS";
      g_htfBias = -1;
   }
}

//+------------------------------------------------------------------+
//| Zone Management (Draw, Expire & Invalidate OB/FVG)               |
//+------------------------------------------------------------------+
void AddZone(const int dir, const string kind, const double low, const double high, const datetime created)
{
   int sz = ArraySize(g_zones);
   if(sz >= 12)
   {
      if(g_zones[0].objName != "") ObjectDelete(0, g_zones[0].objName);
      for(int i = 1; i < sz; i++) g_zones[i - 1] = g_zones[i];
      ArrayResize(g_zones, sz - 1);
      sz--;
   }

   g_zoneSeq++;
   string obj = StringFormat("GSMC_%s_%d", kind, g_zoneSeq);

   SmcZone z;
   z.dir     = dir;
   z.kind    = kind;
   z.low     = low;
   z.high    = high;
   z.created = created;
   z.objName = obj;

   ArrayResize(g_zones, sz + 1);
   g_zones[sz] = z;

   if(InpDrawZones && !MQLInfoInteger(MQL_TESTER))
   {
      datetime endT = created + InpZoneTtlMinutes * 60;
      if(ObjectCreate(0, obj, OBJ_RECTANGLE, 0, created, high, endT, low))
      {
         color c = (dir > 0) ? ((kind == "OB") ? clrTeal : clrMediumSeaGreen)
                             : ((kind == "OB") ? clrCrimson : clrIndianRed);
         ObjectSetInteger(0, obj, OBJPROP_COLOR, c);
         ObjectSetInteger(0, obj, OBJPROP_FILL, true);
         ObjectSetInteger(0, obj, OBJPROP_BACK, true);
         ObjectSetInteger(0, obj, OBJPROP_WIDTH, 1);
      }
   }
}

void UpdateZonesOnClosedBar(const double atr1)
{
   datetime bar1Time = iTime(_Symbol, InpLtf, 1);
   double   o1 = iOpen(_Symbol, InpLtf, 1);
   double   c1 = iClose(_Symbol, InpLtf, 1);
   double   h1 = iHigh(_Symbol, InpLtf, 1);
   double   l1 = iLow(_Symbol, InpLtf, 1);
   double   h3 = iHigh(_Symbol, InpLtf, 3);
   double   l3 = iLow(_Symbol, InpLtf, 3);

   // 1. Fair Value Gaps (bars [3], [2], [1])
   if((l1 - h3) >= atr1 * InpMinFvgAtrRatio)
      AddZone(1, "FVG", h3, l1, bar1Time);
   if((l3 - h1) >= atr1 * InpMinFvgAtrRatio)
      AddZone(-1, "FVG", h1, l3, bar1Time);

   // 2. Displacement Order Blocks
   double body = c1 - o1;
   if(body > atr1 * InpObDisplacementAtr)
   {
      for(int k = 2; k <= 1 + InpObLookbackBars; k++)
      {
         if(iClose(_Symbol, InpLtf, k) < iOpen(_Symbol, InpLtf, k))
         {
            AddZone(1, "OB", iLow(_Symbol, InpLtf, k), iHigh(_Symbol, InpLtf, k), iTime(_Symbol, InpLtf, k));
            break;
         }
      }
   }
   else if(-body > atr1 * InpObDisplacementAtr)
   {
      for(int k = 2; k <= 1 + InpObLookbackBars; k++)
      {
         if(iClose(_Symbol, InpLtf, k) > iOpen(_Symbol, InpLtf, k))
         {
            AddZone(-1, "OB", iLow(_Symbol, InpLtf, k), iHigh(_Symbol, InpLtf, k), iTime(_Symbol, InpLtf, k));
            break;
         }
      }
   }

   // 3. Remove expired or invalidated zones
   datetime now = TimeCurrent();
   for(int i = ArraySize(g_zones) - 1; i >= 0; i--)
   {
      bool expired = ((now - g_zones[i].created) > InpZoneTtlMinutes * 60);
      bool broken  = (g_zones[i].dir > 0 && c1 < g_zones[i].low) ||
                     (g_zones[i].dir < 0 && c1 > g_zones[i].high);
      if(expired || broken)
      {
         if(g_zones[i].objName != "") ObjectDelete(0, g_zones[i].objName);
         int last = ArraySize(g_zones) - 1;
         for(int j = i; j < last; j++) g_zones[j] = g_zones[j + 1];
         ArrayResize(g_zones, last);
      }
   }
}

//+------------------------------------------------------------------+
//| Guardrails (The5ers $2.5k 2-Step Rules)                           |
//+------------------------------------------------------------------+
void InitOrRolloverDay(const bool onInit)
{
   datetime today = GetServerDateOnly(TimeCurrent());
   if(onInit && GlobalVariableCheck(GvKey("DAY_DATE")))
   {
      datetime saved = (datetime)GlobalVariableGet(GvKey("DAY_DATE"));
      if(saved == today && GlobalVariableCheck(GvKey("DAY_REF")))
      {
         g_currentDayDate = saved;
         g_dayStartRef    = GlobalVariableGet(GvKey("DAY_REF"));
         g_dailyLocked    = GlobalVariableCheck(GvKey("DAY_LOCKED")) &&
                            (GlobalVariableGet(GvKey("DAY_LOCKED")) > 0.5);
         return;
      }
   }

   if(today != g_currentDayDate)
   {
      g_currentDayDate = today;
      g_dayStartRef = MathMax(AccountInfoDouble(ACCOUNT_BALANCE), AccountInfoDouble(ACCOUNT_EQUITY));
      g_dailyLocked = false;
      g_lockReason  = "";
      GlobalVariableSet(GvKey("DAY_DATE"), (double)g_currentDayDate);
      GlobalVariableSet(GvKey("DAY_REF"),  g_dayStartRef);
      GlobalVariableSet(GvKey("DAY_LOCKED"), 0.0);
   }
}

void CloseAllEaPositions(const string reason)
{
   Print("[GUARDRAIL] Closing all EA positions: ", reason);
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0) continue;
      if(PositionGetString(POSITION_SYMBOL) != _Symbol) continue;
      if((ulong)PositionGetInteger(POSITION_MAGIC) != InpMagicNumber) continue;
      g_trade.PositionClose(ticket);
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

void GetTodayStats(int &tradesOpened, double &realizedPnl, int &consecLosses)
{
   tradesOpened = 0; realizedPnl = 0.0; consecLosses = 0;
   if(!HistorySelect(g_currentDayDate, TimeCurrent() + 60)) return;
   int total = HistoryDealsTotal();
   for(int i = 0; i < total; i++)
   {
      ulong t = HistoryDealGetTicket(i);
      if(t == 0) continue;
      if((ulong)HistoryDealGetInteger(t, DEAL_MAGIC) != InpMagicNumber) continue;
      if(HistoryDealGetString(t, DEAL_SYMBOL) != _Symbol) continue;
      ENUM_DEAL_ENTRY entryType = (ENUM_DEAL_ENTRY)HistoryDealGetInteger(t, DEAL_ENTRY);
      if(entryType == DEAL_ENTRY_IN) tradesOpened++;
      else if(entryType == DEAL_ENTRY_OUT || entryType == DEAL_ENTRY_INOUT)
      {
         double pnl = HistoryDealGetDouble(t, DEAL_PROFIT) + HistoryDealGetDouble(t, DEAL_SWAP) + HistoryDealGetDouble(t, DEAL_COMMISSION);
         realizedPnl += pnl;
         if(pnl < 0.0) consecLosses++;
         else if(pnl > 0.0) consecLosses = 0;
      }
   }
}

bool CheckHardGuardrails()
{
   double equity = AccountInfoDouble(ACCOUNT_EQUITY);
   double staticFloor = InpInitialBalance * (1.0 - InpTotalLossCapPct / 100.0);
   if(equity <= staticFloor)
   {
      if(!g_accountLocked)
      {
         g_accountLocked = true;
         g_lockReason = StringFormat("STATIC FLOOR ($%.2f <= $%.2f)", equity, staticFloor);
         GlobalVariableSet(GvKey("ACC_LOCKED"), 1.0);
         CloseAllEaPositions(g_lockReason);
      }
      return false;
   }
   if(g_accountLocked) return false;

   double dailyLossPct = (g_dayStartRef > 0.0) ? ((g_dayStartRef - equity) / g_dayStartRef * 100.0) : 0.0;
   if(dailyLossPct >= InpDailyLossCapPct)
   {
      if(!g_dailyLocked)
      {
         g_dailyLocked = true;
         g_lockReason = StringFormat("DAILY LOSS CAP (%.2f%%)", dailyLossPct);
         GlobalVariableSet(GvKey("DAY_LOCKED"), 1.0);
         CloseAllEaPositions(g_lockReason);
      }
      return false;
   }
   if(g_dailyLocked) return false;

   int tradesToday = 0, consecLosses = 0;
   double realizedPnl = 0.0;
   GetTodayStats(tradesToday, realizedPnl, consecLosses);

   if(InpDailyProfitLockPct > 0.0 && realizedPnl >= g_dayStartRef * (InpDailyProfitLockPct / 100.0))
   {
      g_lockReason = StringFormat("Profit locked (+$%.2f)", realizedPnl);
      return false;
   }
   if(InpMaxConsecLosses > 0 && consecLosses >= InpMaxConsecLosses)
   {
      g_lockReason = StringFormat("Cooldown (%d losses)", consecLosses);
      return false;
   }
   if(InpMaxTradesPerDay > 0 && tradesToday >= InpMaxTradesPerDay)
   {
      g_lockReason = StringFormat("Max trades (%d/%d)", tradesToday, InpMaxTradesPerDay);
      return false;
   }
   return true;
}

void ManageOpenPositions()
{
   if(InpBreakevenTriggerR <= 0.0) return;
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0) continue;
      if(PositionGetString(POSITION_SYMBOL) != _Symbol) continue;
      if((ulong)PositionGetInteger(POSITION_MAGIC) != InpMagicNumber) continue;

      ENUM_POSITION_TYPE posType = (ENUM_POSITION_TYPE)PositionGetInteger(POSITION_TYPE);
      double openPrice = PositionGetDouble(POSITION_PRICE_OPEN);
      double sl = PositionGetDouble(POSITION_SL);
      double tp = PositionGetDouble(POSITION_TP);
      if(sl <= 0.0 || tp <= 0.0) continue;

      double slDistEst   = MathAbs(tp - openPrice) / InpRewardRiskRatio;
      double triggerDist = slDistEst * InpBreakevenTriggerR;
      double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
      double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
      int digits = (int)SymbolInfoInteger(_Symbol, SYMBOL_DIGITS);

      if(posType == POSITION_TYPE_BUY && (bid - openPrice) >= triggerDist && sl < openPrice)
      {
         double newSl = NormalizeDouble(openPrice + 0.10, digits);
         if(newSl < bid) g_trade.PositionModify(ticket, newSl, tp);
      }
      else if(posType == POSITION_TYPE_SELL && (openPrice - ask) >= triggerDist && sl > openPrice)
      {
         double newSl = NormalizeDouble(openPrice - 0.10, digits);
         if(newSl > ask) g_trade.PositionModify(ticket, newSl, tp);
      }
   }
}

bool IsHighImpactNewsWindow()
{
   if(!InpUseMt5NewsFilter || MQLInfoInteger(MQL_TESTER)) return false;
   datetime now = TimeCurrent();
   MqlCalendarValue values[];
   if(!CalendarValueHistory(values, now - InpNewsAfterMins * 60, now + InpNewsBeforeMins * 60, NULL, "USD"))
      return false;
   for(int i = 0; i < ArraySize(values); i++)
   {
      MqlCalendarEvent ev;
      if(CalendarEventById(values[i].event_id, ev) && ev.importance == CALENDAR_IMPORTANCE_HIGH)
         return true;
   }
   return false;
}

void PollPythonBridge()
{
   if(!InpUsePythonBridge || MQLInfoInteger(MQL_TESTER)) { g_bridgeAllowTrade = true; return; }
   datetime now = TimeCurrent();
   if(now - g_lastBridgePoll < InpBridgePollSecs) return;
   g_lastBridgePoll = now;

   char post[], res[];
   string headers;
   int code = WebRequest("GET", InpBridgeUrl, "", 2000, post, res, headers);
   if(code == 200)
   {
      string body = CharArrayToString(res);
      g_bridgeAllowTrade = (StringFind(body, "\"allow\":true") >= 0 || StringFind(body, "\"allow\": true") >= 0);
      g_bridgeReason = g_bridgeAllowTrade ? "ALLOW" : "HALT";
   }
   else
   {
      g_bridgeAllowTrade = !InpFailClosedOnBridge;
      g_bridgeReason = StringFormat("Offline (HTTP %d)", code);
   }
}

double CalculateLotSize(const double slDistance)
{
   if(slDistance <= 0.0) return 0.0;
   double riskMoney = AccountInfoDouble(ACCOUNT_BALANCE) * (InpRiskPerTradePct / 100.0);
   double tickSize  = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE);
   double tickValue = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_VALUE_LOSS);
   if(tickValue <= 0.0) tickValue = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_VALUE);
   if(tickSize <= 0.0 || tickValue <= 0.0) return 0.0;

   double lossPerLot = (slDistance / tickSize) * tickValue;
   if(lossPerLot <= 0.0) return 0.0;

   double rawLots = riskMoney / lossPerLot;
   double minLot  = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
   double maxLot  = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MAX);
   double step    = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);
   double stepped = MathFloor(rawLots / step) * step;
   if(stepped < minLot) return 0.0;
   return MathMin(maxLot, NormalizeDouble(stepped, 2));
}

bool GetBuf(const int handle, const int shift, double &val)
{
   double buf[1];
   if(CopyBuffer(handle, 0, shift, 1, buf) != 1) return false;
   val = buf[0];
   return true;
}

//+------------------------------------------------------------------+
//| OnTick                                                           |
//+------------------------------------------------------------------+
void OnTick()
{
   InitOrRolloverDay(false);
   bool guardrailsOk = CheckHardGuardrails();
   ManageOpenPositions();
   PollPythonBridge();

   // Update M15 Structure on new M15 bar
   datetime htfTime = iTime(_Symbol, InpHtf, 0);
   if(htfTime != 0 && htfTime != g_lastHtfBar)
   {
      g_lastHtfBar = htfTime;
      UpdateHtfStructureAtShift(1);
   }

   Comment(StringFormat(
      "GoldSMCPropEA | M15: %s | Zones: %d | Equity: $%.2f | Guard: %s | Bridge: %s",
      g_lastStructureTag, ArraySize(g_zones), AccountInfoDouble(ACCOUNT_EQUITY),
      guardrailsOk ? "OK" : g_lockReason, g_bridgeReason
   ));

   if(!guardrailsOk || CountOpenEaPositions() > 0) return;

   datetime ltfTime = iTime(_Symbol, InpLtf, 0);
   if(ltfTime == 0 || ltfTime == g_lastLtfBar) return;
   g_lastLtfBar = ltfTime;

   double atr1 = 0.0, r1 = 0.0, r2 = 0.0;
   if(!GetBuf(g_hLtfAtr, 1, atr1) || !GetBuf(g_hLtfRsi, 1, r1) || !GetBuf(g_hLtfRsi, 2, r2))
      return;

   // Always keep zones updated before checking entries
   // Check taps against zones that existed prior to closed bar [1]
   double o1 = iOpen(_Symbol, InpLtf, 1);
   double c1 = iClose(_Symbol, InpLtf, 1);
   double h1 = iHigh(_Symbol, InpLtf, 1);
   double l1 = iLow(_Symbol, InpLtf, 1);

   MqlDateTime tm;
   TimeToStruct(TimeCurrent(), tm);
   double spread = SymbolInfoDouble(_Symbol, SYMBOL_ASK) - SymbolInfoDouble(_Symbol, SYMBOL_BID);

   bool filtersOk = g_bridgeAllowTrade
                 && (tm.hour >= InpSessionStartHour && tm.hour < InpSessionEndHour)
                 && (spread <= InpMaxSpreadDollars)
                 && (atr1 >= InpMinAtrDollars && atr1 <= InpMaxAtrDollars)
                 && !IsHighImpactNewsWindow();

   if(filtersOk && g_htfBias != 0)
   {
      int digits = (int)SymbolInfoInteger(_Symbol, SYMBOL_DIGITS);
      for(int i = ArraySize(g_zones) - 1; i >= 0; i--)
      {
         SmcZone z = g_zones[i];
         if(z.dir != g_htfBias) continue;
         double zoneMid = (z.low + z.high) / 2.0;

         if(g_htfBias == 1 && l1 <= z.high && c1 >= z.low)
         {
            if(c1 > o1 && c1 >= zoneMid && r1 >= 42.0 && r1 <= 68.0 && r1 > r2)
            {
               double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
               double rawSl = MathMax(ask - (z.low - atr1 * InpSlAtrBuffer), atr1 * InpMinSlAtrMult);
               double slDist = MathMin(rawSl, atr1 * InpMaxSlAtrMult);
               double lots = CalculateLotSize(slDist);
               if(lots > 0.0)
               {
                  double sl = NormalizeDouble(ask - slDist, digits);
                  double tp = NormalizeDouble(ask + slDist * InpRewardRiskRatio, digits);
                  g_trade.Buy(lots, _Symbol, ask, sl, tp, "SMC_" + z.kind + "_Long");
               }
               break;
            }
         }
         else if(g_htfBias == -1 && h1 >= z.low && c1 <= z.high)
         {
            if(c1 < o1 && c1 <= zoneMid && r1 >= 32.0 && r1 <= 58.0 && r1 < r2)
            {
               double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
               double rawSl = MathMax((z.high + atr1 * InpSlAtrBuffer) - bid, atr1 * InpMinSlAtrMult);
               double slDist = MathMin(rawSl, atr1 * InpMaxSlAtrMult);
               double lots = CalculateLotSize(slDist);
               if(lots > 0.0)
               {
                  double sl = NormalizeDouble(bid + slDist, digits);
                  double tp = NormalizeDouble(bid - slDist * InpRewardRiskRatio, digits);
                  g_trade.Sell(lots, _Symbol, bid, sl, tp, "SMC_" + z.kind + "_Short");
               }
               break;
            }
         }
      }
   }

   UpdateZonesOnClosedBar(atr1);
}
//+------------------------------------------------------------------+
