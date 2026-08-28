//+------------------------------------------------------------------+
//|                    XAUUSD_SessionBreakout_NewsSafe.mq5           |
//|                                                                  |
//|  The 2026 session-breakout portfolio, made safe to run on a      |
//|  prop-firm STANDARD account that restricts trading around        |
//|  high-impact news. The strategy geometry is untouched - this     |
//|  file differs from XAUUSD_SessionBreakout_2026.mq5 only in WHEN  |
//|  it is willing to have orders and positions in the market.       |
//|                                                                  |
//|  WHY THIS EXISTS                                                 |
//|  Swing accounts lift the news restriction, but at FTMO they run  |
//|  EUR 7.96 per $1,000 against EUR 5.40 at the $100k Standard      |
//|  tier and cap out at $25k, so buying the exemption costs both    |
//|  fee rate and allocation. This EA buys it in software instead.   |
//|                                                                  |
//|  THE PROBLEM, PRECISELY                                          |
//|  The rule reads on the moment a position is OPENED or CLOSED,    |
//|  not on when the order was placed. A resting Buy Stop above and  |
//|  a Sell Stop below are mechanically identical to a deliberate    |
//|  news straddle: the release itself triggers the fill. Placing    |
//|  the order six hours earlier is not a defence, because the harm  |
//|  the rule addresses - a fill at a simulated price the real book  |
//|  gapped straight through - is created at execution.              |
//|                                                                  |
//|  So the only real mitigation is to have NOTHING LIVE when the    |
//|  release lands. Three layers, most reliable first:               |
//|                                                                  |
//|   1. TRUNCATED PENDING LIFE. Orders carry a server-side          |
//|      ORDER_TIME_SPECIFIED expiry cut back to the start of the    |
//|      next blackout. The broker removes them even if no tick      |
//|      reaches the EA - OnTick is tick-driven and must not be the  |
//|      only defence.                                               |
//|   2. PRE-NEWS FLATTEN. An open position is closed BEFORE the     |
//|      window opens, because closing inside it is restricted too.  |
//|      A stop-loss firing on the spike is a close in the window    |
//|      that you did not choose to make.                            |
//|   3. TICK GUARD. Nothing arms while a blackout is open.          |
//|                                                                  |
//|  WHAT IT COSTS, AND THE RE-ARM THAT LIMITS IT                    |
//|  A plain blackout throws away every window that overlaps one.    |
//|  With InpRearmAfterNews the bracket is re-armed once the window  |
//|  passes, reusing the SAME high/low - the range is a property of  |
//|  the session hours, not of when we happened to look at it. The   |
//|  inherited "price already outside the bracket" check then does   |
//|  the right thing unaided: if the release broke the range, the    |
//|  day is skipped rather than chased. That is the honest outcome.  |
//|  The move we would have caught is precisely the one we are not   |
//|  allowed to take.                                                |
//|                                                                  |
//|  THE RULE THIS IS FITTED TO (FTMO, as published)                 |
//|  On an FTMO Account it is not permitted to open or close a trade  |
//|  on a targeted instrument within 2 minutes either side of a       |
//|  restricted release - and a Stop Loss or Take Profit triggering   |
//|  inside that window counts. XAUUSD is targeted, via USD. Holding  |
//|  a position that was opened more than 2 minutes before the event  |
//|  IS permitted. Breach may terminate the account.                  |
//|                                                                   |
//|  The restricted list is short and specific - six US releases:      |
//|    Federal Funds Rate & Statement      14:00 ET, 8x/year          |
//|    FOMC Meeting Minutes                14:00 ET, 8x/year          |
//|    Non-Farm Employment Change          08:30 ET, monthly          |
//|    Unemployment Rate & Wages           08:30 ET, with NFP         |
//|    CPI y/y                             08:30 ET, monthly          |
//|    Advance GDP q/q                     08:30 ET, quarterly        |
//|                                                                   |
//|  WHICH MEANS THE EXPOSURE IS SMALL, AND WORTH KNOWING BEFORE      |
//|  YOU TRUST THIS FILE. Under GEO_2026_NO_H13 the pendings are      |
//|  live 01:00-11:00 and 15:00-19:00 UTC. Every 08:30 ET release     |
//|  is 13:30 UTC in winter and 12:30 in summer - both inside the     |
//|  11:00-15:00 gap, with nothing armed. So the 08:30 group cannot   |
//|  touch this portfolio at all.                                     |
//|                                                                   |
//|  That leaves 14:00 ET: 19:00 UTC in winter, 18:00 in summer,      |
//|  against h14's orders. Sixteen days a year, one window. This EA   |
//|  exists for those sixteen days - which is the honest reason to    |
//|  prefer it over a Swing account rather than the other way round.  |
//|                                                                   |
//|  Run the ORIGINAL or GEO_2026 presets instead and h13 arms at     |
//|  13:30 UTC, exactly on the winter 08:30 ET print, and the         |
//|  exposure stops being marginal.                                   |
//|                                                                   |
//|  BLACKOUT SOURCES - the union of whichever are enabled           |
//|    CALENDAR  the terminal's economic calendar, filtered by       |
//|              importance and currency. Accurate, but returns      |
//|              nothing in the Strategy Tester on many builds.      |
//|    SCHEDULE  recurring New York clock times, DST-aware. Coarse   |
//|              and it over-blocks, but it cannot silently return   |
//|              an empty set the way the calendar can. By default   |
//|              it engages only on days the calendar came back      |
//|              empty, so a working calendar costs you nothing.     |
//|    CSV       explicit UTC timestamps from MQL5\Files. This is    |
//|              the source that lets you BACKTEST the filter.       |
//|                                                                  |
//|  ---------------------------------------------------------------|
//|  AN UNVERIFIED FILTER IS WORSE THAN NO FILTER: it buys           |
//|  confidence without buying protection. Before this runs on a     |
//|  funded account, read the startup log - it prints the day's      |
//|  blackouts in UTC and in New York time. Check one against a      |
//|  calendar you trust. Confirm in particular whether your terminal |
//|  reports calendar times in server time or in UTC.                |
//|  InpCalendarIsServerTime assumes server time; this file cannot   |
//|  verify that for you, and getting it wrong shifts every blackout |
//|  by the GMT offset.                                              |
//|  ---------------------------------------------------------------|
//|                                                                  |
//|  SET InpGMTOffsetHours FIRST. Windows are defined in UTC. On     |
//|  Exness servers this is 0 and holds year-round. On any other     |
//|  broker, check it - a wrong offset silently trades a completely  |
//|  different strategy AND misplaces every blackout.                |
//+------------------------------------------------------------------+
#property copyright "Session breakout portfolio - 2026 regime tune, news-safe"
#property version   "2.10"

#include <Trade\Trade.mqh>
#include <Trade\SymbolInfo.mqh>

#define MAX_WINDOWS 8

//--- What to do with a position that is already open when a blackout starts.
//--- Holding is permitted by the rule; it is the SL/TP firing inside the
//--- window that breaches. So all three of these are legal - they differ in
//--- which risk you would rather carry.
enum ENUM_NEWS_POS_MODE
{
   NEWS_POS_FLATTEN,       // Close before the window (safe, gives up the trade)
   NEWS_POS_DETACH_STOPS,  // Hold with SL/TP removed, restore after (no stop through the spike)
   NEWS_POS_HOLD           // Hold untouched - ONLY legal if SL/TP cannot fire in the window
};

enum ENUM_NEWS_IMPORTANCE
{
   NEWS_IMP_HIGH,      // High impact only
   NEWS_IMP_MODERATE   // Moderate and high
};

enum ENUM_PRESET
{
   PRESET_GEO_2026_NO_H13,  // 2026 geometry, 7 windows (recommended)
   PRESET_GEO_2026,         // 2026 geometry, 8 windows (exactly as tested)
   PRESET_TOP8_2026,        // 2026 hour re-selection (aggressive)
   PRESET_ORIGINAL          // original 2024-2025 configuration
};

//+------------------------------------------------------------------+
//| Inputs                                                           |
//+------------------------------------------------------------------+
input group "=== Broker clock (SET THIS FIRST) ==="
input int          InpGMTOffsetHours   = 0;      // Server time = GMT + this many hours
input bool         InpAutoDetectGMT    = true;   // Warn if the terminal disagrees

input group "=== Window set ==="
input ENUM_PRESET  InpPreset           = PRESET_GEO_2026_NO_H13;  // Which portfolio to trade

input group "=== Position sizing ==="
input double       InpBaseLots         = 0.02;   // Base lots per window
input bool         InpUseVolTargeting  = true;   // Scale size by trailing volatility
input double       InpTargetVolPct     = 0.816;  // Reference daily realised vol (%)
input int          InpVolLookbackDays  = 20;     // Sessions in the trailing vol average
input double       InpMaxVolScale      = 3.0;    // Cap on the multiplier (floor is 1/x)

input group "=== Risk guards ==="
input double       InpMaxSpreadUSD     = 0.30;   // Skip entries above this spread, USD/oz (0 = off)
input int          InpMaxOpenPositions = 8;      // Portfolio-wide cap on concurrent positions
input double       InpMaxDailyLossPct  = 0.0;    // Halt for the day past this % loss (0 = off)

input group "=== Session rules ==="
input double       InpOrderExpiryHours = 4.0;    // Cancel unfilled orders this long after the bracket
input bool         InpFlatAnchorNY     = true;   // Anchor the flatten to the NY halt (DST-aware)
input int          InpFlatLeadMinutes  = 5;      // ...this many minutes before the 16:58 NY halt
input int          InpDailyFlatUTCH    = 21;     // Fixed-UTC fallback, only when InpFlatAnchorNY = false
input int          InpDailyFlatUTCM    = 50;     // Fixed-UTC fallback minute (23/57 = original behaviour)
input int          InpFridayCloseUTCH  = 20;     // Friday: flatten at this UTC hour (24 = off)
input double       InpMinRangePct      = 0.05;   // Skip brackets narrower than this % of price
input double       InpMaxRangePct      = 2.00;   // Skip brackets wider than this % of price
input bool         InpSkipSunday       = true;   // Skip the thin Sunday session
input bool         InpAdjustBidToMid   = true;   // Bars are bid-priced; shift to mid

input group "=== News blackout ==="
input bool         InpNewsFilter        = true;  // Master switch for the whole news layer
input bool         InpUseCalendar       = true;  // Source: terminal economic calendar
input bool         InpUseSchedule       = true;  // Source: recurring New York clock times
input string       InpNewsCsvFile       = "";    // Source: UTC timestamps in MQL5\Files (blank = off)
input bool         InpMatchByName       = true;  // Calendar: match the restricted list by name
input string       InpRestrictedEvents  = "Nonfarm,Non-Farm,Unemployment Rate,Average Hourly,Federal Funds,Interest Rate Decision,FOMC,CPI,Consumer Price,GDP"; // ...these substrings
input ENUM_NEWS_IMPORTANCE InpMinImportance = NEWS_IMP_HIGH; // Calendar: floor when not matching by name
input string       InpNewsCurrencies    = "USD"; // Calendar: comma-separated currencies
input string       InpNewsScheduleET    = "08:30,14:00"; // Schedule: New York times, Mon-Fri
input bool         InpScheduleFallbackOnly = true; // Schedule only on days the calendar came back empty
input int          InpNewsPadBefore     = 5;     // Blackout opens this many minutes before the release
input int          InpNewsPadAfter      = 5;     // ...and closes this many minutes after
input ENUM_NEWS_POS_MODE InpNewsPosMode = NEWS_POS_FLATTEN; // Open position when a blackout starts
input int          InpPreNewsFlatSec    = 60;    // ...acted on this many seconds before it opens
input bool         InpRearmAfterNews    = true;  // Re-arm an unfilled bracket once the blackout passes
input int          InpMinOrderLifeMin   = 10;    // Do not arm if the blackout leaves less life than this
input bool         InpCalendarIsServerTime = true; // Calendar stamps are server time, not UTC
input int          InpCalendarRefreshMin = 30;   // Re-query the calendar this often (minutes)

input group "=== Bookkeeping ==="
input ulong        InpMagicBase        = 8830000; // Magic base+0 .. base+7 (NOT the base EA's 8820000)
input int          InpSlippagePoints   = 20;      // Max deviation on market close-outs
input bool         InpVerboseLog       = true;    // Narrate decisions to the Experts log

//+------------------------------------------------------------------+
//| Per-window definition and daily state                            |
//+------------------------------------------------------------------+
struct SessionWindow
{
   string   name;
   int      hour;          // UTC hour the bracket starts
   int      rangeMin;      // bracket length, minutes
   double   targetMult;    // take profit as a multiple of bracket width
   ulong    magic;

   int      armedDay;      // UTC day key the orders were placed
   int      doneDay;       // UTC day key this window already traded
   double   hi;            // bracket high (mid)
   double   lo;            // bracket low  (mid)
   datetime expiryUTC;
   bool     cutForNews;    // pendings were cut short by a blackout, not by natural expiry
   bool     detached;      // SL/TP lifted for the duration of a blackout
   ulong    detTicket;     // ...from this position
   double   detSL;
   double   detTP;
};

SessionWindow g_win[MAX_WINDOWS];
int           g_count = 0;
CTrade        g_trade;
CSymbolInfo   g_sym;

double g_volScale       = 1.0;
int    g_volScaleDay    = -1;
double g_dayStartEquity = 0.0;
int    g_equityDay      = -1;
bool   g_haltedToday    = false;

//+------------------------------------------------------------------+
//| News blackout state                                              |
//+------------------------------------------------------------------+
struct NewsBlackout
{
   datetime from;       // UTC, padding already applied
   datetime to;         // UTC, padding already applied
   string   tag;
};

NewsBlackout g_bo[];
int      g_boCount   = 0;
int      g_boDay     = -1;         // UTC day key the list was built for
datetime g_boBuiltAt = 0;          // UTC time of the last calendar query
bool     g_expirySupported = true; // broker accepts ORDER_TIME_SPECIFIED
int      g_calHighSeen = 0;        // high-impact events the calendar returned
datetime g_csvEvent[];
int      g_csvCount  = 0;

//+------------------------------------------------------------------+
//| Clock - the whole strategy is defined in UTC                     |
//+------------------------------------------------------------------+
datetime UTCNow()                        { return TimeCurrent() - (datetime)(InpGMTOffsetHours * 3600); }
datetime ServerFromUTC(const datetime u) { return u + (datetime)(InpGMTOffsetHours * 3600); }

//--- The Exness XAUUSD daily break is anchored to 17:00 New York, so in UTC
//--- it moves with US DST: 20:58->~22:01 in summer, 21:58->~23:01 in winter.
//--- Verified per-day against raw ticks over 2024-2026 (513 halt days): the
//--- last tick lands at HH:57:58 in 87% of sessions, never after HH:57:59.
//--- The server CLOCK is UTC year-round - it is the SESSION SCHEDULE that
//--- moves. Both are true; do not conflate them.
datetime NthSundayUTC(const int year, const int month, const int nth)
{
   MqlDateTime d;
   d.year = year; d.mon = month; d.day = 1;
   d.hour = 0;    d.min = 0;     d.sec = 0;
   MqlDateTime f;
   TimeToStruct(StructToTime(d), f);
   d.day = 1 + ((7 - f.day_of_week) % 7) + 7 * (nth - 1);
   return StructToTime(d);
}

//--- US DST: second Sunday in March to first Sunday in November.
bool IsUSDST(const datetime utc)
{
   MqlDateTime d;
   TimeToStruct(utc, d);
   return (utc >= NthSundayUTC(d.year, 3, 2) && utc < NthSundayUTC(d.year, 11, 1));
}

//--- UTC second-of-day at which the daily flatten fires.
int DailyFlatSecUTC(const datetime utc)
{
   if(!InpFlatAnchorNY)
      return InpDailyFlatUTCH * 3600 + InpDailyFlatUTCM * 60;
   const int halt = IsUSDST(utc) ? (20 * 3600 + 58 * 60) : (21 * 3600 + 58 * 60);
   return halt - InpFlatLeadMinutes * 60;
}

//--- YYYYMMDD in UTC; never repeats across years the way day_of_year does
int DayKeyOf(const datetime utc)
{
   MqlDateTime dt;
   TimeToStruct(utc, dt);
   return dt.year * 10000 + dt.mon * 100 + dt.day;
}
int UTCDayKey() { return DayKeyOf(UTCNow()); }

//--- Terminal-persistent names. The day's opening equity is the only piece of
//--- state no order carries, so it is the only thing that has to be stored.
//--- Keyed by magic base so two instances never share a baseline.
string DayKeyVarName()    { return StringFormat("SB2026_%I64u_daykey",  InpMagicBase); }
string DayEquityVarName() { return StringFormat("SB2026_%I64u_starteq", InpMagicBase); }

datetime UTCTodayAtHour(const int hour)
{
   MqlDateTime dt;
   TimeToStruct(UTCNow(), dt);
   dt.hour = hour; dt.min = 0; dt.sec = 0;
   return StructToTime(dt);
}

int UTCHourOf(const datetime utc)   { MqlDateTime d; TimeToStruct(utc, d); return d.hour; }
bool IsUTCSunday(const datetime utc){ MqlDateTime d; TimeToStruct(utc, d); return (d.day_of_week == 0); }
bool IsUTCFriday(const datetime utc){ MqlDateTime d; TimeToStruct(utc, d); return (d.day_of_week == 5); }

//+------------------------------------------------------------------+
//| News blackouts                                                   |
//|                                                                  |
//| Intervals are stored padded and merged, in UTC. Merging matters:  |
//| two releases 3 minutes apart with 5-minute padding are ONE        |
//| blackout, and treating them as two would let the EA re-arm into   |
//| the gap between them.                                             |
//+------------------------------------------------------------------+
void AddBlackout(const datetime evtUTC, const string tag)
{
   const datetime from = evtUTC - (datetime)(InpNewsPadBefore * 60);
   const datetime to   = evtUTC + (datetime)(InpNewsPadAfter * 60);

   //--- merge into any interval it touches
   for(int k = 0; k < g_boCount; k++)
   {
      if(from > g_bo[k].to || to < g_bo[k].from)
         continue;
      if(from < g_bo[k].from) g_bo[k].from = from;
      if(to   > g_bo[k].to)   g_bo[k].to   = to;
      if(StringFind(g_bo[k].tag, tag) < 0)
         g_bo[k].tag = g_bo[k].tag + " + " + tag;
      return;
   }
   ArrayResize(g_bo, g_boCount + 1);
   g_bo[g_boCount].from = from;
   g_bo[g_boCount].to   = to;
   g_bo[g_boCount].tag  = tag;
   g_boCount++;
}

//--- insertion sort by start time; the list is a handful of entries a day
void SortBlackouts()
{
   for(int i = 1; i < g_boCount; i++)
   {
      NewsBlackout key = g_bo[i];
      int j = i - 1;
      while(j >= 0 && g_bo[j].from > key.from)
      {
         g_bo[j + 1] = g_bo[j];
         j--;
      }
      g_bo[j + 1] = key;
   }

   //--- coalesce: an event added later can bridge two intervals that did not
   //--- touch when they were created, and a phantom gap between them would let
   //--- the EA re-arm mid-blackout
   int w = 0;
   for(int r = 1; r < g_boCount; r++)
   {
      if(g_bo[r].from <= g_bo[w].to)
      {
         if(g_bo[r].to > g_bo[w].to)
            g_bo[w].to = g_bo[r].to;
         g_bo[w].tag = g_bo[w].tag + " + " + g_bo[r].tag;
      }
      else
         g_bo[++w] = g_bo[r];
   }
   if(g_boCount > 0)
      g_boCount = w + 1;
}

//+------------------------------------------------------------------+
//| Source 1: the terminal's economic calendar.                      |
//|                                                                  |
//| Returns the number of events found. Zero is AMBIGUOUS - it means  |
//| either a genuinely quiet day or a calendar that is unavailable    |
//| (the Strategy Tester on most builds, or a terminal that has not   |
//| synchronised). That ambiguity is the whole reason the schedule    |
//| source exists and defaults to on.                                 |
//+------------------------------------------------------------------+
int LoadCalendarBlackouts(const datetime fromUTC, const datetime toUTC)
{
   string cur[];
   const int nCur = StringSplit(InpNewsCurrencies, ',', cur);
   if(nCur <= 0)
      return 0;

   g_calHighSeen = 0;
   const ENUM_CALENDAR_EVENT_IMPORTANCE floor =
      (InpMinImportance == NEWS_IMP_HIGH) ? CALENDAR_IMPORTANCE_HIGH
                                          : CALENDAR_IMPORTANCE_MODERATE;
   int found = 0;
   for(int c = 0; c < nCur; c++)
   {
      const string code = StringTrim(cur[c]);
      if(StringLen(code) == 0)
         continue;

      MqlCalendarValue vals[];
      const int n = CalendarValueHistory(vals, ToCalendarTime(fromUTC),
                                         ToCalendarTime(toUTC), NULL, code);
      for(int k = 0; k < n; k++)
      {
         MqlCalendarEvent evt;
         if(!CalendarEventById(vals[k].event_id, evt))
            continue;
         if(evt.importance >= floor)
            g_calHighSeen++;

         //--- FTMO restricts six named releases, not "high impact USD news".
         //--- Matching the list by name keeps the blackouts to roughly five a
         //--- month instead of one most days, which is the difference between
         //--- a filter you can afford to leave on and one you cannot.
         if(InpMatchByName)
         {
            if(!NameIsRestricted(evt.name))
               continue;
         }
         else if(evt.importance < floor)
            continue;

         AddBlackout(FromCalendarTime(vals[k].time), code + " " + evt.name);
         found++;
      }
   }
   return found;
}

//--- Substring match, case-insensitive. Provider naming varies ("Nonfarm
//--- Payrolls" vs "Non-Farm Employment Change"), which is why the default
//--- list carries several spellings of the same release.
bool NameIsRestricted(const string name)
{
   string want[];
   const int n = StringSplit(InpRestrictedEvents, ',', want);
   string hay = name;
   StringToLower(hay);
   for(int k = 0; k < n; k++)
   {
      string needle = StringTrim(want[k]);
      if(StringLen(needle) == 0)
         continue;
      StringToLower(needle);
      if(StringFind(hay, needle) >= 0)
         return true;
   }
   return false;
}

//--- The calendar reports in server time on every build I know of, but that
//--- is a broker-dependent assumption, not a documented guarantee - hence
//--- the input. On Exness (GMT+0) both branches agree and this is moot.
datetime ToCalendarTime(const datetime utc)  { return InpCalendarIsServerTime ? ServerFromUTC(utc) : utc; }
datetime FromCalendarTime(const datetime t)  { return InpCalendarIsServerTime ? (t - (datetime)(InpGMTOffsetHours * 3600)) : t; }

string StringTrim(string s)
{
   StringTrimLeft(s);
   StringTrimRight(s);
   return s;
}

//+------------------------------------------------------------------+
//| Source 2: recurring New York clock times.                        |
//|                                                                  |
//| Deliberately blunt. 08:30 ET is 13:30 UTC in winter and 12:30 in  |
//| summer - the DST shift is exactly why these are specified in New  |
//| York time and converted here rather than hard-coded in UTC.       |
//|                                                                  |
//| This over-blocks: 10:00 ET carries a high-impact release only a   |
//| few days a month, and 14:00 ET only on the eight FOMC days. The   |
//| trade is deliberate - a false blackout costs one window, a missed |
//| release costs the account.                                        |
//+------------------------------------------------------------------+
int LoadScheduleBlackouts(const datetime dayStartUTC)
{
   MqlDateTime d;
   TimeToStruct(dayStartUTC, d);
   if(d.day_of_week == 0 || d.day_of_week == 6)
      return 0;

   string parts[];
   const int n = StringSplit(InpNewsScheduleET, ',', parts);
   const int etToUTC = IsUSDST(dayStartUTC) ? 4 : 5;   // EDT = UTC-4, EST = UTC-5

   int added = 0;
   for(int k = 0; k < n; k++)
   {
      string hm[];
      if(StringSplit(StringTrim(parts[k]), ':', hm) != 2)
         continue;
      const int hh = (int)StringToInteger(hm[0]);
      const int mm = (int)StringToInteger(hm[1]);
      AddBlackout(dayStartUTC + (datetime)((hh + etToUTC) * 3600 + mm * 60),
                  StringFormat("schedule %02d:%02d NY", hh, mm));
      added++;
   }
   return added;
}

//+------------------------------------------------------------------+
//| Source 3: a CSV of UTC timestamps, one per line, "YYYY.MM.DD HH:MM".|
//| Read once at init. This is what makes the filter testable - the   |
//| calendar API is empty in the Strategy Tester on most builds, so    |
//| without this you would be backtesting a filter that never fires.   |
//+------------------------------------------------------------------+
void LoadCsvEvents()
{
   g_csvCount = 0;
   if(StringLen(InpNewsCsvFile) == 0)
      return;

   //--- Strategy Tester agents read from their own sandbox, so a file dropped in
   //--- MQL5\Files is invisible there. Fall back to Common\Files, which both see.
   int fh = FileOpen(InpNewsCsvFile, FILE_READ | FILE_TXT | FILE_ANSI);
   if(fh == INVALID_HANDLE)
      fh = FileOpen(InpNewsCsvFile, FILE_READ | FILE_TXT | FILE_ANSI | FILE_COMMON);
   if(fh == INVALID_HANDLE)
   {
      PrintFormat("WARNING: news CSV '%s' opened from neither MQL5\\Files nor "
                  "Common\\Files (%d) - that source is OFF.",
                  InpNewsCsvFile, GetLastError());
      return;
   }
   while(!FileIsEnding(fh))
   {
      const string line = StringTrim(FileReadString(fh));
      if(StringLen(line) < 10 || StringGetCharacter(line, 0) == '#')
         continue;
      const datetime t = StringToTime(line);
      if(t <= 0)
      {
         PrintFormat("WARNING: unparsable line in %s: '%s'", InpNewsCsvFile, line);
         continue;
      }
      ArrayResize(g_csvEvent, g_csvCount + 1);
      g_csvEvent[g_csvCount++] = t;
   }
   FileClose(fh);
   PrintFormat("News CSV: %d event times loaded from %s", g_csvCount, InpNewsCsvFile);
}

int LoadCsvBlackouts(const datetime fromUTC, const datetime toUTC)
{
   int added = 0;
   for(int k = 0; k < g_csvCount; k++)
   {
      if(g_csvEvent[k] < fromUTC || g_csvEvent[k] > toUTC)
         continue;
      AddBlackout(g_csvEvent[k], "csv");
      added++;
   }
   return added;
}

//+------------------------------------------------------------------+
//| Rebuild the day's blackout list. Cheap enough to re-run, but the  |
//| calendar query is not free, so it is rate-limited.                |
//+------------------------------------------------------------------+
void RefreshBlackouts(const datetime utc, const int today)
{
   if(!InpNewsFilter)
      return;
   const bool newDay = (g_boDay != today);
   if(!newDay && g_boBuiltAt > 0
      && utc - g_boBuiltAt < (datetime)(InpCalendarRefreshMin * 60))
      return;

   g_boCount = 0;
   ArrayResize(g_bo, 0);
   g_boDay     = today;
   g_boBuiltAt = utc;

   //--- one hour of margin each side so a release just past midnight, or a
   //--- pending order that outlives the day, is still covered
   const datetime dayStart = UTCTodayAtHour(0);
   const datetime from     = dayStart - 3600;
   const datetime to       = dayStart + 25 * 3600;

   int nCal = 0, nSch = 0, nCsv = 0;
   if(InpUseCalendar) nCal = LoadCalendarBlackouts(from, to);

   //--- With the calendar working, the schedule only adds false blackouts:
   //--- 14:00 ET is restricted on 16 days a year, and blocking h14 on the
   //--- other 234 is a real cost for no protection. So by default the
   //--- schedule is a fallback for the days the calendar gives us nothing.
   const bool needFallback = (!InpScheduleFallbackOnly || nCal == 0);
   if(InpUseSchedule && needFallback) nSch = LoadScheduleBlackouts(dayStart);
   nCsv = LoadCsvBlackouts(from, to);
   SortBlackouts();

   if(newDay || InpVerboseLog)
   {
      PrintFormat("News blackouts for %d: %d interval(s) [calendar %d, schedule %d, csv %d]",
                  today, g_boCount, nCal, nSch, nCsv);
      if(InpUseCalendar && nCal == 0 && g_calHighSeen == 0)
         Print("NOTE: the calendar returned no events at all. Quiet day, or "
               "unavailable (it is empty in the Strategy Tester on most builds).");
      else if(InpUseCalendar && nCal == 0 && g_calHighSeen > 0)
         PrintFormat("WARNING: %d high-impact event(s) today, none matched "
                     "InpRestrictedEvents. Correct if none are on FTMO's list - "
                     "but check the names before trusting it.", g_calHighSeen);
      for(int k = 0; k < g_boCount; k++)
         PrintFormat("  %s - %s UTC  (%s NY)  %s",
                     TimeToString(g_bo[k].from, TIME_MINUTES),
                     TimeToString(g_bo[k].to,   TIME_MINUTES),
                     TimeToString(g_bo[k].from - (datetime)((IsUSDST(g_bo[k].from) ? 4 : 5) * 3600),
                                  TIME_MINUTES),
                     g_bo[k].tag);
   }
}

//+------------------------------------------------------------------+
//| Queries                                                          |
//+------------------------------------------------------------------+
bool InBlackout(const datetime utc, string &tag)
{
   for(int k = 0; k < g_boCount; k++)
   {
      if(utc >= g_bo[k].from && utc <= g_bo[k].to)
      {
         tag = g_bo[k].tag;
         return true;
      }
   }
   return false;
}

//--- Start of the earliest blackout that has not finished yet. Returns 0
//--- when the rest of the day is clear.
datetime NextBlackoutStart(const datetime utc)
{
   for(int k = 0; k < g_boCount; k++)
      if(g_bo[k].to > utc)
         return g_bo[k].from;
   return 0;
}

datetime BlackoutEndAt(const datetime utc)
{
   for(int k = 0; k < g_boCount; k++)
      if(utc >= g_bo[k].from && utc <= g_bo[k].to)
         return g_bo[k].to;
   return 0;
}

//--- The flatten fires BEFORE the interval opens, so the close itself lands
//--- outside the restricted window. Closing inside it would be the very
//--- violation the flatten exists to avoid.
bool InFlattenLead(const datetime utc)
{
   for(int k = 0; k < g_boCount; k++)
      if(utc >= g_bo[k].from - (datetime)InpPreNewsFlatSec && utc <= g_bo[k].to)
         return true;
   return false;
}

//+------------------------------------------------------------------+
//| Presets                                                          |
//+------------------------------------------------------------------+
void AddWindow(const string name, const int hour, const int rangeMin, const double tgt)
{
   if(g_count >= MAX_WINDOWS)
      return;
   const int i = g_count++;
   g_win[i].name       = name;
   g_win[i].hour       = hour;
   g_win[i].rangeMin   = rangeMin;
   g_win[i].targetMult = tgt;
   g_win[i].magic      = InpMagicBase + (ulong)i;
   g_win[i].armedDay   = -1;
   g_win[i].doneDay    = -1;
   g_win[i].hi         = 0.0;
   g_win[i].lo         = 0.0;
   g_win[i].expiryUTC  = 0;
   g_win[i].cutForNews = false;
   g_win[i].detached   = false;
   g_win[i].detTicket  = 0;
   g_win[i].detSL      = 0.0;
   g_win[i].detTP      = 0.0;
}

string LoadPreset(const ENUM_PRESET p)
{
   g_count = 0;
   switch(p)
   {
      case PRESET_GEO_2026_NO_H13:
         AddWindow("h00_r60_t3",  0, 60, 3.0);
         AddWindow("h01_r60_t3",  1, 60, 3.0);
         AddWindow("h02_r60_t3",  2, 60, 3.0);
         AddWindow("h04_r60_t3",  4, 60, 3.0);
         AddWindow("h05_r60_t3",  5, 60, 3.0);
         AddWindow("h06_r60_t3",  6, 60, 3.0);
         AddWindow("h14_r60_t3", 14, 60, 3.0);
         return "GEO_2026_NO_H13 (7 windows)";

      case PRESET_GEO_2026:
         AddWindow("h00_r60_t3",  0, 60, 3.0);
         AddWindow("h01_r60_t3",  1, 60, 3.0);
         AddWindow("h02_r60_t3",  2, 60, 3.0);
         AddWindow("h04_r60_t3",  4, 60, 3.0);
         AddWindow("h05_r60_t3",  5, 60, 3.0);
         AddWindow("h06_r60_t3",  6, 60, 3.0);
         AddWindow("h13_r60_t3", 13, 60, 3.0);
         AddWindow("h14_r60_t3", 14, 60, 3.0);
         return "GEO_2026 (8 windows, as tested)";

      case PRESET_TOP8_2026:
         AddWindow("h00_r30_t3",  0, 30, 3.0);
         AddWindow("h01_r60_t3",  1, 60, 3.0);
         AddWindow("h02_r30_t3",  2, 30, 3.0);
         AddWindow("h05_r30_t3",  5, 30, 3.0);
         AddWindow("h08_r60_t3",  8, 60, 3.0);
         AddWindow("h14_r60_t3", 14, 60, 3.0);
         AddWindow("h15_r60_t3", 15, 60, 3.0);
         AddWindow("h18_r60_t3", 18, 60, 3.0);
         return "TOP8_2026 (8 windows, 2026 hour re-selection)";

      default:
         AddWindow("h00_r30_t1",  0, 30, 1.0);
         AddWindow("h01_r60_t3",  1, 60, 3.0);
         AddWindow("h02_r15_t3",  2, 15, 3.0);
         AddWindow("h04_r30_t3",  4, 30, 3.0);
         AddWindow("h05_r60_t2",  5, 60, 2.0);
         AddWindow("h06_r60_t3",  6, 60, 3.0);
         AddWindow("h13_r30_t3", 13, 30, 3.0);
         AddWindow("h14_r15_t2", 14, 15, 2.0);
         return "ORIGINAL (2024-2025 configuration)";
   }
}

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

   const string preset = LoadPreset(InpPreset);

   PrintFormat("Session breakout 2026 on %s | preset %s | server %s | assumed GMT%+d | UTC %s",
               _Symbol, preset,
               TimeToString(TimeCurrent(), TIME_DATE | TIME_SECONDS),
               InpGMTOffsetHours,
               TimeToString(UTCNow(), TIME_DATE | TIME_SECONDS));

   if(InpAutoDetectGMT && !MQLInfoInteger(MQL_TESTER))
   {
      const int detected = (int)MathRound((double)(TimeCurrent() - TimeGMT()) / 3600.0);
      if(detected != InpGMTOffsetHours)
         PrintFormat("WARNING: terminal suggests the server is GMT%+d but InpGMTOffsetHours=%d. "
                     "Verify before trading.", detected, InpGMTOffsetHours);
   }
   //--- a flatten time earlier than the last bracket would silently kill windows
   int lastArm = 0;
   for(int i = 0; i < g_count; i++)
      lastArm = MathMax(lastArm, g_win[i].hour * 60 + g_win[i].rangeMin);
   const int flatMin = DailyFlatSecUTC(UTCNow()) / 60;
   const int haltMin = flatMin + (InpFlatAnchorNY ? InpFlatLeadMinutes : 0);
   if(flatMin <= lastArm)
      PrintFormat("WARNING: daily flatten %02d:%02d is at or before the last bracket closes "
                  "(%02d:%02d) - those windows will never trade.",
                  flatMin / 60, flatMin % 60, lastArm / 60, lastArm % 60);
   else if(InpFlatAnchorNY)
      PrintFormat("Daily flatten %02d:%02d UTC = %d min before the %02d:%02d halt "
                  "(16:58 New York; US DST %s today; last bracket closes %02d:%02d)",
                  flatMin / 60, flatMin % 60, InpFlatLeadMinutes,
                  haltMin / 60, haltMin % 60, IsUSDST(UTCNow()) ? "ON" : "off",
                  lastArm / 60, lastArm % 60);
   else
      PrintFormat("WARNING: fixed-UTC flatten %02d:%02d - this lands inside the summer halt "
                  "(20:58-22:01) and will pay swap on ~66%% of days. Last bracket %02d:%02d.",
                  flatMin / 60, flatMin % 60, lastArm / 60, lastArm % 60);

   //--- Re-init is not rare and is mostly not chosen: a restart, a reconnect,
   //--- a recompile and an edit to the inputs all land here. Rebuild the
   //--- day's state from the market BEFORE the first tick can re-arm anything.
   RollDailyState(UTCDayKey());
   AdoptExistingState();

   //--- Server-side expiry is defence layer 1. Without it the only thing
   //--- removing an order before a release is a tick arriving in time, and
   //--- the quiet minute before a print is exactly when ticks are scarce.
   const long expFlags = SymbolInfoInteger(_Symbol, SYMBOL_EXPIRATION_MODE);
   g_expirySupported = ((expFlags & SYMBOL_EXPIRATION_SPECIFIED) != 0);
   if(!g_expirySupported)
      Print("WARNING: this symbol rejects ORDER_TIME_SPECIFIED. Falling back to "
            "GTC plus a tick-driven cancel - strictly weaker, because no tick "
            "means no cancel. Consider a shorter InpOrderExpiryHours.");

   if(InpNewsFilter)
   {
      LoadCsvEvents();
      RefreshBlackouts(UTCNow(), UTCDayKey());

      if(!InpUseCalendar && !InpUseSchedule && g_csvCount == 0)
         Print("WARNING: InpNewsFilter is on but every source is off. "
               "The filter will never fire.");
      if(InpNewsPadBefore < 3 || InpNewsPadAfter < 3)
         PrintFormat("WARNING: padding %d/%d min is tight against a 2-minute rule. "
                     "Release times drift and fills are not instantaneous.",
                     InpNewsPadBefore, InpNewsPadAfter);
      if(InpNewsPosMode == NEWS_POS_HOLD)
         Print("WARNING: InpNewsPosMode = HOLD. Positions ride through blackouts "
               "WITH their stops attached, and a SL/TP hit inside the window is "
               "exactly the breach this EA exists to prevent. Legal only if you "
               "are certain no stop can be reached.");
      if(InpNewsPosMode == NEWS_POS_DETACH_STOPS)
         Print("NOTE: InpNewsPosMode = DETACH_STOPS. Positions are held unprotected "
               "for the length of each blackout.");
      WarnOnNakedPositions();
   }
   else
      Print("NOTE: InpNewsFilter is OFF - strategy behaviour matches the base EA, "
            "except that pendings still carry a server-side expiry.");

   if(InpBaseLots < g_sym.LotsMin())
      PrintFormat("WARNING: InpBaseLots %.4f is under the broker minimum %.4f; orders will be raised.",
                  InpBaseLots, g_sym.LotsMin());
   return INIT_SUCCEEDED;
}

//--- A restart in the middle of a blackout loses the saved SL/TP, and the
//--- position would stay naked for the rest of the session with nothing to
//--- notice. This cannot repair it - the bracket that defined the stops is
//--- gone - so it does the one useful thing: says so, loudly.
void WarnOnNakedPositions()
{
   for(int k = PositionsTotal() - 1; k >= 0; k--)
   {
      const ulong t = PositionGetTicket(k);
      if(t == 0) continue;
      if(PositionGetString(POSITION_SYMBOL) != _Symbol) continue;
      if(!IsOurMagic((ulong)PositionGetInteger(POSITION_MAGIC))) continue;
      if(PositionGetDouble(POSITION_SL) != 0.0) continue;

      PrintFormat("WARNING: position %I64u has NO stop loss. If this EA restarted "
                  "during a blackout with DETACH_STOPS, the saved levels are lost. "
                  "Set them by hand or close it - the daily flatten is the only "
                  "protection it now has.", t);
   }
}

void OnDeinit(const int reason) { }

//+------------------------------------------------------------------+
void OnTick()
{
   if(!g_sym.RefreshRates())
      return;

   const datetime utc   = UTCNow();
   const int      today = UTCDayKey();

   RollDailyState(today);
   EnforceDailyLossHalt();

   //--- Flatten rules, most reliable first. A clock-window check alone is
   //--- not enough: on Friday the market can close before the window is
   //--- reached, no tick lands inside it, and the position rides the gap.
   //--- The stale sweep is the backstop that makes daily-flat actually hold.
   if(CloseStaleFromEarlierSessions() > 0)
      return;

   if(InpFridayCloseUTCH < 24 && IsUTCFriday(utc) && UTCHourOf(utc) >= InpFridayCloseUTCH)
   {
      CloseEverything("friday cutoff");
      CancelAllPending("friday cutoff");
      return;
   }

   if(PastDailyFlatten(utc))
   {
      CloseEverything("daily flatten");      // also cancels every pending order
      return;
   }

   if(g_haltedToday)
   {
      CancelAllPending("daily loss halt");
      return;
   }

   RefreshBlackouts(utc, today);
   EnforceNewsBlackout(utc);
   RestoreStops(utc);

   for(int i = 0; i < g_count; i++)
      ProcessWindow(i, utc, today);
}

//+------------------------------------------------------------------+
//| Layer 2 and 3: flatten ahead of the window, then stand aside.     |
//|                                                                   |
//| Pendings are deleted rather than left to expire. Deleting a        |
//| pending order is not opening or closing a position, so it is safe  |
//| inside the window itself - it is the fill that is restricted, not  |
//| the housekeeping.                                                  |
//+------------------------------------------------------------------+
void EnforceNewsBlackout(const datetime utc)
{
   if(!InpNewsFilter || !InFlattenLead(utc))
      return;

   string tag = "";
   const bool inside = InBlackout(utc, tag);

   for(int i = 0; i < g_count; i++)
      if(g_win[i].armedDay == UTCDayKey() && HasPending(g_win[i].magic))
      {
         CancelWindowPending(i, "news blackout");
         g_win[i].cutForNews = true;
         g_win[i].expiryUTC  = utc;   // so the re-arm branch sees an expired window
      }

   if(CountOurPositions() == 0)
      return;

   if(InpNewsPosMode == NEWS_POS_FLATTEN)
   {
      CloseEverything(inside ? "news blackout (late - check the log)"
                             : "pre-news flatten");
      return;
   }
   if(InpNewsPosMode == NEWS_POS_DETACH_STOPS)
      DetachStops();
}

//+------------------------------------------------------------------+
//| Hold through the window with the stops lifted.                    |
//|                                                                   |
//| The rule permits holding a position opened more than 2 minutes     |
//| before the release; what it forbids is a close inside the window,  |
//| and it names Stop Loss and Take Profit explicitly. Removing them   |
//| for four minutes is therefore the precise instrument - modifying   |
//| a position is neither opening nor closing.                         |
//|                                                                   |
//| The cost is real and should not be glossed: for those minutes the  |
//| position is naked through the most violent gold event of the       |
//| month. FLATTEN remains the default for that reason.                |
//+------------------------------------------------------------------+
void DetachStops()
{
   for(int i = 0; i < g_count; i++)
   {
      if(g_win[i].detached)
         continue;
      const ulong t = FindPosition(g_win[i].magic);
      if(t == 0 || !PositionSelectByTicket(t))
         continue;

      g_win[i].detSL     = PositionGetDouble(POSITION_SL);
      g_win[i].detTP     = PositionGetDouble(POSITION_TP);
      g_win[i].detTicket = t;

      g_trade.SetExpertMagicNumber(g_win[i].magic);
      if(g_trade.PositionModify(t, 0.0, 0.0))
      {
         g_win[i].detached = true;
         PrintFormat("%s: SL %.2f / TP %.2f lifted for the blackout - the position "
                     "is UNPROTECTED until it passes", g_win[i].name,
                     g_win[i].detSL, g_win[i].detTP);
      }
      else
         PrintFormat("%s: FAILED to lift SL/TP (%d) - the stops can still fire "
                     "inside the window. Close manually if it matters.",
                     g_win[i].name, g_trade.ResultRetcode());
   }
}

void RestoreStops(const datetime utc)
{
   if(InpNewsFilter && InFlattenLead(utc))
      return;
   for(int i = 0; i < g_count; i++)
   {
      if(!g_win[i].detached)
         continue;
      if(!PositionSelectByTicket(g_win[i].detTicket))
      {
         g_win[i].detached = false;      // gone; nothing to restore
         continue;
      }
      g_trade.SetExpertMagicNumber(g_win[i].magic);
      if(g_trade.PositionModify(g_win[i].detTicket, g_win[i].detSL, g_win[i].detTP))
      {
         g_win[i].detached = false;
         PrintFormat("%s: SL %.2f / TP %.2f restored", g_win[i].name,
                     g_win[i].detSL, g_win[i].detTP);
      }
      else
         PrintFormat("%s: FAILED to restore SL/TP (%d) - retrying next tick",
                     g_win[i].name, g_trade.ResultRetcode());
   }
}

bool HasPending(const ulong magic)
{
   for(int k = OrdersTotal() - 1; k >= 0; k--)
   {
      const ulong t = OrderGetTicket(k);
      if(t == 0) continue;
      if((ulong)OrderGetInteger(ORDER_MAGIC) == magic)
         return true;
   }
   return false;
}

//+------------------------------------------------------------------+
void RollDailyState(const int today)
{
   if(g_equityDay == today)
      return;
   g_equityDay   = today;
   g_haltedToday = false;

   //--- Re-sampling opening equity on every re-init walks the daily-loss
   //--- baseline down with each restart: the halt would then measure from the
   //--- restart rather than from the day, which is silently no protection at
   //--- all. The halt FLAG needs no persistence - with the baseline correct,
   //--- EnforceDailyLossHalt re-trips on the next tick by itself.
   const string kDay = DayKeyVarName(), kEq = DayEquityVarName();
   if(GlobalVariableCheck(kDay) && (int)GlobalVariableGet(kDay) == today
      && GlobalVariableCheck(kEq) && GlobalVariableGet(kEq) > 0.0)
   {
      g_dayStartEquity = GlobalVariableGet(kEq);
      PrintFormat("--- resumed UTC day (%d), start equity %.2f restored ---",
                  today, g_dayStartEquity);
   }
   else
   {
      g_dayStartEquity = AccountInfoDouble(ACCOUNT_EQUITY);
      GlobalVariableSet(kDay, (double)today);
      GlobalVariableSet(kEq, g_dayStartEquity);
      if(InpVerboseLog)
         PrintFormat("--- new UTC day (%d), start equity %.2f ---", today, g_dayStartEquity);
   }

   for(int i = 0; i < g_count; i++)
   {
      g_win[i].armedDay   = -1;
      g_win[i].cutForNews = false;
      g_win[i].detached   = false;
   }
}

void EnforceDailyLossHalt()
{
   if(InpMaxDailyLossPct <= 0.0 || g_dayStartEquity <= 0.0 || g_haltedToday)
      return;
   const double loss = (g_dayStartEquity - AccountInfoDouble(ACCOUNT_EQUITY))
                       / g_dayStartEquity * 100.0;
   if(loss >= InpMaxDailyLossPct)
   {
      g_haltedToday = true;
      PrintFormat("Daily loss %.2f%% hit the %.2f%% limit - halting for today.",
                  loss, InpMaxDailyLossPct);
      CloseEverything("daily loss halt");
   }
}

//--- true from the daily flatten time until the UTC day rolls over.
//---
//--- The flatten is anchored to the daily halt, not to a fixed UTC clock. A
//--- fixed 21:50 UTC lands INSIDE the summer halt (20:58-22:01) on ~66% of
//--- trading days. OnTick is tick-driven, so nothing fires during the halt
//--- and CloseEverything() slips to the first tick after the reopen - past
//--- the swap charge. Anchoring holds swap at exactly 0.
//---
//--- Scanned as an offset before the halt over 2024-2026: net falls
//--- monotonically the earlier you flatten (ORIGINAL $11,897 at 0m ->
//--- $10,230 at 180m), because the cost is swap, NOT the reopen spread -
//--- holding to the reopen actually ADDS gross. So flatten at the last
//--- moment before swap is charged. 0m is the literal optimum; 5m costs
//--- ~$45 over 31 months and buys headroom, since ticks stop at HH:57:58
//--- and an order aimed at the halt itself risks arriving after the close.
//--- Worth +$192 (ORIGINAL) / +$385 (TUNED) against the fixed-UTC rule.
bool PastDailyFlatten(const datetime utc)
{
   MqlDateTime dt;
   TimeToStruct(utc, dt);
   const int sec = dt.hour * 3600 + dt.min * 60 + dt.sec;
   return (sec >= DailyFlatSecUTC(utc));
}

int MinutesToUTCMidnight(const datetime utc)
{
   MqlDateTime dt;
   TimeToStruct(utc, dt);
   return (23 - dt.hour) * 60 + (60 - dt.min);
}

//+------------------------------------------------------------------+
void ProcessWindow(const int i, const datetime utc, const int today)
{
   ResolveOCO(i);
   if(g_win[i].armedDay == today && utc >= g_win[i].expiryUTC)
   {
      CancelWindowPending(i, g_win[i].cutForNews ? "expired at the blackout edge"
                                                 : "expired unfilled");
      //--- A bracket cut short by news never had its full life. Reopen it once
      //--- the window passes: the range is a property of the session hours, so
      //--- re-arming reuses the same high/low rather than re-measuring. The
      //--- inherited "price already outside" check below then skips the day if
      //--- the release itself broke the range - which is the correct outcome,
      //--- since that break is exactly the trade we are not allowed to take.
      if(g_win[i].cutForNews && InpRearmAfterNews
         && FindPosition(g_win[i].magic) == 0 && !InFlattenLead(utc))
      {
         g_win[i].armedDay   = -1;
         g_win[i].doneDay    = -1;
         g_win[i].cutForNews = false;
         if(InpVerboseLog)
            PrintFormat("%s: blackout passed - re-arming", g_win[i].name);
      }
   }

   if(g_win[i].doneDay == today || g_win[i].armedDay == today)
      return;

   const datetime rangeStart = UTCTodayAtHour(g_win[i].hour);
   const datetime rangeEnd   = rangeStart + g_win[i].rangeMin * 60;
   if(utc < rangeEnd)
      return;

   if(utc >= rangeEnd + (datetime)(InpOrderExpiryHours * 3600))
   {
      g_win[i].armedDay = today;          // too late to arm; stop rechecking
      return;
   }
   if(InpSkipSunday && IsUTCSunday(utc))
   {
      g_win[i].armedDay = today;
      return;
   }
   //--- Return WITHOUT marking armedDay: the window is deferred, not spent,
   //--- and ProcessWindow retries it after the blackout. The "too late to
   //--- arm" bound above is what stops this retrying forever.
   if(InpNewsFilter && InFlattenLead(utc))
      return;

   double hi = 0.0, lo = 0.0;
   if(!BuildRange(rangeStart, rangeEnd, hi, lo))
   {
      if(InpVerboseLog)
         PrintFormat("%s: no M1 data for the bracket - skipping today", g_win[i].name);
      g_win[i].armedDay = today;
      return;
   }

   const double width    = hi - lo;
   const double refPx    = (hi + lo) * 0.5;
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
         PrintFormat("%s: spread %.3f over the %.3f limit - skipping",
                     g_win[i].name, g_sym.Ask() - g_sym.Bid(), InpMaxSpreadUSD);
      g_win[i].armedDay = today;
      return;
   }
   //--- on a restart price may already have broken out; arming only the far
   //--- side would take the wrong direction, so sit the day out
   if(g_sym.Ask() >= hi || g_sym.Bid() <= lo)
   {
      if(InpVerboseLog)
         PrintFormat("%s: price already outside %.2f-%.2f - missed the break, skipping",
                     g_win[i].name, lo, hi);
      g_win[i].armedDay = today;
      return;
   }
   if(CountOurPositions() >= InpMaxOpenPositions)
   {
      g_win[i].armedDay = today;
      return;
   }

   //--- Layer 1: the order must not outlive the start of the next blackout.
   //--- A bracket that would only get a few minutes in the market before
   //--- being pulled is not worth arming - it pays the spread for a sliver
   //--- of the distribution, and some brokers reject a near-term expiry
   //--- outright. Wait for the blackout to pass and re-arm instead.
   datetime expiry = rangeEnd + (datetime)(InpOrderExpiryHours * 3600);
   bool     cut    = false;
   if(InpNewsFilter)
   {
      const datetime nextBO = NextBlackoutStart(utc);
      if(nextBO > 0 && nextBO - (datetime)InpPreNewsFlatSec < expiry)
      {
         expiry = nextBO - (datetime)InpPreNewsFlatSec;
         cut    = true;
         if(expiry - utc < (datetime)(InpMinOrderLifeMin * 60))
         {
            if(InpVerboseLog)
               PrintFormat("%s: only %d min before the next blackout - deferring",
                           g_win[i].name, (int)((expiry - utc) / 60));
            return;                       // deferred, not spent
         }
      }
   }
   PlaceBracket(i, hi, lo, width, expiry, today, cut);
}

//+------------------------------------------------------------------+
//| Bracket = high/low of M1 bars over [start, end) in UTC.          |
//| MT5 bars are bid-priced while the study measured mid, so both     |
//| extremes shift up by half the spread. Width is unaffected.        |
//+------------------------------------------------------------------+
bool BuildRange(const datetime rangeStartUTC, const datetime rangeEndUTC,
                double &hi, double &lo)
{
   MqlRates rates[];
   ArraySetAsSeries(rates, false);
   const datetime from = ServerFromUTC(rangeStartUTC);
   const datetime to   = ServerFromUTC(rangeEndUTC) - 60;

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
//| A Buy Stop triggers on ask and a Sell Stop on bid, which is       |
//| exactly how the backtest detected breaks - no adjustment needed.  |
//+------------------------------------------------------------------+
void PlaceBracket(const int i, const double hi, const double lo, const double width,
                  const datetime expiryUTC, const int today, const bool cutForNews)
{
   const double lots = ResolveLots();
   if(lots <= 0.0)
   {
      g_win[i].armedDay = today;
      return;
   }

   const double stopsLevel = (double)SymbolInfoInteger(_Symbol, SYMBOL_TRADE_STOPS_LEVEL)
                             * g_sym.Point();
   const bool buyOK  = (hi - g_sym.Ask() >= stopsLevel);
   const bool sellOK = (g_sym.Bid() - lo >= stopsLevel);
   if(!buyOK && !sellOK)
   {
      if(InpVerboseLog)
         PrintFormat("%s: both sides inside the %.2f stops level - skipping",
                     g_win[i].name, stopsLevel);
      g_win[i].armedDay = today;
      return;
   }

   const double tpBuy  = g_sym.NormalizePrice(hi + g_win[i].targetMult * width);
   const double tpSell = g_sym.NormalizePrice(lo - g_win[i].targetMult * width);

   g_trade.SetExpertMagicNumber(g_win[i].magic);
   const string tag = g_win[i].name;
   bool placed = false;

   //--- Server-side expiry where the broker allows it, so the order dies at
   //--- the blackout edge whether or not a tick reaches us.
   const ENUM_ORDER_TYPE_TIME tt = g_expirySupported ? ORDER_TIME_SPECIFIED : ORDER_TIME_GTC;
   const datetime expSrv = g_expirySupported ? ServerFromUTC(expiryUTC) : 0;

   if(buyOK)
   {
      if(g_trade.BuyStop(lots, hi, _Symbol, lo, tpBuy, tt, expSrv, tag))
         placed = true;
      else
         PrintFormat("%s: BuyStop failed (%d) %s", tag,
                     g_trade.ResultRetcode(), g_trade.ResultRetcodeDescription());
   }
   if(sellOK)
   {
      if(g_trade.SellStop(lots, lo, _Symbol, hi, tpSell, tt, expSrv, tag))
         placed = true;
      else
         PrintFormat("%s: SellStop failed (%d) %s", tag,
                     g_trade.ResultRetcode(), g_trade.ResultRetcodeDescription());
   }

   g_win[i].hi         = hi;
   g_win[i].lo         = lo;
   g_win[i].expiryUTC  = expiryUTC;
   g_win[i].armedDay   = today;
   g_win[i].cutForNews = cutForNews;

   if(placed && InpVerboseLog)
      PrintFormat("%s armed: %.2f-%.2f (width %.2f, %.3f%%), %.2f lots, "
                  "targets %.2f / %.2f, expires %s UTC%s",
                  tag, lo, hi, width, width / ((hi + lo) * 0.5) * 100.0, lots, tpBuy, tpSell,
                  TimeToString(expiryUTC, TIME_MINUTES),
                  cutForNews ? " (cut short by a blackout)" : "");
}

//+------------------------------------------------------------------+
//| Manual OCO: first fill retires the other side, then the take      |
//| profit is re-anchored to the price actually obtained.             |
//+------------------------------------------------------------------+
void ResolveOCO(const int i)
{
   const ulong pos = FindPosition(g_win[i].magic);
   if(pos == 0)
      return;

   for(int k = OrdersTotal() - 1; k >= 0; k--)
   {
      const ulong t = OrderGetTicket(k);
      if(t == 0) continue;
      if((ulong)OrderGetInteger(ORDER_MAGIC) != g_win[i].magic) continue;
      g_trade.OrderDelete(t);
   }
   g_win[i].doneDay    = UTCDayKey();
   g_win[i].cutForNews = false;      // filled: the day is spent, never re-arm
   SyncTakeProfit(i, pos);
}

void SyncTakeProfit(const int i, const ulong ticket)
{
   if(g_win[i].detached)      // stops are lifted for a blackout; leave them alone
      return;
   if(!PositionSelectByTicket(ticket))
      return;
   const double width = g_win[i].hi - g_win[i].lo;
   if(width <= 0.0)
      return;

   const double entry = PositionGetDouble(POSITION_PRICE_OPEN);
   const double sl    = PositionGetDouble(POSITION_SL);
   const double tpNow = PositionGetDouble(POSITION_TP);
   const long   type  = PositionGetInteger(POSITION_TYPE);

   const double tpWant = (type == POSITION_TYPE_BUY)
                         ? g_sym.NormalizePrice(entry + g_win[i].targetMult * width)
                         : g_sym.NormalizePrice(entry - g_win[i].targetMult * width);

   if(MathAbs(tpWant - tpNow) < g_sym.Point())
      return;

   g_trade.SetExpertMagicNumber(g_win[i].magic);
   if(g_trade.PositionModify(ticket, sl, tpWant) && InpVerboseLog)
      PrintFormat("%s: filled at %.2f, take profit re-anchored to %.2f",
                  g_win[i].name, entry, tpWant);
}

//+------------------------------------------------------------------+
//| Sizing: lots = base * clip(targetVol / trailingVol)               |
//|                                                                   |
//| NOTE the backtested medians for this preset were measured at a     |
//| FIXED 0.02 lots. With volatility targeting on and the reference    |
//| left at 0.816%, 2026-level volatility scales size to roughly half, |
//| so live P&L scales down with it. That is deliberate risk control,  |
//| not a different edge - raising InpTargetVolPct raises risk         |
//| proportionally.                                                    |
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
         g_volScale = (tv > 0.0)
                      ? MathMax(1.0 / InpMaxVolScale, MathMin(InpMaxVolScale, InpTargetVolPct / tv))
                      : 1.0;
         g_volScaleDay = today;
         if(InpVerboseLog)
            PrintFormat("Volatility scale today: %.3f (trailing %.3f%% vs target %.3f%%)",
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
      const double v = DailyRealizedVol(UTCTodayAtHour(0) - (datetime)(back * 86400));
      if(v > 0.0) { sum += v; used++; }
   }
   return (used >= 5) ? sum / used : 0.0;
}

double DailyRealizedVol(const datetime dayStartUTC)
{
   MqlRates rates[];
   ArraySetAsSeries(rates, false);
   const int n = CopyRates(_Symbol, PERIOD_M5, ServerFromUTC(dayStartUTC),
                           ServerFromUTC(dayStartUTC + 86400) - 300, rates);
   if(n < 30)
      return 0.0;

   double r[];
   ArrayResize(r, n - 1);
   double mean = 0.0;
   int    m    = 0;
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
   return MathSqrt(ss / (m - 1)) * MathSqrt((double)m) * 100.0;
}

double NormalizeLots(double lots)
{
   const double step = g_sym.LotsStep();
   if(step > 0.0)
      lots = MathFloor(lots / step + 0.5) * step;
   lots = MathMax(g_sym.LotsMin(), MathMin(g_sym.LotsMax(), lots));
   return NormalizeDouble(lots, 2);
}

//+------------------------------------------------------------------+
//| Housekeeping                                                     |
//+------------------------------------------------------------------+
bool IsOurMagic(const ulong m) { return (m >= InpMagicBase && m < InpMagicBase + MAX_WINDOWS); }

bool SpreadAcceptable()
{
   if(InpMaxSpreadUSD <= 0.0)
      return true;
   return ((g_sym.Ask() - g_sym.Bid()) <= InpMaxSpreadUSD);
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

//+------------------------------------------------------------------+
//| Rebuild per-window state from what is already in the market.      |
//|                                                                   |
//| MT5 destroys and recreates the EA on a timeframe change, a symbol |
//| change, a recompile, an edit to the inputs, and every terminal    |
//| restart. Globals go back to their initialisers - the orders and   |
//| positions do not, because they live on the server. Without this   |
//| pass the EA sees an unarmed window, re-arms it, and stacks a      |
//| second bracket at the SAME two prices. The stacked pairs then     |
//| fill together on the break: N re-inits, N times the intended      |
//| size, against a fixed daily loss limit. That is an account        |
//| killer, and it fires on restarts nobody chose.                    |
//|                                                                   |
//| Nothing has to be persisted to undo it. Everything lost is        |
//| recoverable from the market itself:                               |
//|   hi / lo    ARE the pending prices - a BuyStop sits at hi        |
//|   expiryUTC  recomputes from the window's own hour and length     |
//|   armedDay   a live pending means this window armed today         |
//|   doneDay    an open position means it has already traded         |
//|                                                                   |
//| Where the bracket cannot be reconstructed exactly - only one side |
//| survived, or the window already filled - hi/lo are left at zero.  |
//| SyncTakeProfit's width guard then leaves the take profit exactly  |
//| as placed, which beats re-anchoring it to a width we guessed.     |
//+------------------------------------------------------------------+
void AdoptExistingState()
{
   const int today = UTCDayKey();
   int adopted = 0, stale = 0, dupes = 0;

   for(int i = 0; i < g_count; i++)
   {
      double hi = 0.0, lo = 0.0;
      ulong  buyTicket = 0, sellTicket = 0;
      int    pend = 0;

      for(int k = OrdersTotal() - 1; k >= 0; k--)
      {
         const ulong t = OrderGetTicket(k);
         if(t == 0) continue;
         if(OrderGetString(ORDER_SYMBOL) != _Symbol) continue;
         if((ulong)OrderGetInteger(ORDER_MAGIC) != g_win[i].magic) continue;

         //--- an order that outlived a terminal outage: the daily flatten
         //--- never got a tick to cancel it
         const datetime setUTC = (datetime)OrderGetInteger(ORDER_TIME_SETUP)
                                 - (datetime)(InpGMTOffsetHours * 3600);
         if(DayKeyOf(setUTC) != today)
         {
            if(g_trade.OrderDelete(t))
               stale++;
            continue;
         }

         const long ty = OrderGetInteger(ORDER_TYPE);
         if(ty != ORDER_TYPE_BUY_STOP && ty != ORDER_TYPE_SELL_STOP)
            continue;

         //--- Duplicates can only exist because an earlier build re-armed over
         //--- a live bracket. Keep one of each side and delete the rest: this
         //--- is the repair pass for damage already done.
         const bool isBuy = (ty == ORDER_TYPE_BUY_STOP);
         if((isBuy && buyTicket != 0) || (!isBuy && sellTicket != 0))
         {
            if(g_trade.OrderDelete(t))
               dupes++;
            continue;
         }
         if(isBuy)
         {
            buyTicket = t;
            hi = OrderGetDouble(ORDER_PRICE_OPEN);
         }
         else
         {
            sellTicket = t;
            lo = OrderGetDouble(ORDER_PRICE_OPEN);
         }
         pend++;
      }

      const ulong pos = FindPosition(g_win[i].magic);
      if(pend == 0 && pos == 0)
         continue;

      g_win[i].armedDay  = today;
      g_win[i].expiryUTC = UTCTodayAtHour(g_win[i].hour)
                           + (datetime)(g_win[i].rangeMin * 60)
                           + (datetime)(InpOrderExpiryHours * 3600);
      if(pos != 0)
         g_win[i].doneDay = today;
      if(hi > 0.0 && lo > 0.0 && hi > lo)
      {
         g_win[i].hi = hi;
         g_win[i].lo = lo;
      }
      adopted++;

      PrintFormat("%s: adopted %d pending%s%s", g_win[i].name, pend,
                  (pos != 0) ? " and an open position" : "",
                  (g_win[i].hi > 0.0)
                  ? StringFormat(", bracket %.2f-%.2f restored", g_win[i].lo, g_win[i].hi)
                  : ", bracket not reconstructible - take profit left as placed");
   }

   if(adopted > 0 || stale > 0 || dupes > 0)
      PrintFormat("Re-init: %d window(s) adopted, %d stale order(s) and %d duplicate(s) "
                  "cancelled. Without this pass a second bracket would have been "
                  "stacked on each adopted window.", adopted, stale, dupes);
}

//--- Close anything opened on an earlier UTC day. This is the backstop that
//--- makes the daily-flat rule hold across weekends and holidays, when no
//--- tick arrives before midnight.
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
   g_win[i].doneDay = UTCDayKey();
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
