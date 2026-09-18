#property strict
#property version "1.00"
#property description "XAUUSD DEMO USD only. Planned loss <=10, target 10; default PREVIEW."
#include "NOG_CashMath.mqh"

input bool InpEnableDemoOrders=false;
input bool InpAcknowledgeHighRisk=false; // $10 is 10% of $100; demo engineering only
input bool InpPauseNewEntries=false;
input double InpRoundTripCommissionPerLot=-1.0; // USD, -1 unknown; verify before arming
input double InpRoundTripFixedFee=-1.0; // USD per position round trip; -1 unknown
const ulong MAGIC=2609181010;
const string URL="http://127.0.0.1:8765/signal",DIR="NOG_CashDemo";
const int DEVIATION_POINTS=20;
long g_login=0,g_last_bar=0,g_day=0,g_anchor=0,g_started=0,g_tick=0;
string g_server="",g_token="",g_status="",g_spec="";
int g_file=INVALID_HANDLE,g_daily=0,g_total=0,g_halt=0,g_seq=0;
double g_initial_equity=0,g_initial_balance=0;
ulong g_tick_seen=0,g_last_mono=0;
long g_last_wall=0;
struct Signal {string id;string mode;string action;long issued;long expires;long bar;long login;double atr;double bid;double ask;};

string I(const long x) {return IntegerToString(x);}
uint Hash32(const string s)
  {uint h=2166136261;for(int i=0;i<StringLen(s);i++) h=(h^(uint)StringGetCharacter(s,i))*16777619;return h;}
bool IntegerText(const string s,const int maxlen=12)
  {if(StringLen(s)<1||StringLen(s)>maxlen)return false;
   for(int i=0;i<StringLen(s);i++)if(StringGetCharacter(s,i)<'0'||StringGetCharacter(s,i)>'9')return false;return true;}
bool DecimalText(const string s)
  {if(StringLen(s)<1||StringLen(s)>32)return false;int dots=0,digits=0;
   for(int i=0;i<StringLen(s);i++){ushort c=StringGetCharacter(s,i);if(c=='.')dots++;else if(c>='0'&&c<='9')digits++;else return false;}
   return dots<=1&&digits>0&&MathIsValidNumber(StringToDouble(s));}
bool HexText(const string s,const int n)
  {if(StringLen(s)!=n)return false;for(int i=0;i<n;i++){ushort c=StringGetCharacter(s,i);if(!((c>='0'&&c<='9')||(c>='a'&&c<='f')))return false;}return true;}
void Show(const string text)
  {Comment("NOG CASH DEMO | ",InpEnableDemoOrders?"ORDER SWITCH ON":"PREVIEW ONLY",
   "\nXAUUSD M5 | planned loss <= $10 | target ~$10 | NOT GUARANTEED\n",text);
   if(text!=g_status){Print("NOG_CASH | ",text);g_status=text;}}
bool SameDemo()
  {return AccountInfoInteger(ACCOUNT_TRADE_MODE)==ACCOUNT_TRADE_MODE_DEMO &&
   AccountInfoString(ACCOUNT_CURRENCY)=="USD" && _Symbol=="XAUUSD" &&
   SymbolInfoString(_Symbol,SYMBOL_CURRENCY_PROFIT)=="USD" &&
   SymbolInfoInteger(_Symbol,SYMBOL_CHART_MODE)==SYMBOL_CHART_MODE_BID &&
   AccountInfoInteger(ACCOUNT_LOGIN)==g_login && AccountInfoString(ACCOUNT_SERVER)==g_server &&
   TerminalInfoInteger(TERMINAL_CONNECTED);}
bool FeesKnown()
  {return CashNonnegative(InpRoundTripCommissionPerLot)&&CashNonnegative(InpRoundTripFixedFee);}
bool DailyAllows(const double risk)
  {return CashDaily(g_initial_equity,g_initial_balance,AccountInfoDouble(ACCOUNT_EQUITY),
   AccountInfoDouble(ACCOUNT_BALANCE),risk,g_daily,g_total);}

bool Save()
  {
   if(g_file==INVALID_HANDLE)return false;
   string row="C1;"+g_spec+";"+I(++g_seq)+";"+I(g_day)+";"+I(g_anchor)+";"+
      DoubleToString(g_initial_equity,8)+";"+DoubleToString(g_initial_balance,8)+";"+
      I(g_daily)+";"+I(g_total)+";"+I(g_last_bar)+";"+I(g_halt);
   row+=";"+I((long)Hash32(row))+"\r\n";
   FileSeek(g_file,0,SEEK_END);ResetLastError();
   uint written=FileWriteString(g_file,row);FileFlush(g_file);
   return written==(uint)StringLen(row)&&GetLastError()==0;
  }
void Halt(const string why)
  {g_halt=1;Save();Show("HALTED_REVIEW_REQUIRED | "+why);}

bool LoadState()
  {
   string name=DIR+"\\state_"+I(g_login)+"_"+I((long)Hash32(g_server))+".journal";
   bool existed=FileIsExist(name);
   // No FILE_SHARE flags: exclusive journal in this terminal only.
   g_file=FileOpen(name,FILE_READ|FILE_WRITE|FILE_TXT|FILE_ANSI,0,CP_UTF8);
   if(g_file==INVALID_HANDLE){Show("STATE_LOCKED_OR_UNREADABLE | one receiver only");return false;}
   g_spec=I(g_login)+":"+I((long)Hash32(g_server+"|CASH10_10_V1|ONE_ENTRY|"+
                DoubleToString(InpRoundTripCommissionPerLot,8)+"|"+DoubleToString(InpRoundTripFixedFee,8)));
   if(FileSize(g_file)==0)
     {
      if(existed){Show("EMPTY_STATE_REVIEW_REQUIRED");return false;}
      if(!HistorySelect(0,TimeTradeServer()))return false;
      for(int h=0;h<HistoryOrdersTotal();h++)
         if(HistoryOrderGetInteger(HistoryOrderGetTicket(h),ORDER_MAGIC)==(long)MAGIC)return false;
      g_day=(long)TimeGMT()/86400;g_anchor=(long)TimeTradeServer();
      g_initial_equity=AccountInfoDouble(ACCOUNT_EQUITY);g_initial_balance=AccountInfoDouble(ACCOUNT_BALANCE);
      return CashPositive(g_initial_equity)&&CashPositive(g_initial_balance)&&Save();
     }
   string previous_spec="";
   while(!FileIsEnding(g_file))
     {
      string row=FileReadString(g_file),f[];
      if(StringSplit(row,';',f)!=12||f[0]!="C1")return false;
      if(g_total>0&&previous_spec!=""&&f[1]!=previous_spec)return false;
      previous_spec=f[1];string base=f[0];for(int k=1;k<11;k++)base+=";"+f[k];
      if(f[11]!=I((long)Hash32(base)))return false;
      for(int k=2;k<12;k++)if(!DecimalText(f[k])||((k<5||k>6)&&!IntegerText(f[k])))return false;
      int seq=(int)StringToInteger(f[2]),daily=(int)StringToInteger(f[7]);
      int total=(int)StringToInteger(f[8]),halt=(int)StringToInteger(f[10]);
      long day=StringToInteger(f[3]),bar=StringToInteger(f[9]);
      if(seq!=g_seq+1||day<g_day||bar<g_last_bar||total<g_total||total>1||daily<0||daily>1||
         daily>total||halt<0||halt>1||(day==g_day&&daily<g_daily))return false;
      g_seq=seq;g_day=day;g_anchor=StringToInteger(f[4]);g_initial_equity=StringToDouble(f[5]);
      g_initial_balance=StringToDouble(f[6]);g_daily=daily;g_total=total;g_last_bar=bar;g_halt=halt;
      if(!CashPositive(g_initial_equity)||!CashPositive(g_initial_balance)||g_seq>20000)return false;
     }
   if(g_seq==0)return false;
   if(previous_spec!=g_spec)
     {if(g_total>0){Show("SETTINGS_FROZEN_AFTER_SUBMISSION");return false;}return Save();}
   return true;
  }

bool RefreshDay()
  {
   long day=(long)TimeGMT()/86400;
   if(day<g_day){Halt("UTC_CLOCK_MOVED_BACK");return false;}
   // Check funding before resetting the daily anchor, including after downtime.
   if(!HistorySelect((datetime)g_anchor,TimeTradeServer()))return false;
   for(int i=0;i<HistoryDealsTotal();i++)
     {
      ulong ticket=HistoryDealGetTicket(i);long type=HistoryDealGetInteger(ticket,DEAL_TYPE);
      if(HistoryDealGetInteger(ticket,DEAL_TIME)>g_anchor &&
         (type==DEAL_TYPE_BALANCE||type==DEAL_TYPE_CREDIT||type==DEAL_TYPE_CORRECTION||type==DEAL_TYPE_BONUS))
        {Halt("CASH_CHANGE_REVIEW_REQUIRED");return false;}
     }
   if(day>g_day)
     {
      if(PositionsTotal()>0||OrdersTotal()>0)return false;
      g_day=day;g_anchor=(long)TimeTradeServer();g_daily=0;
      g_initial_equity=AccountInfoDouble(ACCOUNT_EQUITY);g_initial_balance=AccountInfoDouble(ACCOUNT_BALANCE);
      if(!Save()){g_halt=1;return false;}
     }
   return true;
  }

bool Parse(const string text,Signal &s)
  {
   string f[];
   if(StringLen(text)>640||StringSplit(text,';',f)!=13||f[0]!="NOG_CASH_V1"||
      (f[1]!="PREVIEW"&&f[1]!="DEMO_SEND")||!HexText(f[2],32)||f[3]!=_Symbol||f[4]!="M5")return false;
   if(f[5]!="BUY"&&f[5]!="SELL"&&f[5]!="WAIT")return false;
   for(int i=6;i<=9;i++)if(!IntegerText(f[i]))return false;
   for(int i=10;i<=12;i++)if(!DecimalText(f[i]))return false;
   s.mode=f[1];s.id=f[2];s.action=f[5];s.issued=StringToInteger(f[6]);s.expires=StringToInteger(f[7]);
   s.bar=StringToInteger(f[8]);s.login=StringToInteger(f[9]);s.atr=StringToDouble(f[10]);s.bid=StringToDouble(f[11]);s.ask=StringToDouble(f[12]);
   long now=(long)TimeGMT();
   return s.login==g_login && s.issued>=g_started && s.issued<=now+3 && now<s.expires &&
      s.expires>s.issued && s.expires-s.issued<=30 && s.bar==(long)iTime(_Symbol,PERIOD_M5,1) &&
      CashPositive(s.atr)&&CashPositive(s.bid)&&CashPositive(s.ask)&&s.ask>=s.bid;
  }
bool FreshQuote(MqlTick &tick)
  {
   if(!SymbolInfoTick(_Symbol,tick)||!CashPositive(tick.bid)||!CashPositive(tick.ask)||tick.ask<tick.bid||tick.time_msc<=0)return false;
   ulong ms=GetTickCount64();
   if(g_tick>0&&tick.time_msc<g_tick){Halt("TICK_CLOCK_MOVED_BACK");return false;}
   if(tick.time_msc>g_tick){g_tick=tick.time_msc;g_tick_seen=ms;}
   return ms-g_tick_seen<=10000 && tick.time_msc/1000>=(long)iTime(_Symbol,PERIOD_M5,0) &&
      tick.time_msc/1000<(long)iTime(_Symbol,PERIOD_M5,0)+300;
  }

bool CheckCash(const MqlTradeRequest &r,const double fee,double &loss,double &profit)
  {
   double stress=DEVIATION_POINTS*SymbolInfoDouble(_Symbol,SYMBOL_POINT);
   int sign=r.type==ORDER_TYPE_BUY?1:-1;
   double a=0,b=0;
   if(!OrderCalcProfit(r.type,_Symbol,r.volume,r.price+sign*stress,r.sl-sign*stress,a)||
      !OrderCalcProfit(r.type,_Symbol,r.volume,r.price+sign*stress,r.tp-sign*stress,b)||
      !MathIsValidNumber(a)||!MathIsValidNumber(b)||a>=0)return false;
   loss=-a+fee;profit=b-fee;
   return loss>0&&loss<=10+1e-8&&profit>=10-1e-8;
  }
bool Plan(Signal &s,MqlTick &tick,MqlTradeRequest &r,CashPlan &p,string &why)
  {
   if(!SameDemo()||!FreshQuote(tick)){why="DEMO_USD_OR_QUOTE";return false;}
   if(!FeesKnown()){why="COMMISSION_UNKNOWN";return false;}
   if((tick.ask-tick.bid)/s.atr>0.12){why="SPREAD_ATR";return false;}
   if(MathAbs((tick.bid+tick.ask-s.bid-s.ask)/2)>0.25*s.atr){why="PRICE_DRIFT";return false;}
   double point=SymbolInfoDouble(_Symbol,SYMBOL_POINT),size=SymbolInfoDouble(_Symbol,SYMBOL_TRADE_TICK_SIZE);
   double minv=SymbolInfoDouble(_Symbol,SYMBOL_VOLUME_MIN),step=SymbolInfoDouble(_Symbol,SYMBOL_VOLUME_STEP);
   double maxv=SymbolInfoDouble(_Symbol,SYMBOL_VOLUME_MAX),limit=SymbolInfoDouble(_Symbol,SYMBOL_VOLUME_LIMIT);
   if(limit>0)maxv=MathMin(maxv,limit);
   if(!CashPositive(point)||!CashPositive(size)||!CashPositive(minv)){why="BROKER_SPEC";return false;}
   bool buy=s.action=="BUY";int sign=buy?1:-1;
   ENUM_ORDER_TYPE type=buy?ORDER_TYPE_BUY:ORDER_TYPE_SELL;
   double entry=buy?tick.ask:tick.bid,a=0,b=0;
   if(!OrderCalcProfit(type,_Symbol,minv,entry,entry-sign*size,a)||
      !OrderCalcProfit(type,_Symbol,minv,entry,entry+sign*size,b)||!CashPositive(-a)||!CashPositive(b))
     {why="BROKER_TICK_CALC_FAILED";return false;}
   double stops=(double)SymbolInfoInteger(_Symbol,SYMBOL_TRADE_STOPS_LEVEL)*point;
   if(!CashMath(buy,tick.bid,tick.ask,s.atr,size,stops,DEVIATION_POINTS*point,minv,step,maxv,
        -a/(minv*size),b/(minv*size),InpRoundTripCommissionPerLot,InpRoundTripFixedFee,p))
     {why="CASH_PLAN_DOES_NOT_FIT | no forced lot or tighter ATR stop";return false;}
   ZeroMemory(r);r.action=TRADE_ACTION_DEAL;r.symbol=_Symbol;r.magic=MAGIC;r.type=type;
   r.volume=NormalizeDouble(p.volume,8);r.price=p.entry;r.sl=NormalizeDouble(p.sl,_Digits);r.tp=NormalizeDouble(p.tp,_Digits);
   r.deviation=DEVIATION_POINTS;r.type_filling=ORDER_FILLING_FOK;r.comment="NOGC:"+StringSubstr(s.id,0,20);
   double loss=0,profit=0;
   if(!CheckCash(r,p.fee,loss,profit)||MathAbs(loss-p.loss)>1e-5||MathAbs(profit-p.profit)>1e-5)
     {why="BROKER_FINAL_CASH_MISMATCH";return false;}
   if(!DailyAllows(loss)){why="DAILY_USD_OR_PILOT_LIMIT | need equity/balance>=100";return false;}
   long mode=SymbolInfoInteger(_Symbol,SYMBOL_TRADE_MODE),allowed=SymbolInfoInteger(_Symbol,SYMBOL_ORDER_MODE);
   if((mode!=SYMBOL_TRADE_MODE_FULL && !(buy&&mode==SYMBOL_TRADE_MODE_LONGONLY)&&!(!buy&&mode==SYMBOL_TRADE_MODE_SHORTONLY))||
      (allowed&SYMBOL_ORDER_MARKET)==0||(allowed&SYMBOL_ORDER_SL)==0||(allowed&SYMBOL_ORDER_TP)==0)
     {why="BROKER_ORDER_OR_STOPS_UNSUPPORTED";return false;}
   if((SymbolInfoInteger(_Symbol,SYMBOL_FILLING_MODE)&SYMBOL_FILLING_FOK)==0){why="FOK_UNSUPPORTED";return false;}
   double margin=0;
   if(!OrderCalcMargin(type,_Symbol,r.volume,entry,margin)||!CashNonnegative(margin)||
      margin>AccountInfoDouble(ACCOUNT_MARGIN_FREE)*.5){why="MARGIN_RESERVE";return false;}
   return true;
  }

void Process(Signal &s)
  {
   if(s.bar<=g_last_bar){Show("DUPLICATE_BAR_IGNORED");return;}
   g_last_bar=s.bar;if(!Save()){g_halt=1;Show("STATE_SAVE_FAILED | NO_ORDER");return;}
   if(s.action=="WAIT"){Show("AI_WAIT | NO_ORDER");return;}
   if(!SameDemo()||!RefreshDay()||g_halt>0||InpPauseNewEntries||PositionsTotal()>0||OrdersTotal()>0)
     {Show("GUARD_BLOCKED | NO_ORDER");return;}
   MqlTick tick;MqlTradeRequest r={};CashPlan p;string why;
   if(!Plan(s,tick,r,p,why)){Show("RISK_REJECT | "+why);return;}
   Print("NOG_CASH | PLAN | ",s.action," | lot=",r.volume," | SL=",r.sl," | TP=",r.tp,
         " | stressed_loss_usd=",p.loss," | stressed_profit_usd=",p.profit," | estimated_fee=",p.fee);
   if(s.mode!="DEMO_SEND"||!InpEnableDemoOrders){Show("PREVIEW_PLAN_OK | NO_ORDER");return;}
   if(!InpAcknowledgeHighRisk||!MQLInfoInteger(MQL_TRADE_ALLOWED)||!TerminalInfoInteger(TERMINAL_TRADE_ALLOWED)||
      !AccountInfoInteger(ACCOUNT_TRADE_ALLOWED)||!AccountInfoInteger(ACCOUNT_TRADE_EXPERT))
     {Show("DEMO_NOT_ARMED_OR_PERMISSION_OFF");return;}
   MqlTradeCheckResult checked={};
   if(!OrderCheck(r,checked)||checked.retcode!=0){Show("ORDER_CHECK_REJECT | "+I(checked.retcode));return;}
   MqlTick latest;double loss=0,profit=0;
   if(!SameDemo()||!FreshQuote(latest)||PositionsTotal()>0||OrdersTotal()>0||InpPauseNewEntries||
      (long)TimeGMT()>=s.expires||s.bar!=(long)iTime(_Symbol,PERIOD_M5,1)||
      MathAbs(latest.ask-tick.ask)>SymbolInfoDouble(_Symbol,SYMBOL_TRADE_TICK_SIZE)/2||
      MathAbs(latest.bid-tick.bid)>SymbolInfoDouble(_Symbol,SYMBOL_TRADE_TICK_SIZE)/2||
      !CheckCash(r,p.fee,loss,profit)||!DailyAllows(loss))
     {Show("REJECT_CHANGED_BEFORE_SEND");return;}
   // Uncertain reservation is flushed BEFORE the only broker request. Never auto-retry.
   g_daily++;g_total++;g_halt=1;
   if(!Save()){Show("RESERVATION_WRITE_FAILED | NO_ORDER");return;}
   if(!SameDemo()||PositionsTotal()>0||OrdersTotal()>0||(long)TimeGMT()>=s.expires||
      s.bar!=(long)iTime(_Symbol,PERIOD_M5,1)) {Halt("CHANGED_AFTER_RESERVATION | NO_ORDER");return;}
   MqlTradeResult result={};bool sent=OrderSend(r,result);
   Print("NOG_CASH | ORDER_RESULT | sent=",sent," | retcode=",result.retcode," | deal=",result.deal);
   if(!sent||result.retcode!=TRADE_RETCODE_DONE||result.deal==0){Halt("UNCERTAIN_OR_REJECTED | no resend");return;}
   if(!SameDemo()||!PositionSelect(_Symbol)||PositionGetInteger(POSITION_MAGIC)!=(long)MAGIC||
      PositionGetInteger(POSITION_TYPE)!=(r.type==ORDER_TYPE_BUY?POSITION_TYPE_BUY:POSITION_TYPE_SELL)||
      MathAbs(PositionGetDouble(POSITION_VOLUME)-r.volume)>1e-8||
      MathAbs(PositionGetDouble(POSITION_SL)-r.sl)>SymbolInfoDouble(_Symbol,SYMBOL_TRADE_TICK_SIZE)/2||
      MathAbs(PositionGetDouble(POSITION_TP)-r.tp)>SymbolInfoDouble(_Symbol,SYMBOL_TRADE_TICK_SIZE)/2)
     {Halt("VERIFY_POSITION_FILL_STOPS_MANUALLY");Alert("NOG CASH DEMO: inspect position and SL/TP now.");return;}
   if(!HistoryDealSelect(result.deal)||
      MathAbs(HistoryDealGetDouble(result.deal,DEAL_COMMISSION))+MathAbs(HistoryDealGetDouble(result.deal,DEAL_FEE))>p.fee+1e-8)
     {Halt("ACTUAL_ENTRY_FEE_EXCEEDS_ESTIMATE_OR_UNKNOWN");Alert("NOG CASH DEMO: inspect commission and position.");return;}
   double actual=0,stress=DEVIATION_POINTS*SymbolInfoDouble(_Symbol,SYMBOL_POINT);int sign=r.type==ORDER_TYPE_BUY?1:-1;
   if(!OrderCalcProfit(r.type,_Symbol,r.volume,PositionGetDouble(POSITION_PRICE_OPEN),r.sl-sign*stress,actual)||
      !MathIsValidNumber(actual)||actual>=0||-actual+p.fee>10+1e-8)
     {Halt("FILLED_RISK_EXCEEDS_PLAN");Alert("NOG CASH DEMO: inspect fill risk now.");return;}
   g_halt=0;if(!Save()){g_halt=1;Show("POST_SEND_STATE_ERROR");return;}
   Show("DEMO_POSITION_CONFIRMED | SL_TP_PRESENT | pilot_entries=1/1");
  }

int OnInit()
  {
   if(MQLInfoInteger(MQL_TESTER)||AccountInfoInteger(ACCOUNT_TRADE_MODE)!=ACCOUNT_TRADE_MODE_DEMO||
      AccountInfoString(ACCOUNT_CURRENCY)!="USD"||_Symbol!="XAUUSD"||_Period!=PERIOD_M5)
     {Print("NOG CASH: XAUUSD M5 on USD DEMO only, not Tester.");return INIT_FAILED;}
   if(!MathIsValidNumber(InpRoundTripCommissionPerLot)||!MathIsValidNumber(InpRoundTripFixedFee)||
      (InpRoundTripCommissionPerLot<0&&InpRoundTripCommissionPerLot!=-1)||
      (InpRoundTripFixedFee<0&&InpRoundTripFixedFee!=-1)||!CashSelfTest())return INIT_FAILED;
   g_login=AccountInfoInteger(ACCOUNT_LOGIN);g_server=AccountInfoString(ACCOUNT_SERVER);g_started=(long)TimeGMT();
   if(StringFind(g_server,";")>=0||StringFind(g_server,"\r")>=0||StringFind(g_server,"\n")>=0)return INIT_FAILED;
   int key=FileOpen(DIR+"\\bridge.token",FILE_READ|FILE_TXT|FILE_ANSI,0,CP_UTF8);
   if(key==INVALID_HANDLE){Print("NOG CASH: run cash_demo.py --install first.");return INIT_FAILED;}
   g_token=FileReadString(key);FileClose(key);if(!HexText(g_token,64))return INIT_FAILED;
   if(!SameDemo()||!LoadState()){Print("NOG CASH STATE STOP: preserve journal.");return INIT_FAILED;}
   MqlTick tick={};if(!SymbolInfoTick(_Symbol,tick))return INIT_FAILED;g_tick=tick.time_msc;g_tick_seen=GetTickCount64();
   if(!EventSetTimer(2))return INIT_FAILED;
   Print("NOG_CASH | CASH_SELF_TEST_PASS | default preview; planned cash is NOT a guarantee");
   Show("STARTED | loss ceiling $10 | target $10 | NO_ORDER by default");return INIT_SUCCEEDED;
  }
void OnTimer()
  {
   if(!SameDemo()){Show("REJECT_ACCOUNT_CONNECTION_CURRENCY");return;}
   // Protection observation stays active even after the submission cap/pause.
   if(PositionSelect(_Symbol)&&PositionGetInteger(POSITION_MAGIC)==(long)MAGIC&&
      (PositionGetDouble(POSITION_SL)<=0||PositionGetDouble(POSITION_TP)<=0))
     {if(g_halt==0){Halt("MISSING_PROTECTION");Alert("NOG CASH DEMO: missing SL/TP. Inspect position.");}return;}
   if(InpPauseNewEntries||g_halt>0||g_total>=1){Show("PAUSED_HALTED_OR_PILOT_CAP | existing positions NOT closed");return;}
   if(!RefreshDay()||!DailyAllows(0)){Show("DAILY_USD_LIMIT_OR_HISTORY_REVIEW");return;}
   if(PositionsTotal()>0||OrdersTotal()>0){Show("ACCOUNT_BUSY | NO_ENTRY");return;}
   if(!FeesKnown()){Show("COMMISSION_UNKNOWN | verify both round-trip fee inputs; no API polling");return;}
   MqlTick tick;if(!FreshQuote(tick)){Show("WAIT_FRESH_QUOTE");return;}
   ulong mono=GetTickCount64();long wall=(long)TimeGMT();
   if(g_last_mono>0&&MathAbs((double)(wall-g_last_wall)-(double)(mono-g_last_mono)/1000)>3)
     {g_last_mono=mono;g_last_wall=wall;Halt("CLOCK_JUMP");return;}
   g_last_mono=mono;g_last_wall=wall;
   string headers="X-NOG-Receiver: CASH_DEMO_V1\r\nX-NOG-Token: "+g_token+"\r\nX-NOG-Account: "+I(g_login)+
      "\r\nX-NOG-Server: "+g_server+"\r\nX-NOG-Symbol: XAUUSD\r\nX-NOG-Timeframe: M5\r\n";
   char data[],result[];string reply;
   ResetLastError();int code=WebRequest("GET",URL,headers,1000,data,result,reply);
   if(code==-1){Show("API_UNAVAILABLE | error="+I(GetLastError()));return;}
   if(code==204){Show("NO_SIGNAL");return;}if(code!=200){Show("REJECT_HTTP | "+I(code));return;}
   if(!SameDemo()||ArraySize(result)<1||ArraySize(result)>640){Show("REJECT_ACCOUNT_PACKET");return;}
   int n=ArraySize(result);uchar bytes[];ArrayResize(bytes,n);
   for(int i=0;i<n;i++){bytes[i]=(uchar)result[i];if(bytes[i]<33||bytes[i]>126){Show("REJECT_ENCODING");return;}}
   Signal s;if(!Parse(CharArrayToString(bytes,0,n,CP_UTF8),s)){Show("REJECT_PROTOCOL_EXPIRED_OR_BAR");return;}
   Process(s);
  }
void OnDeinit(const int reason)
  {EventKillTimer();if(g_file!=INVALID_HANDLE)FileClose(g_file);Comment("");Print("NOG_CASH STOPPED | existing positions NOT closed.");}
