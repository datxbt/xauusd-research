//+------------------------------------------------------------------+
//|                                    XAUUSD_SessionBreakout.mq5     |
//|                                                                  |
//|  Eight opening-range breakouts, one per major session open.      |
//|  Direct port of the backtested strategy:                         |
//|                                                                  |
//|    h00_r30_t1  h01_r60_t3  h02_r15_t3  h04_r30_t3                |
//|    h05_r60_t2  h06_r60_t3  h13_r30_t3  h14_r15_t2                |
//|                                                                  |
//|  Per window, once per day:                                       |
//|    1. Bracket the high/low of the first R minutes after H:00 UTC |
//|    2. Buy Stop at the high, Sell Stop at the low (OCO by hand)   |
//|    3. Stop loss = the opposite side of the bracket               |
//|    4. Take profit = fill price +/- T * bracket width             |
//|    5. Cancel unfilled orders 4h after the bracket closes         |
//|    6. Flat before 00:00 UTC, every day                           |
//|                                                                  |
//|  Position size scales inversely with trailing realized           |
//|  volatility, so risk per trade stays constant as gold's range    |
//|  expands and contracts.                                          |
//|                                                                  |
//|  ---------------------------------------------------------------|
//|  SET InpGMTOffsetHours BEFORE RUNNING. Every window is defined   |
//|  in UTC. If your broker's server clock is GMT+2, this must be 2, |
//|  or the EA trades the wrong hours entirely. Check with the       |
//|  "GMT offset" line the EA prints to the Experts log on start,    |
//|  and mind that many brokers shift by 1h for daylight saving.     |
//|  ---------------------------------------------------------------|
//+------------------------------------------------------------------+
#property copyright "Session breakout portfolio"
#property version   "1.00"

#include <Trade\Trade.mqh>
#include <Trade\SymbolInfo.mqh>

//--- how many windows the portfolio runs
#define WINDOW_COUNT 8

//+------------------------------------------------------------------+
//| Inputs                                                           |
//+------------------------------------------------------------------+
input group "=== Broker clock (READ THE HEADER) ==="
input int      InpGMTOffsetHours   = 0;      // Server time = GMT + this many hours
input bool     InpAutoDetectGMT    = true;   // Cross-check offset against TimeGMT() at start

input group "=== Position sizing ==="
input double   InpBaseLots         = 0.02;   // Base lots per window
input bool     InpUseVolTargeting  = true;   // Scale size by trailing volatility
input double   InpTargetVolPct     = 0.816;  // Reference daily realized vol (%)
input int      InpVolLookbackDays  = 20;     // Sessions in the trailing vol average
input double   InpMaxVolScale      = 3.0;    // Cap on the size multiplier (and 1/x floor)

input group "=== Risk guards ==="
input double   InpMaxSpreadUSD     = 0.30;   // Skip entries above this spread, USD/oz (0 = off)
input int      InpMaxOpenPositions = 6;      // Portfolio-wide cap (backtest peak was 6)
input double   InpMaxDailyLossPct  = 0.0;    // Halt for the day past this % loss (0 = off)

input group "=== Session rules ==="
input double   InpOrderExpiryHours = 4.0;    // Cancel unfilled orders this long after the range
input int      InpFlatBeforeUTCMin = 3;      // Close everything this many minutes before 00:00 UTC
input double   InpMinRangePct      = 0.05;   // Skip if the bracket is narrower than this % of price
input double   InpMaxRangePct      = 2.00;   // Skip if the bracket is wider than this % of price
input int      InpForceFlatUTCH    = 24;     // Flatten everything at this UTC hour daily (24 = off)
input bool     InpTradeNYWindows   = true;   // Trade the 13:00 and 14:00 UTC windows too
input int      InpFridayCloseUTCH  = 20;     // Friday: flatten at this UTC hour (24 = off)
input bool     InpSkipSunday       = true;   // Skip the thin Sunday session
input bool     InpAdjustBidToMid   = true;   // Bars are bid-priced; shift them to mid like the backtest

input group "=== Bookkeeping ==="
input ulong    InpMagicBase        = 8800000; // Magic numbers are base+0 .. base+7
input int      InpSlippagePoints   = 20;      // Max deviation on market close-outs
input bool     InpVerboseLog       = true;    // Narrate decisions to the Experts log

//+------------------------------------------------------------------+
//| Window definition and per-day state                              |
//+------------------------------------------------------------------+
struct SessionWindow
{
   string   name;          // label used in logs and order comments
   int      hour;          // UTC hour the bracket starts
   int      rangeMin;      // bracket length in minutes
   double   targetMult;    // take profit as a multiple of bracket width
   ulong    magic;         // magic number identifying this window's orders

   int      armedDay;      // UTC day key the orders were placed (-1 = not yet today)
   int      doneDay;       // UTC day key this window already traded
   double   hi;            // bracket high (mid price)
   double   lo;            // bracket low  (mid price)
   datetime expiryUTC;     // cancel unfilled orders after this moment
   ulong    buyTicket;     // pending buy stop
   ulong    sellTicket;    // pending sell stop
};

SessionWindow  g_win[WINDOW_COUNT];
CTrade         g_trade;
CSymbolInfo    g_sym;

double   g_volScale      = 1.0;   // cached size multiplier
int      g_volScaleDay   = -1;    // UTC day key the multiplier was computed
double   g_dayStartEquity = 0.0;
int      g_equityDay     = -1;
bool     g_haltedToday   = false;

//+------------------------------------------------------------------+
//| Clock helpers - everything the strategy does is defined in UTC   |
//+------------------------------------------------------------------+
datetime UTCNow()
{
   return TimeCurrent() - (datetime)(InpGMTOffsetHours * 3600);
}

datetime ServerFromUTC(const datetime utc)
{
   return utc + (datetime)(InpGMTOffsetHours * 3600);
}

//--- YYYYMMDD in UTC; unlike day_of_year this never repeats across years
int DayKeyOf(const datetime utc)
{
   MqlDateTime dt;
   TimeToStruct(utc, dt);
   return dt.year * 10000 + dt.mon * 100 + dt.day;
}

int UTCDayKey()
{
   return DayKeyOf(UTCNow());
}

//--- today's H:00:00 UTC, as a UTC timestamp
datetime UTCTodayAtHour(const int hour)
{
   MqlDateTime dt;
   TimeToStruct(UTCNow(), dt);
   dt.hour = hour;
   dt.min  = 0;
   dt.sec  = 0;
   return StructToTime(dt);
}

//+------------------------------------------------------------------+
//| Initialisation                                                   |
//+------------------------------------------------------------------+
int OnInit()
{
   if(!g_sym.Name(_Symbol))
   {
      Print("ERROR: cannot select symbol ", _Symbol);
      return INIT_FAILED;
   }
   g_sym.RefreshRates();

   g_trade.SetDeviationInPoints(InpSlippagePoints);
   g_trade.SetTypeFillingBySymbol(_Symbol);
   g_trade.SetAsyncMode(false);

   //--- the eight windows, exactly as selected in the study
   DefineWindow(0, "h00_r30_t1",  0, 30, 1.0);
   DefineWindow(1, "h01_r60_t3",  1, 60, 3.0);
   DefineWindow(2, "h02_r15_t3",  2, 15, 3.0);
   DefineWindow(3, "h04_r30_t3",  4, 30, 3.0);
   DefineWindow(4, "h05_r60_t2",  5, 60, 2.0);
   DefineWindow(5, "h06_r60_t3",  6, 60, 3.0);
   DefineWindow(6, "h13_r30_t3", 13, 30, 3.0);
   DefineWindow(7, "h14_r15_t2", 14, 15, 2.0);

   RestoreTodayState();

   PrintFormat("Session breakout EA on %s | server time %s | assumed GMT%+d | UTC now %s",
               _Symbol,
               TimeToString(TimeCurrent(), TIME_DATE | TIME_SECONDS),
               InpGMTOffsetHours,
               TimeToString(UTCNow(), TIME_DATE | TIME_SECONDS));

   if(InpAutoDetectGMT)
   {
      //--- TimeGMT() is unreliable inside the tester, so this only warns
      int detected = (int)MathRound((double)(TimeCurrent() - TimeGMT()) / 3600.0);
      if(detected != InpGMTOffsetHours && !MQLInfoInteger(MQL_TESTER))
         PrintFormat("WARNING: terminal suggests the server is GMT%+d but InpGMTOffsetHours=%d. "
                     "Verify before trading - the windows depend on it.",
                     detected, InpGMTOffsetHours);
   }

   if(InpBaseLots < g_sym.LotsMin())
      PrintFormat("WARNING: InpBaseLots %.4f is below the broker minimum %.4f; orders will be raised to it.",
                  InpBaseLots, g_sym.LotsMin());

   return INIT_SUCCEEDED;
}

//+------------------------------------------------------------------+
//| Rebuild today's per-window state after a restart.                 |
//|                                                                   |
//| Without this the EA forgets which windows already traded today and|
//| can arm a second time on the same session - a duplicate trade.    |
//| Live orders, open positions and today's closed deals all count as |
//| evidence that a window is spent.                                  |
//+------------------------------------------------------------------+
void RestoreTodayState()
{
   const int today = UTCDayKey();
   int restored = 0;

   //--- claim the day up front so the first RollDailyState does not wipe this
   g_equityDay      = today;
   g_dayStartEquity = AccountInfoDouble(ACCOUNT_EQUITY);
   g_haltedToday    = false;

   //--- surviving pending orders: the window is armed, and their prices give
   //--- back the bracket that the take-profit re-anchoring needs
   for(int k = OrdersTotal() - 1; k >= 0; k--)
   {
      const ulong t = OrderGetTicket(k);
      if(t == 0) continue;
      if(OrderGetString(ORDER_SYMBOL) != _Symbol) continue;
      const int i = WindowIndexOf((ulong)OrderGetInteger(ORDER_MAGIC));
      if(i < 0) continue;
      g_win[i].armedDay = today;
      const long type = OrderGetInteger(ORDER_TYPE);
      const double px = OrderGetDouble(ORDER_PRICE_OPEN);
      if(type == ORDER_TYPE_BUY_STOP)  g_win[i].hi = px;
      if(type == ORDER_TYPE_SELL_STOP) g_win[i].lo = px;
      restored++;
   }

   //--- an open position means the window has already had its trade
   for(int k = PositionsTotal() - 1; k >= 0; k--)
   {
      const ulong t = PositionGetTicket(k);
      if(t == 0) continue;
      if(PositionGetString(POSITION_SYMBOL) != _Symbol) continue;
      const int i = WindowIndexOf((ulong)PositionGetInteger(POSITION_MAGIC));
      if(i < 0) continue;
      g_win[i].armedDay = today;
      g_win[i].doneDay  = today;
      restored++;
   }

   //--- and so does a deal already closed today
   if(HistorySelect(ServerFromUTC(UTCTodayAtHour(0)), TimeCurrent()))
   {
      for(int k = HistoryDealsTotal() - 1; k >= 0; k--)
      {
         const ulong d = HistoryDealGetTicket(k);
         if(d == 0) continue;
         if(HistoryDealGetString(d, DEAL_SYMBOL) != _Symbol) continue;
         const int i = WindowIndexOf((ulong)HistoryDealGetInteger(d, DEAL_MAGIC));
         if(i < 0) continue;
         g_win[i].armedDay = today;
         g_win[i].doneDay  = today;
         restored++;
      }
   }

   if(restored > 0)
      PrintFormat("Restart: recovered state for today from %d order/position/deal records", restored);
}

int WindowIndexOf(const ulong magic)
{
   if(magic < InpMagicBase || magic >= InpMagicBase + WINDOW_COUNT)
      return -1;
   return (int)(magic - InpMagicBase);
}

void DefineWindow(const int i, const string name, const int hour,
                  const int rangeMin, const double targetMult)
{
   g_win[i].name       = name;
   g_win[i].hour       = hour;
   g_win[i].rangeMin   = rangeMin;
   g_win[i].targetMult = targetMult;
   g_win[i].magic      = InpMagicBase + (ulong)i;
   g_win[i].armedDay   = -1;
   g_win[i].doneDay    = -1;
   g_win[i].hi         = 0.0;
   g_win[i].lo         = 0.0;
   g_win[i].expiryUTC  = 0;
   g_win[i].buyTicket  = 0;
   g_win[i].sellTicket = 0;
}

void OnDeinit(const int reason) { }

//+------------------------------------------------------------------+
//| Main loop                                                        |
//+------------------------------------------------------------------+
void OnTick()
{
   if(!g_sym.RefreshRates())
      return;

   const datetime utc   = UTCNow();
   const int      today = UTCDayKey();

   RollDailyState(today);
   EnforceDailyLossHalt();

   //--- Flatten rules, in order of reliability.
   //--- The 23:57 window alone is not enough: on Friday the market closes
   //--- around 21:00 UTC, no tick ever arrives inside it, and the position
   //--- rides the weekend gap - which the tester showed happening on 2.5%
   //--- of trades. So we also sweep anything left over from an earlier day,
   //--- and flatten early on Friday.
   if(CloseStaleFromEarlierSessions() > 0)
      return;

   //--- Hard daily cut-off, for running on a PC that is not on overnight.
   //--- Everything is closed and cancelled here so nothing is left unmanaged
   //--- once the terminal goes down.
   if(InpForceFlatUTCH < 24 && UTCHourOf(utc) >= InpForceFlatUTCH)
   {
      CloseEverything("daily force-flat");
      CancelAllPending("daily force-flat");
      return;
   }

   if(InpFridayCloseUTCH < 24 && IsUTCFriday(utc) && UTCHourOf(utc) >= InpFridayCloseUTCH)
   {
      CloseEverything("friday cutoff");
      CancelAllPending("friday cutoff");
      return;
   }

   if(MinutesToUTCMidnight(utc) <= InpFlatBeforeUTCMin)
   {
      CloseEverything("session close");
      return;
   }

   if(g_haltedToday)
   {
      CancelAllPending("daily loss halt");
      return;
   }

   for(int i = 0; i < WINDOW_COUNT; i++)
      ProcessWindow(i, utc, today);
}

//+------------------------------------------------------------------+
//| Reset per-day state when the UTC date changes                    |
//+------------------------------------------------------------------+
void RollDailyState(const int today)
{
   if(g_equityDay == today)
      return;

   g_equityDay      = today;
   g_dayStartEquity = AccountInfoDouble(ACCOUNT_EQUITY);
   g_haltedToday    = false;

   for(int i = 0; i < WINDOW_COUNT; i++)
   {
      g_win[i].armedDay   = -1;
      g_win[i].buyTicket  = 0;
      g_win[i].sellTicket = 0;
   }

   if(InpVerboseLog)
      PrintFormat("--- new UTC day (%d), start equity %.2f ---", today, g_dayStartEquity);
}

void EnforceDailyLossHalt()
{
   if(InpMaxDailyLossPct <= 0.0 || g_dayStartEquity <= 0.0 || g_haltedToday)
      return;

   const double eq   = AccountInfoDouble(ACCOUNT_EQUITY);
   const double loss = (g_dayStartEquity - eq) / g_dayStartEquity * 100.0;
   if(loss >= InpMaxDailyLossPct)
   {
      g_haltedToday = true;
      PrintFormat("Daily loss %.2f%% hit the %.2f%% limit - halting new entries for today.",
                  loss, InpMaxDailyLossPct);
      CloseEverything("daily loss halt");
   }
}

int MinutesToUTCMidnight(const datetime utc)
{
   MqlDateTime dt;
   TimeToStruct(utc, dt);
   return (23 - dt.hour) * 60 + (60 - dt.min);
}

//+------------------------------------------------------------------+
//| One window's state machine for the current day                   |
//+------------------------------------------------------------------+
void ProcessWindow(const int i, const datetime utc, const int today)
{
   //--- keep the OCO pair honest and retire expired orders first
   ResolveOCO(i);
   if(g_win[i].armedDay == today && utc >= g_win[i].expiryUTC)
      CancelWindowPending(i, "expired unfilled");

   if(g_win[i].doneDay == today || g_win[i].armedDay == today)
      return;

   //--- NY windows need the terminal alive until ~18:15 UTC for order expiry
   //--- and beyond that for the position. Skipping them shortens the required
   //--- uptime by roughly eight hours and costs about 13% of the edge.
   if(!InpTradeNYWindows && g_win[i].hour >= 12)
   {
      g_win[i].armedDay = today;
      return;
   }

   //--- the bracket must be complete before we act on it
   const datetime rangeStart = UTCTodayAtHour(g_win[i].hour);
   const datetime rangeEnd   = rangeStart + g_win[i].rangeMin * 60;
   if(utc < rangeEnd)
      return;

   //--- too late in the day to still be arming this one
   if(utc >= rangeEnd + (datetime)(InpOrderExpiryHours * 3600))
   {
      g_win[i].armedDay = today;   // mark handled so we stop re-checking
      return;
   }

   if(InpSkipSunday && IsUTCSunday(utc))
   {
      g_win[i].armedDay = today;
      return;
   }

   double hi = 0.0, lo = 0.0;
   if(!BuildRange(rangeStart, rangeEnd, hi, lo))
   {
      if(InpVerboseLog)
         PrintFormat("%s: no M1 data for %s..%s - skipping today", g_win[i].name,
                     TimeToString(rangeStart, TIME_MINUTES), TimeToString(rangeEnd, TIME_MINUTES));
      g_win[i].armedDay = today;
      return;
   }

   const double width   = hi - lo;
   const double refPx   = (hi + lo) * 0.5;
   const double widthPct = (refPx > 0.0) ? width / refPx * 100.0 : 0.0;

   if(widthPct < InpMinRangePct || widthPct > InpMaxRangePct)
   {
      if(InpVerboseLog)
         PrintFormat("%s: bracket %.2f (%.3f%%) outside [%.2f%%, %.2f%%] - skipping",
                     g_win[i].name, width, widthPct, InpMinRangePct, InpMaxRangePct);
      g_win[i].armedDay = today;
      return;
   }

   if(!SpreadAcceptable())
   {
      if(InpVerboseLog)
         PrintFormat("%s: spread %.3f USD above the %.3f limit - skipping",
                     g_win[i].name, g_sym.Ask() - g_sym.Bid(), InpMaxSpreadUSD);
      g_win[i].armedDay = today;
      return;
   }

   //--- normally we arm the instant the bracket closes; on a restart price may
   //--- already have broken out, and arming only the far side would take the
   //--- wrong direction. Sit the day out instead.
   if(g_sym.Ask() >= hi || g_sym.Bid() <= lo)
   {
      if(InpVerboseLog)
         PrintFormat("%s: price already outside %.2f-%.2f at arming time - missed the break, skipping",
                     g_win[i].name, lo, hi);
      g_win[i].armedDay = today;
      return;
   }

   if(CountOurPositions() >= InpMaxOpenPositions)
   {
      if(InpVerboseLog)
         PrintFormat("%s: %d positions already open - skipping", g_win[i].name, CountOurPositions());
      g_win[i].armedDay = today;
      return;
   }

   PlaceBracket(i, hi, lo, width, rangeEnd, today);
}

bool IsUTCSunday(const datetime utc)
{
   MqlDateTime dt;
   TimeToStruct(utc, dt);
   return (dt.day_of_week == 0);
}

bool IsUTCFriday(const datetime utc)
{
   MqlDateTime dt;
   TimeToStruct(utc, dt);
   return (dt.day_of_week == 5);
}

int UTCHourOf(const datetime utc)
{
   MqlDateTime dt;
   TimeToStruct(utc, dt);
   return dt.hour;
}

//--- Close anything opened on an earlier UTC day. This is the backstop that
//--- makes the daily-flat rule hold even across weekends and holidays, when
//--- no tick arrives before midnight. Returns how many it closed.
int CloseStaleFromEarlierSessions()
{
   const int today = UTCDayKey();
   int closed = 0;

   for(int k = PositionsTotal() - 1; k >= 0; k--)
   {
      const ulong t = PositionGetTicket(k);
      if(t == 0) continue;
      if(PositionGetString(POSITION_SYMBOL) != _Symbol) continue;
      const ulong magic = (ulong)PositionGetInteger(POSITION_MAGIC);
      if(!IsOurMagic(magic)) continue;

      const datetime openedUTC = (datetime)PositionGetInteger(POSITION_TIME)
                                 - (datetime)(InpGMTOffsetHours * 3600);
      if(DayKeyOf(openedUTC) == today)
         continue;

      g_trade.SetExpertMagicNumber(magic);
      if(g_trade.PositionClose(t))
      {
         closed++;
         PrintFormat("Stale position %I64u from %s closed - it survived a session boundary",
                     t, TimeToString(openedUTC, TIME_DATE | TIME_MINUTES));
      }
   }
   return closed;
}

//+------------------------------------------------------------------+
//| Bracket = high/low of M1 bars covering [start, end) in UTC       |
//|                                                                  |
//| MT5 bars are bid-priced while the study measured mid, so both    |
//| extremes shift up by half the spread. The width is unaffected.   |
//+------------------------------------------------------------------+
bool BuildRange(const datetime rangeStartUTC, const datetime rangeEndUTC,
                double &hi, double &lo)
{
   MqlRates rates[];
   ArraySetAsSeries(rates, false);

   const datetime from = ServerFromUTC(rangeStartUTC);
   const datetime to   = ServerFromUTC(rangeEndUTC) - 60;   // last bar that opens inside the window

   const int n = CopyRates(_Symbol, PERIOD_M1, from, to, rates);
   if(n <= 0)
      return false;

   hi = rates[0].high;
   lo = rates[0].low;
   for(int k = 1; k < n; k++)
   {
      if(rates[k].high > hi) hi = rates[k].high;
      if(rates[k].low  < lo) lo = rates[k].low;
   }

   if(InpAdjustBidToMid)
   {
      const double half = (g_sym.Ask() - g_sym.Bid()) * 0.5;
      hi += half;
      lo += half;
   }

   hi = g_sym.NormalizePrice(hi);
   lo = g_sym.NormalizePrice(lo);
   return (hi > lo);
}

//+------------------------------------------------------------------+
//| Place the OCO stop pair                                          |
//|                                                                  |
//| A Buy Stop triggers on ask and a Sell Stop on bid, which is      |
//| exactly how the backtest detected breaks - no adjustment needed. |
//| The stop loss sits at the opposite side of the bracket, a fixed  |
//| price. The take profit is set from the ACTUAL fill once the      |
//| position opens (see SyncTakeProfit).                             |
//+------------------------------------------------------------------+
void PlaceBracket(const int i, const double hi, const double lo, const double width,
                  const datetime rangeEnd, const int today)
{
   const double lots = ResolveLots();
   if(lots <= 0.0)
   {
      g_win[i].armedDay = today;
      return;
   }

   //--- the broker refuses stop orders placed nearer than this to the market
   const double stopsLevel = (double)SymbolInfoInteger(_Symbol, SYMBOL_TRADE_STOPS_LEVEL) * g_sym.Point();
   const bool   buyFarEnough  = (hi - g_sym.Ask() >= stopsLevel);
   const bool   sellFarEnough = (g_sym.Bid() - lo >= stopsLevel);
   if(!buyFarEnough && !sellFarEnough)
   {
      if(InpVerboseLog)
         PrintFormat("%s: both sides inside the %.2f stops level - skipping", g_win[i].name, stopsLevel);
      g_win[i].armedDay = today;
      return;
   }

   const double tpBuy  = g_sym.NormalizePrice(hi + g_win[i].targetMult * width);
   const double tpSell = g_sym.NormalizePrice(lo - g_win[i].targetMult * width);

   g_trade.SetExpertMagicNumber(g_win[i].magic);
   const string tag = g_win[i].name;

   bool anyPlaced = false;

   //--- long side: only if price has not already left the bracket upward
   if(buyFarEnough)
   {
      if(g_trade.BuyStop(lots, hi, _Symbol, lo, tpBuy, ORDER_TIME_GTC, 0, tag))
      {
         g_win[i].buyTicket = g_trade.ResultOrder();
         anyPlaced = true;
      }
      else
         PrintFormat("%s: BuyStop failed (%d) %s", tag, g_trade.ResultRetcode(), g_trade.ResultRetcodeDescription());
   }

   //--- short side
   if(sellFarEnough)
   {
      if(g_trade.SellStop(lots, lo, _Symbol, hi, tpSell, ORDER_TIME_GTC, 0, tag))
      {
         g_win[i].sellTicket = g_trade.ResultOrder();
         anyPlaced = true;
      }
      else
         PrintFormat("%s: SellStop failed (%d) %s", tag, g_trade.ResultRetcode(), g_trade.ResultRetcodeDescription());
   }

   g_win[i].hi        = hi;
   g_win[i].lo        = lo;
   g_win[i].expiryUTC = rangeEnd + (datetime)(InpOrderExpiryHours * 3600);
   g_win[i].armedDay  = today;

   if(anyPlaced && InpVerboseLog)
      PrintFormat("%s armed: bracket %.2f-%.2f (width %.2f, %.3f%%), %.2f lots, targets %.2f / %.2f",
                  tag, lo, hi, width, width / ((hi + lo) * 0.5) * 100.0, lots, tpBuy, tpSell);
}

//+------------------------------------------------------------------+
//| Manual OCO: once one side fills, retire the other and set the    |
//| take profit from the price we actually got.                      |
//+------------------------------------------------------------------+
void ResolveOCO(const int i)
{
   const ulong posTicket = FindPosition(g_win[i].magic);
   if(posTicket == 0)
      return;

   //--- a position exists for this window: kill any surviving pending order
   for(int k = OrdersTotal() - 1; k >= 0; k--)
   {
      const ulong t = OrderGetTicket(k);
      if(t == 0) continue;
      if((ulong)OrderGetInteger(ORDER_MAGIC) != g_win[i].magic) continue;
      g_trade.OrderDelete(t);
      if(InpVerboseLog)
         PrintFormat("%s: opposite order cancelled after fill", g_win[i].name);
   }

   g_win[i].doneDay    = UTCDayKey();
   g_win[i].buyTicket  = 0;
   g_win[i].sellTicket = 0;

   SyncTakeProfit(i, posTicket);
}

void SyncTakeProfit(const int i, const ulong ticket)
{
   if(!PositionSelectByTicket(ticket))
      return;

   const double entry = PositionGetDouble(POSITION_PRICE_OPEN);
   const double sl    = PositionGetDouble(POSITION_SL);
   const double tpNow = PositionGetDouble(POSITION_TP);
   const long   type  = PositionGetInteger(POSITION_TYPE);
   const double width = g_win[i].hi - g_win[i].lo;
   if(width <= 0.0)
      return;

   const double tpWant = (type == POSITION_TYPE_BUY)
                         ? g_sym.NormalizePrice(entry + g_win[i].targetMult * width)
                         : g_sym.NormalizePrice(entry - g_win[i].targetMult * width);

   //--- only touch it if the fill drifted from the trigger price
   if(MathAbs(tpWant - tpNow) < g_sym.Point())
      return;

   g_trade.SetExpertMagicNumber(g_win[i].magic);
   if(g_trade.PositionModify(ticket, sl, tpWant) && InpVerboseLog)
      PrintFormat("%s: filled at %.2f, take profit re-anchored to %.2f", g_win[i].name, entry, tpWant);
}

//+------------------------------------------------------------------+
//| Position sizing                                                  |
//|                                                                  |
//| lots = base * clip(targetVol / trailingVol, 1/max, max)          |
//| Trailing vol is the mean of the last N daily realized vols,      |
//| each the stdev of that day's M5 log returns, annualised within   |
//| the day and expressed in percent - the same statistic the study  |
//| used.                                                            |
//+------------------------------------------------------------------+
double ResolveLots()
{
   double lots = InpBaseLots;

   if(InpUseVolTargeting)
   {
      const int today = UTCDayKey();
      if(g_volScaleDay != today)
      {
         const double tv = TrailingRealizedVol(InpVolLookbackDays);
         if(tv > 0.0)
         {
            g_volScale = InpTargetVolPct / tv;
            g_volScale = MathMax(1.0 / InpMaxVolScale, MathMin(InpMaxVolScale, g_volScale));
         }
         else
            g_volScale = 1.0;

         g_volScaleDay = today;
         if(InpVerboseLog)
            PrintFormat("Volatility scale for today: %.3f (trailing vol %.3f%% vs target %.3f%%)",
                        g_volScale, tv, InpTargetVolPct);
      }
      lots = InpBaseLots * g_volScale;
   }

   return NormalizeLots(lots);
}

double TrailingRealizedVol(const int days)
{
   double sum = 0.0;
   int    used = 0;

   for(int back = 1; back <= days * 2 + 10 && used < days; back++)
   {
      const datetime dayStartUTC = UTCTodayAtHour(0) - (datetime)(back * 86400);
      const double v = DailyRealizedVol(dayStartUTC);
      if(v > 0.0)
      {
         sum += v;
         used++;
      }
   }

   return (used >= 5) ? sum / used : 0.0;
}

double DailyRealizedVol(const datetime dayStartUTC)
{
   MqlRates rates[];
   ArraySetAsSeries(rates, false);

   const datetime from = ServerFromUTC(dayStartUTC);
   const datetime to   = ServerFromUTC(dayStartUTC + 86400) - 300;

   const int n = CopyRates(_Symbol, PERIOD_M5, from, to, rates);
   if(n < 30)
      return 0.0;

   //--- log returns of the M5 closes
   double mean = 0.0;
   int    m    = 0;
   double r[];
   ArrayResize(r, n - 1);
   for(int k = 1; k < n; k++)
   {
      if(rates[k - 1].close <= 0.0 || rates[k].close <= 0.0) continue;
      r[m] = MathLog(rates[k].close / rates[k - 1].close);
      mean += r[m];
      m++;
   }
   if(m < 20)
      return 0.0;
   mean /= m;

   double ss = 0.0;
   for(int k = 0; k < m; k++)
      ss += (r[k] - mean) * (r[k] - mean);

   const double sd = MathSqrt(ss / (m - 1));
   return sd * MathSqrt((double)m) * 100.0;
}

double NormalizeLots(double lots)
{
   const double minL = g_sym.LotsMin();
   const double maxL = g_sym.LotsMax();
   const double step = g_sym.LotsStep();

   if(step > 0.0)
      lots = MathFloor(lots / step + 0.5) * step;

   lots = MathMax(minL, MathMin(maxL, lots));
   return NormalizeDouble(lots, 2);
}

//+------------------------------------------------------------------+
//| Housekeeping                                                     |
//+------------------------------------------------------------------+
bool IsOurMagic(const ulong magic)
{
   return (magic >= InpMagicBase && magic < InpMagicBase + WINDOW_COUNT);
}

ulong FindPosition(const ulong magic)
{
   for(int k = PositionsTotal() - 1; k >= 0; k--)
   {
      const ulong t = PositionGetTicket(k);
      if(t == 0) continue;
      if(PositionGetString(POSITION_SYMBOL) != _Symbol) continue;
      if((ulong)PositionGetInteger(POSITION_MAGIC) == magic)
         return t;
   }
   return 0;
}

int CountOurPositions()
{
   int n = 0;
   for(int k = PositionsTotal() - 1; k >= 0; k--)
   {
      if(PositionGetTicket(k) == 0) continue;
      if(PositionGetString(POSITION_SYMBOL) != _Symbol) continue;
      if(IsOurMagic((ulong)PositionGetInteger(POSITION_MAGIC)))
         n++;
   }
   return n;
}

//--- Expressed in USD per ounce so it behaves the same on 2- and 3-digit
//--- quotes. A 60-point cap on 3-digit gold is only $0.06 and rejects most
//--- of a wide-spread year, while the strategy breaks even around $1.34/oz.
bool SpreadAcceptable()
{
   if(InpMaxSpreadUSD <= 0.0)
      return true;
   return ((g_sym.Ask() - g_sym.Bid()) <= InpMaxSpreadUSD);
}

void CancelWindowPending(const int i, const string why)
{
   for(int k = OrdersTotal() - 1; k >= 0; k--)
   {
      const ulong t = OrderGetTicket(k);
      if(t == 0) continue;
      if((ulong)OrderGetInteger(ORDER_MAGIC) != g_win[i].magic) continue;
      if(g_trade.OrderDelete(t) && InpVerboseLog)
         PrintFormat("%s: pending order cancelled (%s)", g_win[i].name, why);
   }
   g_win[i].buyTicket  = 0;
   g_win[i].sellTicket = 0;
   g_win[i].doneDay    = UTCDayKey();   // do not re-arm this window today
}

void CancelAllPending(const string why)
{
   for(int k = OrdersTotal() - 1; k >= 0; k--)
   {
      const ulong t = OrderGetTicket(k);
      if(t == 0) continue;
      if(OrderGetString(ORDER_SYMBOL) != _Symbol) continue;
      if(!IsOurMagic((ulong)OrderGetInteger(ORDER_MAGIC))) continue;
      g_trade.OrderDelete(t);
   }
}

void CloseEverything(const string why)
{
   CancelAllPending(why);

   for(int k = PositionsTotal() - 1; k >= 0; k--)
   {
      const ulong t = PositionGetTicket(k);
      if(t == 0) continue;
      if(PositionGetString(POSITION_SYMBOL) != _Symbol) continue;
      const ulong magic = (ulong)PositionGetInteger(POSITION_MAGIC);
      if(!IsOurMagic(magic)) continue;

      g_trade.SetExpertMagicNumber(magic);
      if(g_trade.PositionClose(t) && InpVerboseLog)
         PrintFormat("Position %I64u closed (%s)", t, why);
   }
}
//+------------------------------------------------------------------+
