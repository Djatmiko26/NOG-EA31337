#property strict
#property version "1.00"
#property description "DEMO ONLY. Default preview. Never attach to a real account."
#include "NOG_RiskMath.mqh"

input bool InpEnableDemoOrders=false; // PLUS --demo-orders on Python; never real accounts
input bool InpPauseNewEntries=false;
input double InpCapitalCap=100.0; // ACCOUNT CURRENCY, not always USD
input double InpRoundTripCommissionPerLot=-1.0; // -1 UNKNOWN: blocks sending
const ulong MAGIC=2609182501;
const string URL="http://127.0.0.1:8765/signal";
const string DIR="NOG_GuardedDemo";
const int DEVIATION_POINTS=20; // stress assumption; market fills can exceed it
long g_login=0,g_last_bar=0,g_day=0,g_anchor_server=0;
string g_server="",g_token="",g_status="",g_state_spec="";
int g_file=INVALID_HANDLE,g_daily=0,g_total=0,g_halt=0,g_seq=0;
double g_initial_equity=0,g_initial_balance=0,g_capital=0;
long g_tick=0,g_started=0;
ulong g_tick_seen=0,g_last_poll=0;
long g_last_wall=0;

struct Signal {string id; string mode; string action; long issued; long expires;
               long bar; long login; double atr; double bid; double ask;};
string I(const long x) {return IntegerToString(x);}
uint Hash32(const string s) {uint h=2166136261; for(int i=0;i<StringLen(s);i++) h=(h^(uint)StringGetCharacter(s,i))*16777619; return h;}
bool IntegerText(const string s,const int maxlen=12)
  {if(StringLen(s)<1||StringLen(s)>maxlen) return false;
   for(int i=0;i<StringLen(s);i++) if(StringGetCharacter(s,i)<'0'||StringGetCharacter(s,i)>'9') return false; return true;}
bool DecimalText(const string s)
  {if(StringLen(s)<1||StringLen(s)>32) return false; int dots=0,digits=0;
   for(int i=0;i<StringLen(s);i++){ushort c=StringGetCharacter(s,i); if(c=='.') dots++; else if(c>='0'&&c<='9') digits++; else return false;}
   return dots<=1&&digits>0&&MathIsValidNumber(StringToDouble(s));}
bool HexText(const string s,const int n)
  {if(StringLen(s)!=n) return false; for(int i=0;i<n;i++){ushort c=StringGetCharacter(s,i); if(!((c>='0'&&c<='9')||(c>='a'&&c<='f'))) return false;} return true;}
void Show(const string s)
  {Comment("NOG GUARDED DEMO | ",InpEnableDemoOrders?"DEMO ARMED":"PREVIEW ONLY","\n",_Symbol," M5 | planned risk 0.25% | capital cap ",DoubleToString(InpCapitalCap,2)," ",AccountInfoString(ACCOUNT_CURRENCY),"\n",s);
   if(s!=g_status){Print("NOG_GUARDED | ",s); g_status=s;}}
bool SameDemo()
  {return AccountInfoInteger(ACCOUNT_TRADE_MODE)==ACCOUNT_TRADE_MODE_DEMO && AccountInfoInteger(ACCOUNT_LOGIN)==g_login && AccountInfoString(ACCOUNT_SERVER)==g_server && TerminalInfoInteger(TERMINAL_CONNECTED);}

bool Save()
  {
   if(g_file==INVALID_HANDLE) return false;
   string row="S1;"+g_state_spec+";"+I(++g_seq)+";"+I(g_day)+";"+I(g_anchor_server)+";"+
      DoubleToString(g_initial_equity,8)+";"+DoubleToString(g_initial_balance,8)+";"+DoubleToString(g_capital,8)+";"+
      I(g_daily)+";"+I(g_total)+";"+I(g_last_bar)+";"+I(g_halt);
   row+=";"+I((long)Hash32(row))+"\r\n";
   FileSeek(g_file,0,SEEK_END); ResetLastError();
   uint written=FileWriteString(g_file,row); FileFlush(g_file);
   return written>0&&GetLastError()==0;
  }
void Halt(const string why)
  {g_halt=1; Save(); Show("HALTED_REVIEW_REQUIRED | "+why);}

bool LoadState()
  {
   string name=DIR+"\\state_"+I(g_login)+"_"+I((long)Hash32(g_server))+".journal";
   bool existed=FileIsExist(name);
   // No FILE_SHARE flags: one receiver per account in this terminal, even on other charts.
   g_file=FileOpen(name,FILE_READ|FILE_WRITE|FILE_TXT|FILE_ANSI,0,CP_UTF8);
   if(g_file==INVALID_HANDLE) {Show("STATE_LOCKED_OR_UNREADABLE | one EA only"); return false;}
   g_state_spec=I(g_login)+":"+I((long)Hash32(g_server+"|"+DoubleToString(InpCapitalCap,8)+"|"+DoubleToString(InpRoundTripCommissionPerLot,8)));
   if(FileSize(g_file)==0)
     {
      if(existed) {Show("EMPTY_STATE_REVIEW_REQUIRED"); return false;}
      // Missing state must not erase a previous order budget for this fixed magic.
      if(!HistorySelect(0,TimeTradeServer())) return false;
      for(int h=0;h<HistoryOrdersTotal();h++)
         if(HistoryOrderGetInteger(HistoryOrderGetTicket(h),ORDER_MAGIC)==(long)MAGIC) return false;
      g_day=(long)TimeGMT()/86400; g_anchor_server=(long)TimeTradeServer();
      g_initial_equity=AccountInfoDouble(ACCOUNT_EQUITY); g_initial_balance=AccountInfoDouble(ACCOUNT_BALANCE);
      g_capital=MathMin(InpCapitalCap,MathMin(g_initial_balance,g_initial_equity));
      return NOGPositive(g_capital)&&Save();
     }
   string previous_spec="";
   FileSeek(g_file,0,SEEK_SET);
   while(!FileIsEnding(g_file))
     {
      string row=FileReadString(g_file); string f[];
      if(StringSplit(row,';',f)!=13 || f[0]!="S1") return false;
      if(g_total>0 && previous_spec!="" && f[1]!=previous_spec) return false;
      previous_spec=f[1];
      string base=f[0]; for(int k=1;k<12;k++) base+=";"+f[k];
      if(f[12]!=I((long)Hash32(base))) return false;
      for(int k=2;k<13;k++) if(!DecimalText(f[k])) return false;
      for(int k=2;k<13;k++) if((k<5||k>7)&&!IntegerText(f[k])) return false;
      int seq=(int)StringToInteger(f[2]);
      long day=StringToInteger(f[3]),bar=StringToInteger(f[10]);
      int daily=(int)StringToInteger(f[8]),total=(int)StringToInteger(f[9]),halt=(int)StringToInteger(f[11]);
      if(seq!=g_seq+1||day<g_day||bar<g_last_bar||total<g_total||total>2||daily<0||daily>2||halt<0||halt>1) return false;
      g_seq=seq; g_day=day; g_anchor_server=StringToInteger(f[4]);
      g_initial_equity=StringToDouble(f[5]); g_initial_balance=StringToDouble(f[6]); g_capital=StringToDouble(f[7]);
      g_daily=daily; g_total=total; g_last_bar=bar; g_halt=halt;
      if(!NOGPositive(g_initial_equity)||!NOGPositive(g_initial_balance)||!NOGPositive(g_capital)||g_seq>20000) return false;
     }
   if(g_seq==0) return false;
   if(previous_spec!=g_state_spec)
     {
      if(g_total>0) {Show("SETTINGS_FROZEN_AFTER_FIRST_SUBMISSION"); return false;}
      // Fee/capital setup may be corrected before ANY order attempt, without losing state.
      g_capital=MathMin(InpCapitalCap,MathMin(g_initial_equity,g_initial_balance));
      return Save();
     }
   return true;
  }

bool RefreshDay()
  {
   long day=(long)TimeGMT()/86400;
   if(day<g_day) {Halt("UTC_CLOCK_MOVED_BACK"); return false;}
   if(day>g_day)
     {
      if(PositionsTotal()>0||OrdersTotal()>0) return false; // no reset around carried positions
      g_day=day; g_anchor_server=(long)TimeTradeServer(); g_daily=0;
      g_initial_equity=AccountInfoDouble(ACCOUNT_EQUITY); g_initial_balance=AccountInfoDouble(ACCOUNT_BALANCE);
      g_capital=MathMin(InpCapitalCap,MathMin(g_initial_balance,g_initial_equity));
      if(!Save()) {g_halt=1; return false;}
     }
   // Funding changes invalidate the simple equity baseline. Dedicated demo only.
   if(!HistorySelect((datetime)g_anchor_server,TimeTradeServer())) return false;
   for(int i=0;i<HistoryDealsTotal();i++)
     {
      ulong ticket=HistoryDealGetTicket(i);
      long type=HistoryDealGetInteger(ticket,DEAL_TYPE);
      if(HistoryDealGetInteger(ticket,DEAL_TIME)>g_anchor_server &&
         (type==DEAL_TYPE_BALANCE||type==DEAL_TYPE_CREDIT||type==DEAL_TYPE_CORRECTION||type==DEAL_TYPE_BONUS))
        {Halt("CASH_CHANGE_REVIEW_REQUIRED"); return false;}
     }
   return true;
  }

bool Parse(const string text,Signal &s)
  {
   string f[];
   if(StringLen(text)>640||StringSplit(text,';',f)!=13||f[0]!="NOG_GUARDED_V1"||
      (f[1]!="PREVIEW"&&f[1]!="DEMO_SEND")||!HexText(f[2],32)||f[3]!=_Symbol||f[4]!="M5") return false;
   if(f[5]!="BUY"&&f[5]!="SELL"&&f[5]!="WAIT") return false;
   for(int i=6;i<=9;i++) if(!IntegerText(f[i])) return false;
   for(int i=10;i<=12;i++) if(!DecimalText(f[i])) return false;
   s.mode=f[1]; s.id=f[2]; s.action=f[5]; s.issued=StringToInteger(f[6]); s.expires=StringToInteger(f[7]);
   s.bar=StringToInteger(f[8]); s.login=StringToInteger(f[9]);
   s.atr=StringToDouble(f[10]); s.bid=StringToDouble(f[11]); s.ask=StringToDouble(f[12]);
   long now=(long)TimeGMT();
   return s.login==g_login && s.issued>=g_started && s.issued<=now+3 && now<s.expires &&
          s.expires>s.issued && s.expires-s.issued<=30 && s.bar==(long)iTime(_Symbol,PERIOD_M5,1) &&
          NOGPositive(s.atr)&&NOGPositive(s.bid)&&NOGPositive(s.ask)&&s.ask>=s.bid;
  }

bool FreshQuote(MqlTick &tick)
  {
   if(!SymbolInfoTick(_Symbol,tick)||tick.bid<=0||tick.ask<tick.bid||tick.time_msc<=0) return false;
   ulong ms=GetTickCount64();
   if(g_tick>0&&tick.time_msc<g_tick) {Halt("TICK_CLOCK_MOVED_BACK"); return false;}
   if(tick.time_msc>g_tick){g_tick=tick.time_msc; g_tick_seen=ms;}
   return ms-g_tick_seen<=10000 && tick.time_msc/1000>=(long)iTime(_Symbol,PERIOD_M5,0) &&
          tick.time_msc/1000<(long)iTime(_Symbol,PERIOD_M5,0)+300;
  }

bool Plan(Signal &s,MqlTick &tick,MqlTradeRequest &r,double &budget,double &planned,string &why)
  {
   budget=NOGBudget(AccountInfoDouble(ACCOUNT_EQUITY),AccountInfoDouble(ACCOUNT_BALANCE),InpCapitalCap);
   if(!FreshQuote(tick)) {why="QUOTE_NOT_FRESH"; return false;}
   if((tick.ask-tick.bid)/s.atr>0.12) {why="SPREAD_ATR"; return false;}
   if(MathAbs((tick.bid+tick.ask-s.bid-s.ask)/2)>0.25*s.atr) {why="PRICE_DRIFT"; return false;}
   double point=SymbolInfoDouble(_Symbol,SYMBOL_POINT),size=SymbolInfoDouble(_Symbol,SYMBOL_TRADE_TICK_SIZE);
   double minv=SymbolInfoDouble(_Symbol,SYMBOL_VOLUME_MIN),step=SymbolInfoDouble(_Symbol,SYMBOL_VOLUME_STEP);
   double maxv=SymbolInfoDouble(_Symbol,SYMBOL_VOLUME_MAX),limit=SymbolInfoDouble(_Symbol,SYMBOL_VOLUME_LIMIT);
   if(limit>0) maxv=MathMin(maxv,limit);
   double stops=(double)SymbolInfoInteger(_Symbol,SYMBOL_TRADE_STOPS_LEVEL)*point;
   bool buy=s.action=="BUY"; double sl,tp;
   if(!NOGStops(buy,tick.bid,tick.ask,s.atr,size,stops,sl,tp)) {why="STOP_GEOMETRY"; return false;}
   double entry=buy?tick.ask:tick.bid,stress=DEVIATION_POINTS*point,loss=0;
   ENUM_ORDER_TYPE type=buy?ORDER_TYPE_BUY:ORDER_TYPE_SELL;
   if(!OrderCalcProfit(type,_Symbol,minv,entry+(buy?stress:-stress),sl+(buy?-stress:stress),loss)||loss>=0)
     {why="BROKER_RISK_CALC_FAILED"; return false;}
   double commission=InpRoundTripCommissionPerLot;
   if(commission<0){why="COMMISSION_UNKNOWN | set verified round-trip fee per lot"; return false;}
   double lots=NOGLot(budget,-loss,minv,step,maxv,commission,planned);
   if(lots<=0){why="MIN_LOT_EXCEEDS_BUDGET | budget="+DoubleToString(budget,4)+" | minimum_stressed_loss="+DoubleToString(-loss+minv*commission,4); return false;}
   double checkprofit=0;
   if(!OrderCalcProfit(type,_Symbol,lots,entry+(buy?stress:-stress),sl+(buy?-stress:stress),checkprofit)||
      checkprofit>=0||-checkprofit+lots*commission>budget*0.8+1e-8) {why="FINAL_RISK_CHECK"; return false;}
   planned=-checkprofit+lots*commission;
   if(!NOGDailyAllows(g_initial_equity,g_initial_balance,AccountInfoDouble(ACCOUNT_EQUITY),
                     AccountInfoDouble(ACCOUNT_BALANCE),g_capital,planned,g_daily)) {why="DAILY_LOSS_OR_SUBMISSION_LIMIT"; return false;}
   long mode=SymbolInfoInteger(_Symbol,SYMBOL_TRADE_MODE),allowed=SymbolInfoInteger(_Symbol,SYMBOL_ORDER_MODE);
   if((mode!=SYMBOL_TRADE_MODE_FULL && !(buy&&mode==SYMBOL_TRADE_MODE_LONGONLY) && !(!buy&&mode==SYMBOL_TRADE_MODE_SHORTONLY))||
      (allowed&SYMBOL_ORDER_MARKET)==0||(allowed&SYMBOL_ORDER_SL)==0||(allowed&SYMBOL_ORDER_TP)==0)
     {why="BROKER_ORDER_OR_STOPS_UNSUPPORTED"; return false;}
   // Only FOK: no partial-volume retry or RETURN pending remainder.
   if((SymbolInfoInteger(_Symbol,SYMBOL_FILLING_MODE)&SYMBOL_FILLING_FOK)==0) {why="FOK_NOT_SUPPORTED"; return false;}
   ZeroMemory(r); r.action=TRADE_ACTION_DEAL; r.symbol=_Symbol; r.magic=MAGIC; r.type=type;
   r.volume=NormalizeDouble(lots,8); r.price=entry; r.sl=sl; r.tp=tp;
   r.deviation=DEVIATION_POINTS; r.type_filling=ORDER_FILLING_FOK; r.comment="NOGG:"+StringSubstr(s.id,0,20);
   double margin=0;
   if(!OrderCalcMargin(type,_Symbol,r.volume,entry,margin)||!MathIsValidNumber(margin)||margin<0||
      margin>AccountInfoDouble(ACCOUNT_MARGIN_FREE)*0.5) {why="MARGIN_RESERVE"; return false;}
   return true;
  }

void Process(Signal &s)
  {
   if(s.bar<=g_last_bar) {Show("DUPLICATE_BAR_IGNORED"); return;}
   // Consume even WAIT/preview/rejected signal: never arm a cached message later.
   g_last_bar=s.bar;
   if(!Save()){g_halt=1; Show("STATE_WRITE_FAILED"); return;}
   if(s.action=="WAIT") {Show("AI_WAIT | NO_ORDER"); return;}
   if(PositionsTotal()>0||OrdersTotal()>0) {Show("REJECT_ACCOUNT_HAS_POSITION_OR_ORDER"); return;}
   MqlTradeRequest request={}; MqlTick tick; double budget=0,planned=0; string why;
   if(!Plan(s,tick,request,budget,planned,why)) {Show("RISK_REJECT | "+why); return;}
   Show("RISK_PLAN | "+s.action+" | lot="+DoubleToString(request.volume,8)+" | SL="+DoubleToString(request.sl,_Digits)+
        " | TP="+DoubleToString(request.tp,_Digits)+" | planned="+DoubleToString(planned,4)+" | budget="+DoubleToString(budget,4));
   if(s.mode!="DEMO_SEND"||!InpEnableDemoOrders) {Show("PREVIEW_ONLY | NO_ORDER"); return;}
   if(!MQLInfoInteger(MQL_TRADE_ALLOWED)||!TerminalInfoInteger(TERMINAL_TRADE_ALLOWED)||
      !AccountInfoInteger(ACCOUNT_TRADE_ALLOWED)||!AccountInfoInteger(ACCOUNT_TRADE_EXPERT)) {Show("REJECT_ALGO_PERMISSION"); return;}
   if(g_total>=2||g_halt>0) {Show("PILOT_ORDER_LIMIT_OR_HALT"); return;}
   MqlTradeCheckResult checked={};
   if(!OrderCheck(request,checked)||checked.retcode!=0) {Show("ORDER_CHECK_REJECT | code="+I(checked.retcode)); return;}
   MqlTick latest;
   if(!SameDemo()||!FreshQuote(latest)||PositionsTotal()>0||OrdersTotal()>0||InpPauseNewEntries||
      (long)TimeGMT()>=s.expires || s.bar!=(long)iTime(_Symbol,PERIOD_M5,1)||
      MathAbs(latest.ask-tick.ask)>SymbolInfoDouble(_Symbol,SYMBOL_TRADE_TICK_SIZE)/2||
      MathAbs(latest.bid-tick.bid)>SymbolInfoDouble(_Symbol,SYMBOL_TRADE_TICK_SIZE)/2)
     {Show("REJECT_CHANGED_BEFORE_SEND"); return;}
   if(!NOGDailyAllows(g_initial_equity,g_initial_balance,AccountInfoDouble(ACCOUNT_EQUITY),
      AccountInfoDouble(ACCOUNT_BALANCE),g_capital,planned,g_daily)) {Show("DAILY_LIMIT_BEFORE_SEND"); return;}
   // Durable reservation, including an UNCERTAIN halt, before the ONLY OrderSend path.
   g_daily++; g_total++; g_halt=1;
   if(!Save()) {Show("RESERVATION_WRITE_FAILED | NO_ORDER"); return;}
   if(!SameDemo()||PositionsTotal()>0||OrdersTotal()>0||(long)TimeGMT()>=s.expires||
      s.bar!=(long)iTime(_Symbol,PERIOD_M5,1)) {Halt("CHANGED_AFTER_RESERVATION | NO_ORDER"); return;}
   MqlTradeResult result={}; bool sent=OrderSend(request,result);
   Print("NOG_GUARDED | ORDER_RESULT | sent=",sent," | retcode=",result.retcode," | deal=",result.deal," | order=",result.order);
   if(!sent||result.retcode!=TRADE_RETCODE_DONE||result.deal==0) {Halt("ORDER_UNCERTAIN_OR_REJECTED | do not resend"); return;}
   // Confirm protected position after the call. No automatic resubmit/averaging.
   if(!SameDemo()||!PositionSelect(_Symbol)||PositionGetInteger(POSITION_MAGIC)!=(long)MAGIC||
      PositionGetDouble(POSITION_SL)<=0||PositionGetDouble(POSITION_TP)<=0)
     {Halt("VERIFY_DEMO_POSITION_AND_STOPS_MANUALLY"); Alert("NOG DEMO: inspect position/protection; execution halted."); return;}
   double actual=0;
   if(!OrderCalcProfit(request.type,_Symbol,PositionGetDouble(POSITION_VOLUME),PositionGetDouble(POSITION_PRICE_OPEN),
      PositionGetDouble(POSITION_SL),actual)||actual>=0||-actual+request.volume*InpRoundTripCommissionPerLot>budget)
     {Halt("FILLED_RISK_EXCEEDS_ESTIMATE_REVIEW_POSITION"); Alert("NOG DEMO: inspect actual risk; execution halted."); return;}
   g_halt=0; if(!Save()) {g_halt=1; Show("POST_SEND_STATE_ERROR"); return;}
   Show("DEMO_POSITION_CONFIRMED | SL_TP_PRESENT | total="+I(g_total)+"/2");
  }

int OnInit()
  {
   if(MQLInfoInteger(MQL_TESTER)||AccountInfoInteger(ACCOUNT_TRADE_MODE)!=ACCOUNT_TRADE_MODE_DEMO||_Period!=PERIOD_M5)
     {Print("NOG: DEMO M5 chart only, not Tester."); return INIT_FAILED;}
   if(!NOGPositive(InpCapitalCap)||!MathIsValidNumber(InpRoundTripCommissionPerLot)||
      InpRoundTripCommissionPerLot< -1||!NOGRiskSelfTest()) return INIT_FAILED;
   g_login=AccountInfoInteger(ACCOUNT_LOGIN); g_server=AccountInfoString(ACCOUNT_SERVER); g_started=(long)TimeGMT();
   if(StringFind(g_server,";")>=0||StringFind(g_server,"\r")>=0||StringFind(g_server,"\n")>=0) return INIT_FAILED;
   int key=FileOpen(DIR+"\\bridge.token",FILE_READ|FILE_TXT|FILE_ANSI,0,CP_UTF8);
   if(key==INVALID_HANDLE){Print("NOG: run guarded_demo.py --install first."); return INIT_FAILED;}
   g_token=FileReadString(key); FileClose(key); if(!HexText(g_token,64)) return INIT_FAILED;
   if(!SameDemo()||!LoadState()){Print("NOG STATE STOP: preserve journal, review settings/other chart."); return INIT_FAILED;}
   MqlTick tick; SymbolInfoTick(_Symbol,tick); g_tick=tick.time_msc; g_tick_seen=GetTickCount64();
   if(!EventSetTimer(2)) return INIT_FAILED;
   Print("NOG_GUARDED | RISK_SELF_TEST_PASS | mode=",InpEnableDemoOrders?"DEMO_ENABLED":"PREVIEW");
   Show("STARTED | local risk engine | default NO_ORDER"); return INIT_SUCCEEDED;
  }
void OnTimer()
  {
   if(!SameDemo()) {Show("REJECT_ACCOUNT_OR_CONNECTION"); return;}
   if(PositionSelect(_Symbol)&&PositionGetInteger(POSITION_MAGIC)==(long)MAGIC &&
      (PositionGetDouble(POSITION_SL)<=0||PositionGetDouble(POSITION_TP)<=0))
     {if(g_halt==0) {Halt("MISSING_PROTECTION"); Alert("NOG DEMO: inspect missing SL/TP.");} return;}
   if(InpPauseNewEntries||g_halt>0||g_total>=2) {Show("PAUSED_OR_HALTED_OR_PILOT_CAP | inspect open positions"); return;}
   if(!RefreshDay()) {Show("DAY_OR_HISTORY_REVIEW_REQUIRED"); return;}
   if(!NOGDailyAllows(g_initial_equity,g_initial_balance,AccountInfoDouble(ACCOUNT_EQUITY),AccountInfoDouble(ACCOUNT_BALANCE),g_capital,0,g_daily))
     {Show("DAILY_LOSS_LOCK | no new entries"); return;}
   MqlTick tick; if(!FreshQuote(tick)){Show("WAIT_FRESH_QUOTE"); return;}
   ulong mono=GetTickCount64(); long wall=(long)TimeGMT();
   if(g_last_poll>0 && MathAbs((double)(wall-g_last_wall)-(double)(mono-g_last_poll)/1000)>3)
     {g_last_poll=mono; g_last_wall=wall; Halt("CLOCK_JUMP"); return;}
   g_last_poll=mono; g_last_wall=wall;
   if(PositionsTotal()>0||OrdersTotal()>0)
     {
      if(PositionSelect(_Symbol)&&PositionGetInteger(POSITION_MAGIC)==(long)MAGIC &&
        (PositionGetDouble(POSITION_SL)<=0||PositionGetDouble(POSITION_TP)<=0)) {Halt("MISSING_PROTECTION"); Alert("NOG DEMO: inspect missing SL/TP.");}
      Show("ACCOUNT_BUSY | no new entries"); return;
     }
   string headers="X-NOG-Receiver: GUARDED_DEMO_V1\r\nX-NOG-Token: "+g_token+"\r\nX-NOG-Account: "+I(g_login)+
      "\r\nX-NOG-Server: "+g_server+"\r\nX-NOG-Symbol: "+_Symbol+"\r\nX-NOG-Timeframe: M5\r\n";
   char data[],result[]; string response_headers;
   ResetLastError(); int code=WebRequest("GET",URL,headers,1000,data,result,response_headers);
   if(code==-1){Show("API_UNAVAILABLE | error="+I(GetLastError())); return;}
   if(code==204){Show("NO_SIGNAL"); return;}
   if(code!=200){Show("REJECT_HTTP | code="+I(code)); return;}
   if(!SameDemo()||ArraySize(result)<1||ArraySize(result)>640) {Show("REJECT_ACCOUNT_OR_PACKET_SIZE"); return;}
   uchar bytes[]; int n=ArraySize(result); ArrayResize(bytes,n);
   for(int i=0;i<n;i++){bytes[i]=(uchar)result[i]; if(bytes[i]<33||bytes[i]>126){Show("REJECT_PACKET_ENCODING"); return;}}
   Signal s;
   if(!Parse(CharArrayToString(bytes,0,n,CP_UTF8),s)) {Show("REJECT_PROTOCOL_OR_EXPIRED_OR_BAR"); return;}
   Process(s);
  }
void OnDeinit(const int reason)
  {EventKillTimer(); if(g_file!=INVALID_HANDLE) FileClose(g_file); Comment(""); Print("NOG_GUARDED STOPPED | existing positions NOT closed; inspect Trade tab.");}
