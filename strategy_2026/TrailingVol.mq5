//+------------------------------------------------------------------+
//|                                              TrailingVol.mq5     |
//|                                                                  |
//|  Prints the trailing realised volatility that the preset switch  |
//|  runs on, using exactly the same maths as the EA.                |
//|                                                                  |
//|  Drop it on any XAUUSD chart and read the Experts tab. It is a   |
//|  SCRIPT, not an EA - it runs once and exits, places no orders.   |
//|                                                                  |
//|    daily_vol_pct = stdev(M5 log returns) * sqrt(n) * 100         |
//|    trailing      = mean of the last N completed UTC sessions     |
//+------------------------------------------------------------------+
#property script_show_inputs
#property copyright "Session breakout portfolio"
#property version   "1.00"

input int    InpGMTOffsetHours = 0;    // Server time = GMT + this many hours
input int    InpLookbackDays   = 20;   // Sessions in the trailing average
input int    InpShowDays       = 15;   // How many recent sessions to list
input double InpSwitchDown     = 1.1;  // Below this: run ORIGINAL
input double InpSwitchUp       = 1.3;  // Above this: run TUNED_2026

datetime ServerFromUTC(const datetime u) { return u + (datetime)(InpGMTOffsetHours * 3600); }

//--- midnight UTC of the session `back` days ago, as a UTC timestamp
datetime UTCMidnightBack(const int back)
{
   MqlDateTime dt;
   TimeToStruct(TimeCurrent() - (datetime)(InpGMTOffsetHours * 3600), dt);
   dt.hour = 0; dt.min = 0; dt.sec = 0;
   return StructToTime(dt) - (datetime)(back * 86400);
}

//--- realised volatility of one UTC session, in percent
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

void OnStart()
{
   PrintFormat("=== trailing realised volatility on %s ===", _Symbol);
   PrintFormat("server %s | assumed GMT%+d | lookback %d sessions",
               TimeToString(TimeCurrent(), TIME_DATE | TIME_MINUTES),
               InpGMTOffsetHours, InpLookbackDays);

   double sum = 0.0;
   int    used = 0;
   int    listed = 0;

   for(int back = 1; back <= InpLookbackDays * 2 + 10 && used < InpLookbackDays; back++)
   {
      const datetime d = UTCMidnightBack(back);
      const double   v = DailyRealizedVol(d);
      if(v <= 0.0)
         continue;                                  // weekend or holiday

      sum += v;
      used++;
      if(listed < InpShowDays)
      {
         PrintFormat("  %s  daily %.3f", TimeToString(d, TIME_DATE), v);
         listed++;
      }
   }

   if(used < 5)
   {
      Print("not enough M5 history to compute it - load more history for ", _Symbol);
      return;
   }

   const double trailing = sum / used;
   string call;
   if(trailing < InpSwitchDown)      call = "run ORIGINAL";
   else if(trailing > InpSwitchUp)   call = "run TUNED_2026";
   else                              call = StringFormat("hold current (inside the %.1f-%.1f band)",
                                                         InpSwitchDown, InpSwitchUp);

   Print("--------------------------------------------------");
   PrintFormat("sessions used        %d", used);
   PrintFormat("TRAILING VOL         %.3f  (annualised %.1f%%)",
               trailing, trailing * MathSqrt(252.0));
   PrintFormat("switch rule       -> %s", call);
   Print("--------------------------------------------------");
   PrintFormat("  < %.1f      ORIGINAL", InpSwitchDown);
   PrintFormat("  %.1f - %.1f   hold whichever is already running", InpSwitchDown, InpSwitchUp);
   PrintFormat("  > %.1f      TUNED_2026", InpSwitchUp);
}
//+------------------------------------------------------------------+
