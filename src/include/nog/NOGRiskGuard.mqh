//+------------------------------------------------------------------+
//| NOG Risk Guard for EA31337 Libre                                 |
//| Initial V1 safety layer for MetaTrader 5                         |
//+------------------------------------------------------------------+
#ifndef NOG_RISK_GUARD_MQH
#define NOG_RISK_GUARD_MQH

#ifdef __MQL5__

class NOGRiskGuard {
 private:
  double _day_start_balance;
  int _day_key;

  int DayKey() {
    MqlDateTime dt;
    TimeToStruct(TimeCurrent(), dt);
    return dt.year * 10000 + dt.mon * 100 + dt.day;
  }

  void RefreshDay() {
    int key = DayKey();
    if (key != _day_key) {
      _day_key = key;
      _day_start_balance = AccountInfoDouble(ACCOUNT_BALANCE);
    }
  }

 public:
  NOGRiskGuard() {
    _day_key = 0;
    _day_start_balance = 0.0;
    RefreshDay();
  }

  double DailyLossPercent() {
    RefreshDay();
    if (_day_start_balance <= 0.0) return 0.0;
    double equity = AccountInfoDouble(ACCOUNT_EQUITY);
    double loss = _day_start_balance - equity;
    if (loss <= 0.0) return 0.0;
    return (loss / _day_start_balance) * 100.0;
  }

  double DrawdownPercent() {
    double balance = AccountInfoDouble(ACCOUNT_BALANCE);
    double equity = AccountInfoDouble(ACCOUNT_EQUITY);
    if (balance <= 0.0 || equity >= balance) return 0.0;
    return ((balance - equity) / balance) * 100.0;
  }

  double SpreadPoints(const string symbol) {
    MqlTick tick;
    if (!SymbolInfoTick(symbol, tick)) return DBL_MAX;
    double point = SymbolInfoDouble(symbol, SYMBOL_POINT);
    if (point <= 0.0) return DBL_MAX;
    return (tick.ask - tick.bid) / point;
  }

  bool CanTrade(const string symbol,
                const bool enabled,
                const bool emergency_stop,
                const double max_daily_loss_pct,
                const double max_drawdown_pct,
                const double max_spread_points) {
    if (!enabled) return true;
    if (emergency_stop) return false;
    if (!TerminalInfoInteger(TERMINAL_CONNECTED)) return false;
    if (!TerminalInfoInteger(TERMINAL_TRADE_ALLOWED)) return false;
    if (!MQLInfoInteger(MQL_TRADE_ALLOWED)) return false;
    if (max_daily_loss_pct > 0.0 && DailyLossPercent() >= max_daily_loss_pct) return false;
    if (max_drawdown_pct > 0.0 && DrawdownPercent() >= max_drawdown_pct) return false;
    if (max_spread_points > 0.0 && SpreadPoints(symbol) > max_spread_points) return false;
    return true;
  }
};

#endif  // __MQL5__
#endif  // NOG_RISK_GUARD_MQH
