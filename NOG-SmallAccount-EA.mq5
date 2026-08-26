//+------------------------------------------------------------------+
//| NOG Small Account EA V2                                         |
//| Standalone MT5 EA: EMA crossover + trend/ADX/ATR risk controls  |
//+------------------------------------------------------------------+
#property strict
#property version   "2.00"
#property description "Low-risk EMA/ADX/ATR MT5 EA with trend, session and trade management filters"

#include <Trade/Trade.mqh>
CTrade trade;

input group "Strategy"
input ENUM_TIMEFRAMES InpTimeframe       = PERIOD_M15;
input int             InpFastEMA         = 20;
input int             InpSlowEMA         = 50;
input int             InpTrendEMA        = 200;
input int             InpADXPeriod       = 14;
input double          InpMinADX          = 20.0;
input int             InpATRPeriod       = 14;
input double          InpATRStopMult     = 1.5;
input double          InpRiskReward      = 1.5;
input double          InpMinATRPoints    = 0.0;

input group "Risk Management"
input double          InpRiskPercent     = 0.25;
input double          InpMaxDailyLoss    = 1.50;
input int             InpMaxTradesDay    = 2;
input int             InpMaxConsecLosses = 2;
input double          InpMaxSpreadPts    = 50.0;
input bool            InpEmergencyStop   = false;

input group "Trading Session (server time)"
input bool            InpUseSession      = true;
input int             InpSessionStartHour= 7;
input int             InpSessionEndHour  = 20;

input group "Position Management"
input bool            InpUseBreakEven    = true;
input double          InpBreakEvenAtR    = 1.0;
input double          InpBreakEvenLockR  = 0.10;
input bool            InpUseTrailing     = true;
input double          InpTrailStartR     = 1.20;
input double          InpTrailATRMult    = 1.0;

input group "Execution"
input ulong           InpMagicNumber     = 26082602;
input int             InpSlippagePoints  = 20;
input bool            InpOnePositionOnly = true;

int fast_handle=INVALID_HANDLE, slow_handle=INVALID_HANDLE, trend_handle=INVALID_HANDLE;
int atr_handle=INVALID_HANDLE, adx_handle=INVALID_HANDLE;
datetime last_bar_time=0;
int current_day_key=0, trades_today=0, consecutive_losses=0;
double day_start_balance=0.0;

int DayKey(){ MqlDateTime dt; TimeToStruct(TimeCurrent(),dt); return dt.year*10000+dt.mon*100+dt.day; }

void RefreshDailyState(){
   int key=DayKey();
   if(key!=current_day_key){ current_day_key=key; day_start_balance=AccountInfoDouble(ACCOUNT_BALANCE); trades_today=0; consecutive_losses=0; }
}

double DailyLossPercent(){
   RefreshDailyState();
   if(day_start_balance<=0.0) return 0.0;
   double equity=AccountInfoDouble(ACCOUNT_EQUITY);
   if(equity>=day_start_balance) return 0.0;
   return (day_start_balance-equity)/day_start_balance*100.0;
}

double SpreadPoints(){
   MqlTick t; if(!SymbolInfoTick(_Symbol,t)) return DBL_MAX;
   double p=SymbolInfoDouble(_Symbol,SYMBOL_POINT); if(p<=0) return DBL_MAX;
   return (t.ask-t.bid)/p;
}

bool InSession(){
   if(!InpUseSession) return true;
   MqlDateTime dt; TimeToStruct(TimeCurrent(),dt);
   if(InpSessionStartHour==InpSessionEndHour) return true;
   if(InpSessionStartHour<InpSessionEndHour) return (dt.hour>=InpSessionStartHour && dt.hour<InpSessionEndHour);
   return (dt.hour>=InpSessionStartHour || dt.hour<InpSessionEndHour);
}

bool HasOpenPosition(){
   for(int i=PositionsTotal()-1;i>=0;--i){
      ulong ticket=PositionGetTicket(i); if(ticket==0) continue;
      if(PositionGetString(POSITION_SYMBOL)==_Symbol && (ulong)PositionGetInteger(POSITION_MAGIC)==InpMagicNumber) return true;
   }
   return false;
}

int VolumeDigits(double step){ int d=0; while(d<8 && MathAbs(step-NormalizeDouble(step,d))>1e-12) d++; return d; }

double NormalizeVolume(double volume){
   double vmin=SymbolInfoDouble(_Symbol,SYMBOL_VOLUME_MIN), vmax=SymbolInfoDouble(_Symbol,SYMBOL_VOLUME_MAX), step=SymbolInfoDouble(_Symbol,SYMBOL_VOLUME_STEP);
   if(step<=0) return 0.0;
   volume=MathFloor(volume/step)*step;
   // Do not force broker minimum if it would exceed requested risk.
   if(volume<vmin) return 0.0;
   volume=MathMin(vmax,volume);
   return NormalizeDouble(volume,VolumeDigits(step));
}

double CalculateRiskVolume(double entry,double stop){
   double risk=AccountInfoDouble(ACCOUNT_BALANCE)*InpRiskPercent/100.0;
   double distance=MathAbs(entry-stop), ts=SymbolInfoDouble(_Symbol,SYMBOL_TRADE_TICK_SIZE), tv=SymbolInfoDouble(_Symbol,SYMBOL_TRADE_TICK_VALUE);
   if(risk<=0 || distance<=0 || ts<=0 || tv<=0) return 0.0;
   double loss_per_lot=(distance/ts)*tv; if(loss_per_lot<=0) return 0.0;
   return NormalizeVolume(risk/loss_per_lot);
}

bool NewBar(){
   datetime t[1]; if(CopyTime(_Symbol,InpTimeframe,0,1,t)!=1) return false;
   if(t[0]==last_bar_time) return false; last_bar_time=t[0]; return true;
}

bool ReadSignals(double &f1,double &f2,double &s1,double &s2,double &trend,double &atr,double &adx,double &close1){
   double f[2],s[2],tr[1],a[1],x[1],c[1];
   if(CopyBuffer(fast_handle,0,1,2,f)!=2 || CopyBuffer(slow_handle,0,1,2,s)!=2) return false;
   if(CopyBuffer(trend_handle,0,1,1,tr)!=1 || CopyBuffer(atr_handle,0,1,1,a)!=1 || CopyBuffer(adx_handle,0,1,1,x)!=1) return false;
   if(CopyClose(_Symbol,InpTimeframe,1,1,c)!=1) return false;
   f1=f[0]; f2=f[1]; s1=s[0]; s2=s[1]; trend=tr[0]; atr=a[0]; adx=x[0]; close1=c[0]; return true;
}

bool TradingAllowed(){
   RefreshDailyState();
   if(InpEmergencyStop || !TerminalInfoInteger(TERMINAL_CONNECTED) || !TerminalInfoInteger(TERMINAL_TRADE_ALLOWED) || !MQLInfoInteger(MQL_TRADE_ALLOWED)) return false;
   if(!InSession()) return false;
   if(InpMaxDailyLoss>0 && DailyLossPercent()>=InpMaxDailyLoss) return false;
   if(InpMaxTradesDay>0 && trades_today>=InpMaxTradesDay) return false;
   if(InpMaxConsecLosses>0 && consecutive_losses>=InpMaxConsecLosses) return false;
   if(InpMaxSpreadPts>0 && SpreadPoints()>InpMaxSpreadPts) return false;
   if(InpOnePositionOnly && HasOpenPosition()) return false;
   return true;
}

bool OpenTrade(bool buy,double atr){
   MqlTick t; if(!SymbolInfoTick(_Symbol,t)) return false;
   int digits=(int)SymbolInfoInteger(_Symbol,SYMBOL_DIGITS);
   double dist=atr*InpATRStopMult, entry=buy?t.ask:t.bid;
   double sl=NormalizeDouble(buy?entry-dist:entry+dist,digits);
   double tp=NormalizeDouble(buy?entry+dist*InpRiskReward:entry-dist*InpRiskReward,digits);
   double vol=CalculateRiskVolume(entry,sl); if(vol<=0){ Print("Trade skipped: minimum lot would exceed risk target"); return false; }
   bool ok=buy?trade.Buy(vol,_Symbol,0,sl,tp,"NOG V2 BUY"):trade.Sell(vol,_Symbol,0,sl,tp,"NOG V2 SELL");
   if(ok){ trades_today++; return true; }
   Print("Order failed: ",trade.ResultRetcode()," ",trade.ResultRetcodeDescription()); return false;
}

void ManagePositions(){
   if(!InpUseBreakEven && !InpUseTrailing) return;
   double atrbuf[1]; if(CopyBuffer(atr_handle,0,0,1,atrbuf)!=1 || atrbuf[0]<=0) return;
   MqlTick t; if(!SymbolInfoTick(_Symbol,t)) return;
   int digits=(int)SymbolInfoInteger(_Symbol,SYMBOL_DIGITS);
   for(int i=PositionsTotal()-1;i>=0;--i){
      ulong ticket=PositionGetTicket(i); if(ticket==0) continue;
      if(PositionGetString(POSITION_SYMBOL)!=_Symbol || (ulong)PositionGetInteger(POSITION_MAGIC)!=InpMagicNumber) continue;
      ENUM_POSITION_TYPE type=(ENUM_POSITION_TYPE)PositionGetInteger(POSITION_TYPE);
      double open=PositionGetDouble(POSITION_PRICE_OPEN), sl=PositionGetDouble(POSITION_SL), tp=PositionGetDouble(POSITION_TP);
      double initialRisk=(tp>0 && InpRiskReward>0)?MathAbs(tp-open)/InpRiskReward:atrbuf[0]*InpATRStopMult;
      if(initialRisk<=0) continue;
      double price=(type==POSITION_TYPE_BUY)?t.bid:t.ask;
      double profitDist=(type==POSITION_TYPE_BUY)?price-open:open-price;
      double newSL=sl;
      if(InpUseBreakEven && profitDist>=initialRisk*InpBreakEvenAtR){
         double be=(type==POSITION_TYPE_BUY)?open+initialRisk*InpBreakEvenLockR:open-initialRisk*InpBreakEvenLockR;
         if(type==POSITION_TYPE_BUY && (newSL==0 || be>newSL)) newSL=be;
         if(type==POSITION_TYPE_SELL && (newSL==0 || be<newSL)) newSL=be;
      }
      if(InpUseTrailing && profitDist>=initialRisk*InpTrailStartR){
         double trail=(type==POSITION_TYPE_BUY)?price-atrbuf[0]*InpTrailATRMult:price+atrbuf[0]*InpTrailATRMult;
         if(type==POSITION_TYPE_BUY && (newSL==0 || trail>newSL)) newSL=trail;
         if(type==POSITION_TYPE_SELL && (newSL==0 || trail<newSL)) newSL=trail;
      }
      newSL=NormalizeDouble(newSL,digits);
      if(newSL>0 && MathAbs(newSL-sl)>=SymbolInfoDouble(_Symbol,SYMBOL_POINT)) trade.PositionModify(ticket,newSL,tp);
   }
}

int OnInit(){
   if(InpFastEMA<=0 || InpSlowEMA<=0 || InpTrendEMA<=0 || InpATRPeriod<=0 || InpADXPeriod<=0 || InpFastEMA>=InpSlowEMA) return INIT_PARAMETERS_INCORRECT;
   fast_handle=iMA(_Symbol,InpTimeframe,InpFastEMA,0,MODE_EMA,PRICE_CLOSE);
   slow_handle=iMA(_Symbol,InpTimeframe,InpSlowEMA,0,MODE_EMA,PRICE_CLOSE);
   trend_handle=iMA(_Symbol,InpTimeframe,InpTrendEMA,0,MODE_EMA,PRICE_CLOSE);
   atr_handle=iATR(_Symbol,InpTimeframe,InpATRPeriod);
   adx_handle=iADX(_Symbol,InpTimeframe,InpADXPeriod);
   if(fast_handle==INVALID_HANDLE || slow_handle==INVALID_HANDLE || trend_handle==INVALID_HANDLE || atr_handle==INVALID_HANDLE || adx_handle==INVALID_HANDLE) return INIT_FAILED;
   trade.SetExpertMagicNumber(InpMagicNumber); trade.SetDeviationInPoints(InpSlippagePoints); trade.SetTypeFillingBySymbol(_Symbol);
   RefreshDailyState(); return INIT_SUCCEEDED;
}

void OnDeinit(const int reason){
   if(fast_handle!=INVALID_HANDLE) IndicatorRelease(fast_handle); if(slow_handle!=INVALID_HANDLE) IndicatorRelease(slow_handle);
   if(trend_handle!=INVALID_HANDLE) IndicatorRelease(trend_handle); if(atr_handle!=INVALID_HANDLE) IndicatorRelease(atr_handle); if(adx_handle!=INVALID_HANDLE) IndicatorRelease(adx_handle);
}

void OnTradeTransaction(const MqlTradeTransaction &trans,const MqlTradeRequest &request,const MqlTradeResult &result){
   if(trans.type!=TRADE_TRANSACTION_DEAL_ADD || trans.deal==0) return;
   if(!HistoryDealSelect(trans.deal)) return;
   if(HistoryDealGetString(trans.deal,DEAL_SYMBOL)!=_Symbol || (ulong)HistoryDealGetInteger(trans.deal,DEAL_MAGIC)!=InpMagicNumber) return;
   ENUM_DEAL_ENTRY entry=(ENUM_DEAL_ENTRY)HistoryDealGetInteger(trans.deal,DEAL_ENTRY);
   if(entry!=DEAL_ENTRY_OUT && entry!=DEAL_ENTRY_OUT_BY) return;
   double pnl=HistoryDealGetDouble(trans.deal,DEAL_PROFIT)+HistoryDealGetDouble(trans.deal,DEAL_SWAP)+HistoryDealGetDouble(trans.deal,DEAL_COMMISSION);
   if(pnl<0) consecutive_losses++; else if(pnl>0) consecutive_losses=0;
}

void OnTick(){
   ManagePositions();
   if(!NewBar() || !TradingAllowed()) return;
   double f1,f2,s1,s2,tr,atr,adx,close1;
   if(!ReadSignals(f1,f2,s1,s2,tr,atr,adx,close1) || atr<=0) return;
   double point=SymbolInfoDouble(_Symbol,SYMBOL_POINT);
   if(InpMinATRPoints>0 && point>0 && atr/point<InpMinATRPoints) return;
   if(InpMinADX>0 && adx<InpMinADX) return;
   bool bull=(f2<=s2 && f1>s1 && close1>tr);
   bool bear=(f2>=s2 && f1<s1 && close1<tr);
   if(bull) OpenTrade(true,atr); else if(bear) OpenTrade(false,atr);
}
