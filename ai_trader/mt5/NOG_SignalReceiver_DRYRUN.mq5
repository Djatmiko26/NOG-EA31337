// Communication harness only. No trade library, no execution switch.
#property strict
#property version "1.00"
#property description "TEST_ONLY: local API receiver; never opens or modifies positions."

const string DEMO_URL="http://127.0.0.1:8765/signal";
const int POLL_SECONDS=2;
const int TIMEOUT_MS=1000;
const int MAX_SEEN=256;
string g_seen[];
string g_last_status="";

struct TestSignal
  {
   string id;
   string action;
   long issued;
   long expires;
  };

bool Digits10(const string value)
  {
   if(StringLen(value)!=10 || StringGetCharacter(value,0)=='0') return false;
   for(int i=0;i<10;i++)
     {
      ushort c=StringGetCharacter(value,i);
      if(c<'0' || c>'9') return false;
     }
   return true;
  }

bool HexId(const string value)
  {
   if(StringLen(value)!=32) return false;
   for(int i=0;i<32;i++)
     {
      ushort c=StringGetCharacter(value,i);
      if(!((c>='0' && c<='9') || (c>='a' && c<='f'))) return false;
     }
   return true;
  }

// Pure parser/checks: it never uses broker prices or places an order.
bool ValidatePacket(const string body,const string symbol,const long now,
                    TestSignal &signal,string &reason)
  {
   string f[];
   if(StringLen(body)>512 || StringSplit(body,';',f)!=8)
     { reason="REJECT_FORMAT"; return false; }
   if(f[0]!="NOG_DEMO_V1" || f[1]!="TEST_ONLY")
     { reason="REJECT_PROTOCOL"; return false; }
   if(!HexId(f[2])) { reason="REJECT_ID"; return false; }
   if(f[3]!=symbol || f[4]!="M5")
     { reason="REJECT_SYMBOL_TIMEFRAME"; return false; }
   if(f[5]!="BUY" && f[5]!="SELL" && f[5]!="WAIT")
     { reason="REJECT_ACTION"; return false; }
   if(!Digits10(f[6]) || !Digits10(f[7]))
     { reason="REJECT_TIME_FORMAT"; return false; }
   long issued=StringToInteger(f[6]),expires=StringToInteger(f[7]);
   if(expires<=issued || expires-issued>60)
     { reason="REJECT_TTL"; return false; }
   if(issued>now+3) { reason="REJECT_FUTURE_CLOCK"; return false; }
   if(now>=expires) { reason="REJECT_EXPIRED"; return false; }
   signal.id=f[2]; signal.action=f[5]; signal.issued=issued; signal.expires=expires;
   reason="VALID";
   return true;
  }

bool WasSeen(const string id)
  {
   for(int i=0;i<ArraySize(g_seen);i++)
      if(g_seen[i]==id) return true;
   return false;
  }

void ShowStatus(const string status)
  {
   Comment("NOG SIGNAL RECEIVER - DRY RUN\n",
           "TEST SIGNALS ONLY | NO ORDER EXECUTION\n",
           "Chart: ",_Symbol," M5\n",status);
   if(status!=g_last_status)
     {
      Print("NOG_DRYRUN | ",status," | NO_ORDER_EXECUTION");
      g_last_status=status;
     }
  }

// These execute inside MT5 at attachment, not in the Python test suite.
bool ParserSelfTest()
  {
   string base="NOG_DEMO_V1;TEST_ONLY;0123456789abcdef0123456789abcdef;XAUUSD;M5;";
   TestSignal s; string why;
   if(!ValidatePacket(base+"BUY;1700000000;1700000030","XAUUSD",1700000001,s,why)) return false;
   if(s.action!="BUY") return false;
   if(!ValidatePacket(base+"SELL;1700000000;1700000030","XAUUSD",1700000001,s,why)) return false;
   if(!ValidatePacket(base+"WAIT;1700000000;1700000030","XAUUSD",1700000001,s,why)) return false;
   if(ValidatePacket(base+"BUY;1700000000;1700000030","XAUUSD",1700000030,s,why) || why!="REJECT_EXPIRED") return false;
   if(ValidatePacket(base+"BUY;1700000100;1700000130","XAUUSD",1700000001,s,why) || why!="REJECT_FUTURE_CLOCK") return false;
   if(ValidatePacket(base+"BUY;1700000000;1700000100","XAUUSD",1700000001,s,why) || why!="REJECT_TTL") return false;
   if(ValidatePacket(base+"BUY;1700000000;1700000030","OTHER",1700000001,s,why) || why!="REJECT_SYMBOL_TIMEFRAME") return false;
   if(ValidatePacket(base+"TRADE;1700000000;1700000030","XAUUSD",1700000001,s,why) || why!="REJECT_ACTION") return false;
   if(ValidatePacket("{}","XAUUSD",1700000001,s,why) || why!="REJECT_FORMAT") return false;
   return true;
  }

int OnInit()
  {
   // Account gate is defense-in-depth; this file has no order-execution path.
   if(MQLInfoInteger(MQL_TESTER))
     { Print("NOG_DRYRUN: use a demo chart, not Strategy Tester."); return INIT_FAILED; }
   if(AccountInfoInteger(ACCOUNT_TRADE_MODE)!=ACCOUNT_TRADE_MODE_DEMO)
     { Print("NOG_DRYRUN: DEMO account required."); return INIT_FAILED; }
   if(_Period!=PERIOD_M5)
     { Print("NOG_DRYRUN: attach to an M5 chart."); return INIT_FAILED; }
   if(!ParserSelfTest())
     { Print("NOG_DRYRUN: SELF_TEST_FAILED."); return INIT_FAILED; }
   Print("NOG_DRYRUN | PARSER_SELF_TEST_PASS");
   ArrayResize(g_seen,0);
   if(!EventSetTimer(POLL_SECONDS)) return INIT_FAILED;
   ShowStatus("STARTED | awaiting local test API");
   return INIT_SUCCEEDED;
  }

void OnTimer()
  {
   if(AccountInfoInteger(ACCOUNT_TRADE_MODE)!=ACCOUNT_TRADE_MODE_DEMO)
     { ShowStatus("REJECT_NOT_DEMO"); return; }
   if(!TerminalInfoInteger(TERMINAL_CONNECTED))
     { ShowStatus("REJECT_TERMINAL_DISCONNECTED"); return; }
   char data[],result[];
   string response_headers;
   ResetLastError();
   int code=WebRequest("GET",DEMO_URL,"Accept: text/plain\r\n",TIMEOUT_MS,
                       data,result,response_headers);
   if(code==-1)
     { ShowStatus("API_UNAVAILABLE | error="+IntegerToString(GetLastError())+" | check server and allowed URL"); return; }
   if(code==204) { ShowStatus("NO_SIGNAL"); return; }
   if(code!=200) { ShowStatus("REJECT_HTTP_"+IntegerToString(code)); return; }
   int n=ArraySize(result);
   if(n<1 || n>512) { ShowStatus("REJECT_BODY_SIZE"); return; }
   uchar bytes[];
   ArrayResize(bytes,n);
   for(int i=0;i<n;i++)
     {
      bytes[i]=(uchar)result[i];
      if(bytes[i]<33 || bytes[i]>126) { ShowStatus("REJECT_NON_ASCII"); return; }
     }
   TestSignal signal; string reason;
   string body=CharArrayToString(bytes,0,n,CP_UTF8);
   // Wall-clock UTC from this PC, not guessed broker candle/server offsets.
   if(!ValidatePacket(body,_Symbol,(long)TimeGMT(),signal,reason))
     { ShowStatus(reason); return; }
   // Recheck after synchronous HTTP in case terminal/account state changed.
   if(AccountInfoInteger(ACCOUNT_TRADE_MODE)!=ACCOUNT_TRADE_MODE_DEMO ||
      !TerminalInfoInteger(TERMINAL_CONNECTED))
     { ShowStatus("REJECT_ACCOUNT_OR_CONNECTION_CHANGED"); return; }
   if(WasSeen(signal.id))
     { ShowStatus("DUPLICATE_IGNORED | "+signal.id); return; }
   int count=ArraySize(g_seen);
   if(count>=MAX_SEEN) { ShowStatus("REJECT_SESSION_LIMIT"); return; }
   if(ArrayResize(g_seen,count+1)!=count+1) { ShowStatus("REJECT_MEMORY"); return; }
   g_seen[count]=signal.id;
   ShowStatus("ACCEPTED_TEST "+signal.action+" | "+signal.id);
   // Deliberately ends here. There is NO broker order or risk approval.
  }

void OnDeinit(const int reason)
  {
   EventKillTimer();
   Comment("");
   Print("NOG_DRYRUN | STOPPED | NO_ORDER_EXECUTION");
  }
