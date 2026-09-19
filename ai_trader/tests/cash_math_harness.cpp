#include <cmath>
#include <algorithm>
#include <iostream>
#include <iomanip>
#include <limits>
#include <cassert>
#define MathIsValidNumber std::isfinite
#define MathAbs std::abs
#define MathMin std::min
#define MathMax std::max
#define MathFloor std::floor
#define MathCeil std::ceil
// Allow int/double arguments just as MQL MathMin/Max do.
#undef MathMin
#undef MathMax
inline double MathMin(double a,double b){return std::min(a,b);}
inline double MathMax(double a,double b){return std::max(a,b);}
#include "../mt5/NOG_CashMath.mqh"
int main(){
 assert(CashSelfTest());
 assert(!CashDaily(100,100,100,100,10,1,1));
 assert(!CashDaily(100,100,100,100,10,0,1));
 assert(!CashDaily(100,100,99,99,10,0,0));
 assert(!CashDaily(100,100,111,110,10,0,0));
 assert(!CashDaily(100,100,100,100,std::numeric_limits<double>::quiet_NaN(),0,0));
 CashPlan out;
 assert(!CashMath(true,NAN,100.2,1,.01,.1,.02,.01,.01,10,100,100,0,0,out));
 int buy;double bid,ask,atr,tick,stop,stress,mn,step,mx,loss,gain,commission,fixed;
 std::cout<<std::setprecision(17);
 while(std::cin>>buy>>bid>>ask>>atr>>tick>>stop>>stress>>mn>>step>>mx>>loss>>gain>>commission>>fixed){
   bool ok=CashMath(buy!=0,bid,ask,atr,tick,stop,stress,mn,step,mx,loss,gain,commission,fixed,out);
   std::cout<<ok;
   if(ok)std::cout<<" "<<out.volume<<" "<<out.entry<<" "<<out.sl<<" "<<out.tp<<" "<<out.loss<<" "<<out.profit<<" "<<out.fee<<" "<<out.technical;
   std::cout<<"\n";
 }
}
