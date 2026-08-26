//+------------------------------------------------------------------+
//| NOG Small Account EA V6                                         |
//| H1 market regime + M15 pullback/cross entry                      |
//+------------------------------------------------------------------+
#property strict
#property version   "6.00"
#property description "Multi-timeframe EMA/ADX regime + M15 pullback EA with low-risk controls"

#include <Trade/Trade.mqh>
CTrade trade;

input group "Timeframes"
input ENUM_TIMEFRAMES InpEntryTimeframe = PERIOD_M15;
input ENUM_TIMEFRAMES InpTrendTimeframe = PERIOD_H1;

input group "Entry Strategy"
input int    InpFastEMA = 20;
input int    InpSlowEMA = 50;
input int    InpEntryADXPeriod = 14;
input double InpMinEntryADX = 12.0;
input int    InpATRPeriod = 14;
input double InpATRStopMult = 1.50;
input double InpRiskReward = 1.70;
input bool   InpUseCrossEntry = true;
input bool   InpUsePullbackEntry = true;
input double InpPullbackATRDistance = 0.30;
input double InpMaxDistanceFromFastATR = 0.70;
input bool   InpRequireCandleConfirm = true;

input group "H1 Regime Filter"
input int    InpHTFFastEMA = 50;
input int    InpHTFSlowEMA = 200;
input int    InpHTFADXPeriod = 14;
input double InpMinHTFADX = 18.0;
input double InpMinHTFDIDifference = 2.0;
input bool   InpUseHTFSlowSlope = true;

input group "Risk Management"
input double InpRiskPercent = 0.25;
input double InpMaxDailyLoss = 1.50;
input int    InpMaxTradesDay = 2;
input int    InpMaxConsecLosses = 2;
input int    InpLossCooldownHours = 6;
input double InpMaxSpreadPts = 50.0;
input bool   InpEmergencyStop = false;

input group "Trading Session (server time)"
input bool InpUseSession = true;
input int  InpSessionStartHour = 6;
input int  InpSessionEndHour = 21;

input group "Position Management"
input bool   InpUseBreakEven = true;
input double InpBreakEvenAtR = 1.00;
input double InpBreakEvenLockR = 0.10;
input bool   InpUseTrailing = true;
input double InpTrailStartR = 1.30;
input double InpTrailATRMult = 1.00;

input group "Execution"
input ulong InpMagicNumber = 26082606;
input int   InpSlippagePoints = 20;

int entry_fast_handle = INVALID_HANDLE;
int entry_slow_handle = INVALID_HANDLE;
int entry_atr_handle  = INVALID_HANDLE;
int entry_adx_handle  = INVALID_HANDLE;
int htf_fast_handle   = INVALID_HANDLE;
int htf_slow_handle   = INVALID_HANDLE;
int htf_adx_handle    = INVALID_HANDLE;

datetime last_bar_time = 0;
int current_day_key = 0;
int trades_today = 0;
int consecutive_losses = 0;
datetime loss_cooldown_until = 0;
double day_start_balance = 0.0;

int DayKey()
{
   MqlDateTime d;
   TimeToStruct(TimeCurrent(), d);
   return d.year * 10000 + d.mon * 100 + d.day;
}

void RefreshDailyState()
{
   int key = DayKey();
   if(key != current_day_key)
   {
      current_day_key = key;
      day_start_balance = AccountInfoDouble(ACCOUNT_BALANCE);
      trades_today = 0;
   }
}

double DailyLossPercent()
{
   RefreshDailyState();
   if(day_start_balance <= 0.0)
      return 0.0;

   double equity = AccountInfoDouble(ACCOUNT_EQUITY);
   if(equity >= day_start_balance)
      return 0.0;

   return (day_start_balance - equity) / day_start_balance * 100.0;
}

double SpreadPoints()
{
   MqlTick tick;
   if(!SymbolInfoTick(_Symbol, tick))
      return DBL_MAX;

   double point = SymbolInfoDouble(_Symbol, SYMBOL_POINT);
   if(point <= 0.0)
      return DBL_MAX;

   return (tick.ask - tick.bid) / point;
}

bool InSession()
{
   if(!InpUseSession)
      return true;

   MqlDateTime d;
   TimeToStruct(TimeCurrent(), d);

   if(InpSessionStartHour == InpSessionEndHour)
      return true;

   if(InpSessionStartHour < InpSessionEndHour)
      return d.hour >= InpSessionStartHour && d.hour < InpSessionEndHour;

   return d.hour >= InpSessionStartHour || d.hour < InpSessionEndHour;
}

bool HasOpenPosition()
{
   for(int i = PositionsTotal() - 1; i >= 0; --i)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0)
         continue;

      if(PositionGetString(POSITION_SYMBOL) == _Symbol &&
         (ulong)PositionGetInteger(POSITION_MAGIC) == InpMagicNumber)
         return true;
   }
   return false;
}

int VolumeDigits(double step)
{
   int digits = 0;
   while(digits < 8 && MathAbs(step - NormalizeDouble(step, digits)) > 1e-12)
      digits++;
   return digits;
}

double NormalizeVolume(double volume)
{
   double vmin = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
   double vmax = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MAX);
   double step = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);

   if(step <= 0.0)
      return 0.0;

   volume = MathFloor(volume / step) * step;

   // Never force broker minimum if it exceeds the calculated risk budget.
   if(volume < vmin)
      return 0.0;

   volume = MathMin(volume, vmax);
   return NormalizeDouble(volume, VolumeDigits(step));
}

double RiskVolume(double entry, double stop)
{
   double risk_money = AccountInfoDouble(ACCOUNT_BALANCE) * InpRiskPercent / 100.0;
   double distance = MathAbs(entry - stop);
   double tick_size = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE);
   double tick_value = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_VALUE_LOSS);
   if(tick_value <= 0.0)
      tick_value = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_VALUE);

   if(risk_money <= 0.0 || distance <= 0.0 || tick_size <= 0.0 || tick_value <= 0.0)
      return 0.0;

   double loss_per_lot = (distance / tick_size) * tick_value;
   if(loss_per_lot <= 0.0)
      return 0.0;

   return NormalizeVolume(risk_money / loss_per_lot);
}

bool NewEntryBar()
{
   datetime t[1];
   if(CopyTime(_Symbol, InpEntryTimeframe, 0, 1, t) != 1)
      return false;

   if(t[0] == last_bar_time)
      return false;

   last_bar_time = t[0];
   return true;
}

bool CopyOne(int handle, int buffer_num, int shift, double &value)
{
   double a[1];
   if(CopyBuffer(handle, buffer_num, shift, 1, a) != 1)
      return false;
   value = a[0];
   return true;
}

bool ReadEntryData(double &fast1, double &fast2,
                   double &slow1, double &slow2,
                   double &atr1, double &adx1,
                   double &open1, double &close1,
                   double &high1, double &low1)
{
   if(!CopyOne(entry_fast_handle, 0, 1, fast1)) return false;
   if(!CopyOne(entry_fast_handle, 0, 2, fast2)) return false;
   if(!CopyOne(entry_slow_handle, 0, 1, slow1)) return false;
   if(!CopyOne(entry_slow_handle, 0, 2, slow2)) return false;
   if(!CopyOne(entry_atr_handle, 0, 1, atr1)) return false;
   if(!CopyOne(entry_adx_handle, 0, 1, adx1)) return false;

   double o[1], c[1], h[1], l[1];
   if(CopyOpen(_Symbol, InpEntryTimeframe, 1, 1, o) != 1) return false;
   if(CopyClose(_Symbol, InpEntryTimeframe, 1, 1, c) != 1) return false;
   if(CopyHigh(_Symbol, InpEntryTimeframe, 1, 1, h) != 1) return false;
   if(CopyLow(_Symbol, InpEntryTimeframe, 1, 1, l) != 1) return false;

   open1 = o[0];
   close1 = c[0];
   high1 = h[0];
   low1 = l[0];
   return true;
}

bool ReadHTFData(double &fast1, double &fast2,
                 double &slow1, double &slow2,
                 double &adx1, double &plusDI1, double &minusDI1,
                 double &close1)
{
   if(!CopyOne(htf_fast_handle, 0, 1, fast1)) return false;
   if(!CopyOne(htf_fast_handle, 0, 2, fast2)) return false;
   if(!CopyOne(htf_slow_handle, 0, 1, slow1)) return false;
   if(!CopyOne(htf_slow_handle, 0, 2, slow2)) return false;
   if(!CopyOne(htf_adx_handle, 0, 1, adx1)) return false;
   if(!CopyOne(htf_adx_handle, 1, 1, plusDI1)) return false;
   if(!CopyOne(htf_adx_handle, 2, 1, minusDI1)) return false;

   double c[1];
   if(CopyClose(_Symbol, InpTrendTimeframe, 1, 1, c) != 1)
      return false;
   close1 = c[0];
   return true;
}

int MarketRegime()
{
   double f1, f2, s1, s2, adx, plusDI, minusDI, close1;
   if(!ReadHTFData(f1, f2, s1, s2, adx, plusDI, minusDI, close1))
      return 0;

   if(InpMinHTFADX > 0.0 && adx < InpMinHTFADX)
      return 0;

   bool slope_up = !InpUseHTFSlowSlope || s1 > s2;
   bool slope_down = !InpUseHTFSlowSlope || s1 < s2;
   bool di_bull = plusDI >= minusDI + InpMinHTFDIDifference;
   bool di_bear = minusDI >= plusDI + InpMinHTFDIDifference;

   bool bull = f1 > s1 && close1 > s1 && slope_up && di_bull;
   bool bear = f1 < s1 && close1 < s1 && slope_down && di_bear;

   if(bull) return 1;
   if(bear) return -1;
   return 0;
}

bool TradingAllowed()
{
   RefreshDailyState();

   if(InpEmergencyStop) return false;
   if(!TerminalInfoInteger(TERMINAL_CONNECTED)) return false;
   if(!TerminalInfoInteger(TERMINAL_TRADE_ALLOWED)) return false;
   if(!MQLInfoInteger(MQL_TRADE_ALLOWED)) return false;
   if(!InSession()) return false;
   if(loss_cooldown_until > TimeCurrent()) return false;
   if(InpMaxDailyLoss > 0.0 && DailyLossPercent() >= InpMaxDailyLoss) return false;
   if(InpMaxTradesDay > 0 && trades_today >= InpMaxTradesDay) return false;
   if(InpMaxSpreadPts > 0.0 && SpreadPoints() > InpMaxSpreadPts) return false;
   if(HasOpenPosition()) return false;

   return true;
}

double MinimumStopDistance()
{
   double point = SymbolInfoDouble(_Symbol, SYMBOL_POINT);
   long stops_level = SymbolInfoInteger(_Symbol, SYMBOL_TRADE_STOPS_LEVEL);
   if(point <= 0.0 || stops_level <= 0)
      return 0.0;
   return (double)stops_level * point;
}

bool OpenTrade(bool buy, double atr)
{
   MqlTick tick;
   if(!SymbolInfoTick(_Symbol, tick))
      return false;

   int digits = (int)SymbolInfoInteger(_Symbol, SYMBOL_DIGITS);
   double point = SymbolInfoDouble(_Symbol, SYMBOL_POINT);
   double dist = atr * InpATRStopMult;
   double min_stop = MinimumStopDistance();
   if(point > 0.0 && min_stop > 0.0)
      dist = MathMax(dist, min_stop + 2.0 * point);

   double entry = buy ? tick.ask : tick.bid;
   double sl = NormalizeDouble(buy ? entry - dist : entry + dist, digits);
   double tp = NormalizeDouble(buy ? entry + dist * InpRiskReward :
                                   entry - dist * InpRiskReward, digits);
   double volume = RiskVolume(entry, sl);

   if(volume <= 0.0)
   {
      Print("V6 skipped: broker minimum lot exceeds risk target");
      return false;
   }

   bool ok = buy ? trade.Buy(volume, _Symbol, 0.0, sl, tp, "NOG V6 BUY") :
                   trade.Sell(volume, _Symbol, 0.0, sl, tp, "NOG V6 SELL");

   if(ok)
   {
      trades_today++;
      return true;
   }

   Print("V6 order failed: ", trade.ResultRetcode(), " ", trade.ResultRetcodeDescription());
   return false;
}

void ManagePositions()
{
   if(!InpUseBreakEven && !InpUseTrailing)
      return;

   double atr_now;
   if(!CopyOne(entry_atr_handle, 0, 0, atr_now) || atr_now <= 0.0)
      return;

   MqlTick tick;
   if(!SymbolInfoTick(_Symbol, tick))
      return;

   int digits = (int)SymbolInfoInteger(_Symbol, SYMBOL_DIGITS);
   double point = SymbolInfoDouble(_Symbol, SYMBOL_POINT);

   for(int i = PositionsTotal() - 1; i >= 0; --i)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0)
         continue;

      if(PositionGetString(POSITION_SYMBOL) != _Symbol ||
         (ulong)PositionGetInteger(POSITION_MAGIC) != InpMagicNumber)
         continue;

      ENUM_POSITION_TYPE type = (ENUM_POSITION_TYPE)PositionGetInteger(POSITION_TYPE);
      double open = PositionGetDouble(POSITION_PRICE_OPEN);
      double sl = PositionGetDouble(POSITION_SL);
      double tp = PositionGetDouble(POSITION_TP);
      double initial_risk = (tp > 0.0 && InpRiskReward > 0.0) ?
                            MathAbs(tp - open) / InpRiskReward :
                            atr_now * InpATRStopMult;
      if(initial_risk <= 0.0)
         continue;

      double price = (type == POSITION_TYPE_BUY) ? tick.bid : tick.ask;
      double profit_dist = (type == POSITION_TYPE_BUY) ? price - open : open - price;
      double new_sl = sl;

      if(InpUseBreakEven && profit_dist >= initial_risk * InpBreakEvenAtR)
      {
         double be = (type == POSITION_TYPE_BUY) ?
                     open + initial_risk * InpBreakEvenLockR :
                     open - initial_risk * InpBreakEvenLockR;

         if(type == POSITION_TYPE_BUY && (new_sl == 0.0 || be > new_sl)) new_sl = be;
         if(type == POSITION_TYPE_SELL && (new_sl == 0.0 || be < new_sl)) new_sl = be;
      }

      if(InpUseTrailing && profit_dist >= initial_risk * InpTrailStartR)
      {
         double trail = (type == POSITION_TYPE_BUY) ?
                        price - atr_now * InpTrailATRMult :
                        price + atr_now * InpTrailATRMult;

         if(type == POSITION_TYPE_BUY && (new_sl == 0.0 || trail > new_sl)) new_sl = trail;
         if(type == POSITION_TYPE_SELL && (new_sl == 0.0 || trail < new_sl)) new_sl = trail;
      }

      new_sl = NormalizeDouble(new_sl, digits);
      if(new_sl > 0.0 && point > 0.0 && MathAbs(new_sl - sl) >= point)
         trade.PositionModify(ticket, new_sl, tp);
   }
}

int OnInit()
{
   if(InpFastEMA <= 0 || InpSlowEMA <= 0 || InpHTFFastEMA <= 0 || InpHTFSlowEMA <= 0 ||
      InpATRPeriod <= 0 || InpEntryADXPeriod <= 0 || InpHTFADXPeriod <= 0 ||
      InpFastEMA >= InpSlowEMA || InpHTFFastEMA >= InpHTFSlowEMA)
      return INIT_PARAMETERS_INCORRECT;

   entry_fast_handle = iMA(_Symbol, InpEntryTimeframe, InpFastEMA, 0, MODE_EMA, PRICE_CLOSE);
   entry_slow_handle = iMA(_Symbol, InpEntryTimeframe, InpSlowEMA, 0, MODE_EMA, PRICE_CLOSE);
   entry_atr_handle  = iATR(_Symbol, InpEntryTimeframe, InpATRPeriod);
   entry_adx_handle  = iADX(_Symbol, InpEntryTimeframe, InpEntryADXPeriod);

   htf_fast_handle = iMA(_Symbol, InpTrendTimeframe, InpHTFFastEMA, 0, MODE_EMA, PRICE_CLOSE);
   htf_slow_handle = iMA(_Symbol, InpTrendTimeframe, InpHTFSlowEMA, 0, MODE_EMA, PRICE_CLOSE);
   htf_adx_handle  = iADX(_Symbol, InpTrendTimeframe, InpHTFADXPeriod);

   if(entry_fast_handle == INVALID_HANDLE || entry_slow_handle == INVALID_HANDLE ||
      entry_atr_handle == INVALID_HANDLE || entry_adx_handle == INVALID_HANDLE ||
      htf_fast_handle == INVALID_HANDLE || htf_slow_handle == INVALID_HANDLE ||
      htf_adx_handle == INVALID_HANDLE)
      return INIT_FAILED;

   trade.SetExpertMagicNumber(InpMagicNumber);
   trade.SetDeviationInPoints(InpSlippagePoints);
   trade.SetTypeFillingBySymbol(_Symbol);
   RefreshDailyState();
   return INIT_SUCCEEDED;
}

void OnDeinit(const int reason)
{
   if(entry_fast_handle != INVALID_HANDLE) IndicatorRelease(entry_fast_handle);
   if(entry_slow_handle != INVALID_HANDLE) IndicatorRelease(entry_slow_handle);
   if(entry_atr_handle  != INVALID_HANDLE) IndicatorRelease(entry_atr_handle);
   if(entry_adx_handle  != INVALID_HANDLE) IndicatorRelease(entry_adx_handle);
   if(htf_fast_handle   != INVALID_HANDLE) IndicatorRelease(htf_fast_handle);
   if(htf_slow_handle   != INVALID_HANDLE) IndicatorRelease(htf_slow_handle);
   if(htf_adx_handle    != INVALID_HANDLE) IndicatorRelease(htf_adx_handle);
}

void OnTradeTransaction(const MqlTradeTransaction &trans,
                        const MqlTradeRequest &request,
                        const MqlTradeResult &result)
{
   if(trans.type != TRADE_TRANSACTION_DEAL_ADD || trans.deal == 0)
      return;

   if(!HistoryDealSelect(trans.deal))
      return;

   if(HistoryDealGetString(trans.deal, DEAL_SYMBOL) != _Symbol ||
      (ulong)HistoryDealGetInteger(trans.deal, DEAL_MAGIC) != InpMagicNumber)
      return;

   ENUM_DEAL_ENTRY entry_type = (ENUM_DEAL_ENTRY)HistoryDealGetInteger(trans.deal, DEAL_ENTRY);
   if(entry_type != DEAL_ENTRY_OUT && entry_type != DEAL_ENTRY_OUT_BY)
      return;

   double pnl = HistoryDealGetDouble(trans.deal, DEAL_PROFIT) +
                HistoryDealGetDouble(trans.deal, DEAL_SWAP) +
                HistoryDealGetDouble(trans.deal, DEAL_COMMISSION);

   if(pnl < 0.0)
   {
      consecutive_losses++;
      if(InpMaxConsecLosses > 0 && consecutive_losses >= InpMaxConsecLosses)
      {
         loss_cooldown_until = TimeCurrent() + InpLossCooldownHours * 3600;
         consecutive_losses = 0;
         Print("V6 loss cooldown until ", TimeToString(loss_cooldown_until, TIME_DATE | TIME_MINUTES));
      }
   }
   else if(pnl > 0.0)
   {
      consecutive_losses = 0;
      loss_cooldown_until = 0;
   }
}

void OnTick()
{
   ManagePositions();

   if(!NewEntryBar() || !TradingAllowed())
      return;

   int regime = MarketRegime();
   if(regime == 0)
      return;

   double f1, f2, s1, s2, atr, adx, o, c, h, l;
   if(!ReadEntryData(f1, f2, s1, s2, atr, adx, o, c, h, l))
      return;

   if(atr <= 0.0)
      return;

   if(InpMinEntryADX > 0.0 && adx < InpMinEntryADX)
      return;

   if(InpMaxDistanceFromFastATR > 0.0 && MathAbs(c - f1) > atr * InpMaxDistanceFromFastATR)
      return;

   bool bullish_candle = !InpRequireCandleConfirm || c > o;
   bool bearish_candle = !InpRequireCandleConfirm || c < o;

   bool bull_cross = InpUseCrossEntry &&
                     regime == 1 &&
                     f2 <= s2 && f1 > s1 &&
                     c > f1 && bullish_candle;

   bool bear_cross = InpUseCrossEntry &&
                     regime == -1 &&
                     f2 >= s2 && f1 < s1 &&
                     c < f1 && bearish_candle;

   bool bull_pullback = InpUsePullbackEntry &&
                        regime == 1 &&
                        f1 > s1 &&
                        l <= f1 + atr * InpPullbackATRDistance &&
                        c > f1 && bullish_candle;

   bool bear_pullback = InpUsePullbackEntry &&
                        regime == -1 &&
                        f1 < s1 &&
                        h >= f1 - atr * InpPullbackATRDistance &&
                        c < f1 && bearish_candle;

   if(bull_cross || bull_pullback)
      OpenTrade(true, atr);
   else if(bear_cross || bear_pullback)
      OpenTrade(false, atr);
}
