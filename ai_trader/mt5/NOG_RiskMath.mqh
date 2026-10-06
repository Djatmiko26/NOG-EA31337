#ifndef NOG_RISK_MATH_V1
#define NOG_RISK_MATH_V1
// Pure arithmetic. Shared by the EA and the offline C++ test harness.
// No account/broker/API access. Positive results are estimates, not loss guarantees.
bool NOGPositive(const double x) { return MathIsValidNumber(x) && x>0; }

double NOGBudget(const double equity,const double balance,const double capital_cap)
  {
   if(!NOGPositive(equity)||!NOGPositive(balance)||!NOGPositive(capital_cap)) return 0;
   return MathMin(capital_cap,MathMin(equity,balance))*0.0025;
  }

bool NOGStops(const bool buy,const double bid,const double ask,const double atr,
              const double tick,const double minimum_distance,double &sl,double &tp)
  {
   sl=0; tp=0;
   if(!NOGPositive(bid)||!NOGPositive(ask)||ask<bid||!NOGPositive(atr)||
      !NOGPositive(tick)||!MathIsValidNumber(minimum_distance)||minimum_distance<0) return false;
   double entry=buy?ask:bid;
   double distance=1.5*atr; // Fixed engineering experiment, not an optimized strategy.
   if(buy)
     {
      sl=MathFloor(MathMin(entry-distance,bid-minimum_distance-tick)/tick)*tick;
      tp=MathCeil((entry+2.0*(entry-sl))/tick)*tick;
     }
   else
     {
      sl=MathCeil(MathMax(entry+distance,ask+minimum_distance+tick)/tick)*tick;
      tp=MathFloor((entry-2.0*(sl-entry))/tick)*tick;
     }
   if(!NOGPositive(sl)||!NOGPositive(tp)) return false;
   return buy?(sl<bid && tp>ask):(sl>ask && tp<bid);
  }

// loss_at_min comes from broker OrderCalcProfit at stressed entry and stop prices.
// Commission is account-currency ROUND TRIP per 1.0 lot and must be explicitly known.
double NOGLot(const double budget,const double loss_at_min,const double volume_min,
              const double volume_step,const double volume_max,const double commission,
              double &planned_risk)
  {
   planned_risk=0;
   if(!NOGPositive(budget)||!NOGPositive(loss_at_min)||!NOGPositive(volume_min)||
      !NOGPositive(volume_step)||!NOGPositive(volume_max)||volume_max<volume_min||
      !MathIsValidNumber(commission)||commission<0) return 0;
   double per_lot=loss_at_min/volume_min+commission;
   double allocation=budget*0.8; // 20% unallocated cushion; not insurance against gaps.
   double raw=MathMin(volume_max,allocation/per_lot);
   if(raw<volume_min) return 0; // NEVER round UP to broker minimum.
   double lot=volume_min+MathFloor((raw-volume_min)/volume_step+1e-10)*volume_step;
   if(lot>raw+1e-10) lot-=volume_step;
   double risk=lot*per_lot;
   if(lot<volume_min-1e-10||lot>volume_max+1e-10||risk>allocation+1e-9) return 0;
   planned_risk=risk;
   return lot;
  }

bool NOGDailyAllows(const double initial_equity,const double initial_balance,
                   const double equity,const double balance,const double capital,
                   const double new_risk,const int submissions)
  {
   if(!NOGPositive(initial_equity)||!NOGPositive(initial_balance)||!NOGPositive(equity)||
      !NOGPositive(balance)||!NOGPositive(capital)||!MathIsValidNumber(new_risk)||new_risk<0) return false;
   double loss=MathMax(0,MathMax(initial_equity-equity,initial_balance-balance));
   return submissions<2 && loss<capital*0.01 && loss+new_risk<=capital*0.01;
  }

bool NOGRiskSelfTest()
  {
   double risk=0,sl=0,tp=0;
   if(MathAbs(NOGBudget(100,100,100)-0.25)>1e-9) return false;
   if(NOGBudget(0,100,100)!=0) return false;
   if(NOGLot(0.25,1,0.01,0.01,10,0,risk)!=0) return false;
   double lot=NOGLot(10,1,0.01,0.01,10,0,risk);
   if(MathAbs(lot-0.08)>1e-9 || MathAbs(risk-8)>1e-9) return false;
   if(!NOGStops(true,100,100.2,1,0.1,0.3,sl,tp)||sl>=100||tp<=100.2) return false;
   if(!NOGStops(false,100,100.2,1,0.1,0.3,sl,tp)||sl<=100.2||tp>=100) return false;
   if(NOGDailyAllows(100,100,99,99,100,0,0)) return false;
   if(NOGDailyAllows(100,100,100,100,100,0.1,2)) return false;
   return true;
  }
#endif
