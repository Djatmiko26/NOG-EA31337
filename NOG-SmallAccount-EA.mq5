//+------------------------------------------------------------------+
//| NOG Small Account EA V7                                         |
//| Session breakout + ATR risk management baseline                  |
//+------------------------------------------------------------------+
#property strict
#property version   "7.00"
#property description "M15 breakout + ATR + session filter with low-risk controls"

#include <Trade/Trade.mqh>
CTrade trade;

input group "Breakout Strategy"
input ENUM_TIMEFRAMES InpTimeframe = PERIOD_M15;
input int    InpBreakoutLookback = 20;          // previous closed bars used as channel
input int    InpATRPeriod = 14;
input double InpBreakoutBufferATR = 0.05;       // close must exceed channel by this ATR fraction
input double InpMinBodyATR = 0.20;              // reject weak breakout candle
input double InpMaxBodyATR = 1.50;              // reject extreme spike candle
input double InpMinRangeATR = 1.00;             // minimum channel width in ATR
input double InpMaxRangeATR = 6.00;             // maximum channel width in ATR
input double InpATRStopMult = 1.50;
input double InpRiskReward = 1.50;
input bool   InpEnableBuy = true;
input bool   InpEnableSell = true;

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
input int  InpSessionStartHour = 7;
input int  InpSessionEndHour = 20;

input group "Position Management"
input bool   InpUseBreakEven = false;
input double InpBreakEvenAtR = 1.00;
input double InpBreakEvenLockR = 0.05;
input bool   InpUseTrailing = false;
input double InpTrailStartR = 1.30;
input double InpTrailATRMult = 1.00;

input group "Execution"
input ulong InpMagicNumber = 26082607;
input int   InpSlippagePoints = 20;

int atr_handle = INVALID_HANDLE;
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

   // Do not force broker minimum if it would exceed the risk budget.
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

bool NewBar()
{
   datetime t[1];
   if(CopyTime(_Symbol, InpTimeframe, 0, 1, t) != 1)
      return false;

   if(t[0] == last_bar_time)
      return false;

   last_bar_time = t[0];
   return true;
}

bool CopyATR(int shift, double &value)
{
   double a[1];
   if(CopyBuffer(atr_handle, 0, shift, 1, a) != 1)
      return false;
   value = a[0];
   return true;
}

bool ReadBreakoutData(double &range_high,
                      double &range_low,
                      double &atr1,
                      double &open1,
                      double &close1,
                      double &high1,
                      double &low1)
{
   if(InpBreakoutLookback < 2)
      return false;

   if(!CopyATR(1, atr1) || atr1 <= 0.0)
      return false;

   double o[1], c[1], h1[1], l1[1];
   if(CopyOpen(_Symbol, InpTimeframe, 1, 1, o) != 1) return false;
   if(CopyClose(_Symbol, InpTimeframe, 1, 1, c) != 1) return false;
   if(CopyHigh(_Symbol, InpTimeframe, 1, 1, h1) != 1) return false;
   if(CopyLow(_Symbol, InpTimeframe, 1, 1, l1) != 1) return false;

   double highs[];
   double lows[];
   ArrayResize(highs, InpBreakoutLookback);
   ArrayResize(lows, InpBreakoutLookback);

   // Start from shift 2 so the breakout candle itself is excluded from the channel.
   if(CopyHigh(_Symbol, InpTimeframe, 2, InpBreakoutLookback, highs) != InpBreakoutLookback)
      return false;
   if(CopyLow(_Symbol, InpTimeframe, 2, InpBreakoutLookback, lows) != InpBreakoutLookback)
      return false;

   range_high = -DBL_MAX;
   range_low = DBL_MAX;

   for(int i = 0; i < InpBreakoutLookback; ++i)
   {
      if(highs[i] > range_high) range_high = highs[i];
      if(lows[i] < range_low) range_low = lows[i];
   }

   open1 = o[0];
   close1 = c[0];
   high1 = h1[0];
   low1 = l1[0];
   return range_high > range_low;
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
      Print("V7 skipped: broker minimum lot exceeds risk target");
      return false;
   }

   bool ok = buy ? trade.Buy(volume, _Symbol, 0.0, sl, tp, "NOG V7 BREAKOUT BUY") :
                   trade.Sell(volume, _Symbol, 0.0, sl, tp, "NOG V7 BREAKOUT SELL");

   if(ok)
   {
      trades_today++;
      return true;
   }

   Print("V7 order failed: ", trade.ResultRetcode(), " ", trade.ResultRetcodeDescription());
   return false;
}

void ManagePositions()
{
   if(!InpUseBreakEven && !InpUseTrailing)
      return;

   double atr_now;
   if(!CopyATR(0, atr_now) || atr_now <= 0.0)
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
      if(new_sl > 0.0 && MathAbs(new_sl - sl) >= point)
         trade.PositionModify(ticket, new_sl, tp);
   }
}

int OnInit()
{
   if(InpBreakoutLookback < 2 || InpATRPeriod <= 0 || InpATRStopMult <= 0.0 ||
      InpRiskReward <= 0.0 || InpRiskPercent <= 0.0)
      return INIT_PARAMETERS_INCORRECT;

   atr_handle = iATR(_Symbol, InpTimeframe, InpATRPeriod);
   if(atr_handle == INVALID_HANDLE)
      return INIT_FAILED;

   trade.SetExpertMagicNumber(InpMagicNumber);
   trade.SetDeviationInPoints(InpSlippagePoints);
   trade.SetTypeFillingBySymbol(_Symbol);

   RefreshDailyState();
   return INIT_SUCCEEDED;
}

void OnDeinit(const int reason)
{
   if(atr_handle != INVALID_HANDLE)
      IndicatorRelease(atr_handle);
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

   ENUM_DEAL_ENTRY entry = (ENUM_DEAL_ENTRY)HistoryDealGetInteger(trans.deal, DEAL_ENTRY);
   if(entry != DEAL_ENTRY_OUT && entry != DEAL_ENTRY_OUT_BY)
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
         Print("V7 loss cooldown until ",
               TimeToString(loss_cooldown_until, TIME_DATE | TIME_MINUTES));
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

   if(!NewBar() || !TradingAllowed())
      return;

   double range_high, range_low, atr1, open1, close1, high1, low1;
   if(!ReadBreakoutData(range_high, range_low, atr1, open1, close1, high1, low1))
      return;

   if(atr1 <= 0.0)
      return;

   double range_width = range_high - range_low;
   double body = MathAbs(close1 - open1);

   if(InpMinRangeATR > 0.0 && range_width < atr1 * InpMinRangeATR)
      return;
   if(InpMaxRangeATR > 0.0 && range_width > atr1 * InpMaxRangeATR)
      return;
   if(InpMinBodyATR > 0.0 && body < atr1 * InpMinBodyATR)
      return;
   if(InpMaxBodyATR > 0.0 && body > atr1 * InpMaxBodyATR)
      return;

   double buffer = atr1 * InpBreakoutBufferATR;

   bool bullish_candle = close1 > open1;
   bool bearish_candle = close1 < open1;
   bool buy_signal = InpEnableBuy && bullish_candle && close1 > range_high + buffer;
   bool sell_signal = InpEnableSell && bearish_candle && close1 < range_low - buffer;

   if(buy_signal)
      OpenTrade(true, atr1);
   else if(sell_signal)
      OpenTrade(false, atr1);
}
