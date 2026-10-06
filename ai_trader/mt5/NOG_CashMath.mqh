#ifndef NOG_CASH_MATH_V1
#define NOG_CASH_MATH_V1
// Shared with the C++ tests. USD targets; never a guarantee of actual fill/P&L.
struct CashPlan
  {double volume; double entry; double sl; double tp; double loss; double profit; double fee; double technical;};
bool CashPositive(const double x) {return MathIsValidNumber(x)&&x>0;}
bool CashNonnegative(const double x) {return MathIsValidNumber(x)&&x>=0;}
bool CashMath(const bool buy,const double bid,const double ask,const double atr,
              const double tick,const double stops,const double stress,const double minimum,
              const double step,const double maximum,const double loss_slope,const double gain_slope,
              const double commission,const double fixed_fee,CashPlan &p)
  {
   if(!CashPositive(bid)||!CashPositive(ask)||ask<bid||!CashPositive(atr)||!CashPositive(tick)||
      !CashPositive(minimum)||!CashPositive(step)||!CashPositive(maximum)||
      !CashPositive(loss_slope)||!CashPositive(gain_slope)||!CashNonnegative(stops)||
      !CashNonnegative(stress)||!CashNonnegative(commission)||!CashNonnegative(fixed_fee)) return false;
   double cap=MathMin(maximum,0.10);
   if(cap<minimum||fixed_fee>=10) return false;
   p.entry=buy?ask:bid;
   p.technical=buy?MathFloor(MathMin(p.entry-1.5*atr,bid-stops-tick)/tick)*tick:
                    MathCeil(MathMax(p.entry+1.5*atr,ask+stops+tick)/tick)*tick;
   if(!CashPositive(p.technical)) return false;
   double per_lot=loss_slope*(MathAbs(p.entry-p.technical)+2*stress)+commission;
   double raw=MathMin(cap,(10-fixed_fee)/per_lot);
   if(raw<minimum) return false;
   p.volume=minimum+MathFloor((raw-minimum)/step+1e-10)*step;
   if(p.volume>raw+1e-12) p.volume-=step;
   if(p.volume<minimum-1e-12) return false;
   p.fee=p.volume*commission+fixed_fee;
   double d=(10-p.fee)/(loss_slope*p.volume)-2*stress;
   p.sl=buy?MathCeil((p.entry-d)/tick)*tick:MathFloor((p.entry+d)/tick)*tick;
   double target=(10+p.fee)/(gain_slope*p.volume)+2*stress;
   p.tp=buy?MathCeil((p.entry+target)/tick)*tick:MathFloor((p.entry-target)/tick)*tick;
   if(!CashPositive(p.volume)||!CashPositive(p.sl)||!CashPositive(p.tp)) return false;
   double eps=tick*1e-6;
   if(buy && (p.sl>p.technical+eps||p.sl>=bid-stops||p.tp<=MathMax(ask,bid+stops))) return false;
   if(!buy && (p.sl<p.technical-eps||p.sl<=ask+stops||p.tp>=MathMin(bid,ask-stops))) return false;
   p.loss=p.volume*loss_slope*(MathAbs(p.entry-p.sl)+2*stress)+p.fee;
   p.profit=p.volume*gain_slope*(MathAbs(p.tp-p.entry)-2*stress)-p.fee;
   return p.loss>0 && p.loss<=10+1e-8 && p.profit>=10-1e-8 && p.profit<=10+gain_slope*p.volume*tick+1e-8;
  }

bool CashDaily(const double initial_eq,const double initial_bal,const double equity,
               const double balance,const double risk,const int daily,const int total)
  {
   if(!CashPositive(initial_eq)||!CashPositive(initial_bal)||!CashPositive(equity)||
      !CashPositive(balance)||!CashNonnegative(risk)||daily<0||total<0) return false;
   double loss=MathMax(0,MathMax(initial_eq-equity,initial_bal-balance));
   return equity>=100 && balance>=100 && daily<1 && total<1 && balance-initial_bal<10 &&
          loss<10 && loss+risk<=10+1e-8;
  }

bool CashSelfTest()
  {
   CashPlan p;
   if(!CashMath(true,100,100.2,1,.01,.1,.02,.01,.01,10,100,100,0,0,p)) return false;
   if(p.loss>10+1e-8||p.profit<10-1e-8) return false;
   if(!CashMath(false,100,100.2,1,.01,.1,.02,.01,.01,10,100,100,7,.1,p)) return false;
   if(CashMath(true,100,100.2,20,.01,.1,.02,.01,.01,10,100,100,0,0,p)) return false;
   if(CashDaily(100,100,100,100,10,1,1)||CashDaily(100,100,90,90,10,0,1)) return false;
   return CashDaily(100,100,100,100,10,0,0);
  }
#endif
