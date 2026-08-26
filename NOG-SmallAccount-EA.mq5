//+------------------------------------------------------------------+
//| NOG Small Account EA V1                                         |
//| Standalone MT5 Expert Advisor for low-risk demo testing          |
//+------------------------------------------------------------------+
#property strict
#property version   "1.00"
#property description "Standalone EMA + ATR MT5 EA with low-risk controls"

#include <Trade/Trade.mqh>

CTrade trade;

input group "Strategy"
input ENUM_TIMEFRAMES InpTimeframe      = PERIOD_M15;
input int             InpFastEMA        = 20;
input int             InpSlowEMA        = 50;
input int             InpATRPeriod      = 14;
input double          InpATRStopMult    = 1.5;
input double          InpRiskReward     = 1.5;

input group "Risk Management"
input double          InpRiskPercent    = 0.50;   // % balance risked per trade
input double          InpMaxDailyLoss   = 2.00;   // % from day-start balance
input int             InpMaxTradesDay   = 2;
input double          InpMaxSpreadPts   = 50.0;
input bool            InpEmergencyStop  = false;

input group "Execution"
input ulong           InpMagicNumber    = 26082601;
input int             InpSlippagePoints = 20;
input bool            InpOnePositionOnly = true;

int      fast_handle = INVALID_HANDLE;
int      slow_handle = INVALID_HANDLE;
int      atr_handle  = INVALID_HANDLE;
datetime last_bar_time = 0;
int      current_day_key = 0;
double   day_start_balance = 0.0;
int      trades_today = 0;

int DayKey()
{
   MqlDateTime dt;
   TimeToStruct(TimeCurrent(), dt);
   return dt.year * 10000 + dt.mon * 100 + dt.day;
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

   return ((day_start_balance - equity) / day_start_balance) * 100.0;
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
   double vmin  = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
   double vmax  = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MAX);
   double vstep = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);

   if(vstep <= 0.0)
      return 0.0;

   volume = MathFloor(volume / vstep) * vstep;
   volume = MathMax(vmin, MathMin(vmax, volume));
   return NormalizeDouble(volume, VolumeDigits(vstep));
}

double CalculateRiskVolume(double entry_price, double stop_price)
{
   double balance = AccountInfoDouble(ACCOUNT_BALANCE);
   double risk_money = balance * InpRiskPercent / 100.0;
   double distance = MathAbs(entry_price - stop_price);

   double tick_size  = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE);
   double tick_value = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_VALUE);

   if(risk_money <= 0.0 || distance <= 0.0 || tick_size <= 0.0 || tick_value <= 0.0)
      return 0.0;

   double loss_per_lot = (distance / tick_size) * tick_value;
   if(loss_per_lot <= 0.0)
      return 0.0;

   return NormalizeVolume(risk_money / loss_per_lot);
}

bool NewBar()
{
   datetime times[1];
   if(CopyTime(_Symbol, InpTimeframe, 0, 1, times) != 1)
      return false;

   if(times[0] == last_bar_time)
      return false;

   last_bar_time = times[0];
   return true;
}

bool ReadSignals(double &fast1, double &fast2,
                 double &slow1, double &slow2,
                 double &atr1)
{
   double fast[2], slow[2], atr[1];

   if(CopyBuffer(fast_handle, 0, 1, 2, fast) != 2)
      return false;
   if(CopyBuffer(slow_handle, 0, 1, 2, slow) != 2)
      return false;
   if(CopyBuffer(atr_handle, 0, 1, 1, atr) != 1)
      return false;

   fast1 = fast[0];
   fast2 = fast[1];
   slow1 = slow[0];
   slow2 = slow[1];
   atr1  = atr[0];
   return true;
}

bool TradingAllowed()
{
   RefreshDailyState();

   if(InpEmergencyStop)
      return false;
   if(!TerminalInfoInteger(TERMINAL_CONNECTED))
      return false;
   if(!TerminalInfoInteger(TERMINAL_TRADE_ALLOWED))
      return false;
   if(!MQLInfoInteger(MQL_TRADE_ALLOWED))
      return false;
   if(InpMaxDailyLoss > 0.0 && DailyLossPercent() >= InpMaxDailyLoss)
      return false;
   if(InpMaxTradesDay > 0 && trades_today >= InpMaxTradesDay)
      return false;
   if(InpMaxSpreadPts > 0.0 && SpreadPoints() > InpMaxSpreadPts)
      return false;
   if(InpOnePositionOnly && HasOpenPosition())
      return false;

   return true;
}

bool OpenBuy(double atr_value)
{
   MqlTick tick;
   if(!SymbolInfoTick(_Symbol, tick))
      return false;

   int digits = (int)SymbolInfoInteger(_Symbol, SYMBOL_DIGITS);
   double stop_distance = atr_value * InpATRStopMult;
   double sl = NormalizeDouble(tick.ask - stop_distance, digits);
   double tp = NormalizeDouble(tick.ask + stop_distance * InpRiskReward, digits);
   double volume = CalculateRiskVolume(tick.ask, sl);

   if(volume <= 0.0)
      return false;

   if(trade.Buy(volume, _Symbol, 0.0, sl, tp, "NOG EMA-ATR BUY"))
   {
      trades_today++;
      return true;
   }

   Print("BUY failed. Retcode=", trade.ResultRetcode(), " ", trade.ResultRetcodeDescription());
   return false;
}

bool OpenSell(double atr_value)
{
   MqlTick tick;
   if(!SymbolInfoTick(_Symbol, tick))
      return false;

   int digits = (int)SymbolInfoInteger(_Symbol, SYMBOL_DIGITS);
   double stop_distance = atr_value * InpATRStopMult;
   double sl = NormalizeDouble(tick.bid + stop_distance, digits);
   double tp = NormalizeDouble(tick.bid - stop_distance * InpRiskReward, digits);
   double volume = CalculateRiskVolume(tick.bid, sl);

   if(volume <= 0.0)
      return false;

   if(trade.Sell(volume, _Symbol, 0.0, sl, tp, "NOG EMA-ATR SELL"))
   {
      trades_today++;
      return true;
   }

   Print("SELL failed. Retcode=", trade.ResultRetcode(), " ", trade.ResultRetcodeDescription());
   return false;
}

int OnInit()
{
   if(InpFastEMA <= 0 || InpSlowEMA <= 0 || InpATRPeriod <= 0 || InpFastEMA >= InpSlowEMA)
      return INIT_PARAMETERS_INCORRECT;

   fast_handle = iMA(_Symbol, InpTimeframe, InpFastEMA, 0, MODE_EMA, PRICE_CLOSE);
   slow_handle = iMA(_Symbol, InpTimeframe, InpSlowEMA, 0, MODE_EMA, PRICE_CLOSE);
   atr_handle  = iATR(_Symbol, InpTimeframe, InpATRPeriod);

   if(fast_handle == INVALID_HANDLE || slow_handle == INVALID_HANDLE || atr_handle == INVALID_HANDLE)
      return INIT_FAILED;

   trade.SetExpertMagicNumber(InpMagicNumber);
   trade.SetDeviationInPoints(InpSlippagePoints);
   trade.SetTypeFillingBySymbol(_Symbol);

   RefreshDailyState();
   return INIT_SUCCEEDED;
}

void OnDeinit(const int reason)
{
   if(fast_handle != INVALID_HANDLE)
      IndicatorRelease(fast_handle);
   if(slow_handle != INVALID_HANDLE)
      IndicatorRelease(slow_handle);
   if(atr_handle != INVALID_HANDLE)
      IndicatorRelease(atr_handle);
}

void OnTick()
{
   if(!NewBar())
      return;

   if(!TradingAllowed())
      return;

   double fast1, fast2, slow1, slow2, atr1;
   if(!ReadSignals(fast1, fast2, slow1, slow2, atr1))
      return;

   if(atr1 <= 0.0)
      return;

   bool bullish_cross = (fast2 <= slow2 && fast1 > slow1);
   bool bearish_cross = (fast2 >= slow2 && fast1 < slow1);

   if(bullish_cross)
      OpenBuy(atr1);
   else if(bearish_cross)
      OpenSell(atr1);
}
