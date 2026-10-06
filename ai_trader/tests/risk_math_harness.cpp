// Compiles the SAME pure .mqh functions, not a Python reimplementation.
#include <cmath>
#include <algorithm>
#include <cassert>
#include <iostream>
#include <limits>
inline bool MathIsValidNumber(double x){return std::isfinite(x);}
inline double MathMin(double x,double y){return std::min(x,y);}
inline double MathMax(double x,double y){return std::max(x,y);}
inline double MathAbs(double x){return std::abs(x);}
inline double MathFloor(double x){return std::floor(x);}
inline double MathCeil(double x){return std::ceil(x);}
#include "../mt5/NOG_RiskMath.mqh"
int main(){
 int checks=0; double risk=0,sl=0,tp=0;
 auto check=[&](bool value){++checks; if(!value){std::cerr<<"FAIL "<<checks<<"\n"; std::exit(1);}};
 check(NOGRiskSelfTest());
 check(NOGBudget(100000,100000,100)==.25);
 check(NOGBudget(90,100,100)==.225);
 check(NOGBudget(std::numeric_limits<double>::quiet_NaN(),100,100)==0);
 check(NOGLot(.25,4.5,.01,.01,100,0,risk)==0);
 check(NOGLot(.25,.1,.01,.01,100,-1,risk)==0);
 check(NOGLot(10,1,.01,.01,100,100,risk)>.0 && risk<=8.00000001);
 check(NOGStops(true,100,100.2,1,.1,.3,sl,tp)&&sl<100&&tp>100.2);
 check(NOGStops(false,100,100.2,1,.1,.3,sl,tp)&&sl>100.2&&tp<100);
 check(!NOGStops(true,100,99,1,.1,.3,sl,tp));
 check(!NOGDailyAllows(100,100,99.2,100,100,.3,0));
 check(NOGDailyAllows(100,100,99.2,100,100,.1,0));
 check(!NOGDailyAllows(100,100,100,100,100,.1,2));
 check(!NOGDailyAllows(100,100,100,100,100,std::numeric_limits<double>::infinity(),0));
 // Deterministic grid exercises lots below minimum, step boundaries and commissions.
 for(int i=1;i<=10000;i++){
  double budget=i*.001,minv=.01,step=.01,cost=(i%17)*.3;
  double lot=NOGLot(budget,.123,minv,step,5,cost,risk);
  check(lot==0 || (lot>=minv-1e-10 && lot<=5+1e-10 && risk<=budget*.8+1e-9));
  if(lot>0) check(std::abs((lot-minv)/step-std::round((lot-minv)/step))<1e-7);
 }
 std::cout<<"PASS | "<<checks<<" arithmetic assertions | actual NOG_RiskMath.mqh via C++ shim\n";
 std::cout<<"This does NOT compile the MQL5 EA or test broker execution.\n";
}
