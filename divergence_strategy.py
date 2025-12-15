import pandas as pd
import logging
from datetime import datetime, timedelta
import os
import csv
import time
import json

class DivergenceStrategy:
    """
    Implements the Spot-Option Divergence Strategy.
    
    Logic:
    - Monitor Nifty 50 Spot and ATM Options (CE/PE).
    - ATM Strike Selection: Uses current spot price to determine ATM strike.
    - Signal:
        - PE Buy: Spot Green (Close > Open) AND PE Green (Close > Open).
        - CE Buy: Spot Red (Close < Open) AND CE Green (Close > Open).
    - Entry: High of the signal candle.
    - SL: Low of the signal candle - 0.25.
    - Target: 1:3 Risk:Reward.
    - Capital: Fixed ₹10,000 per trade (Paper Trading).
    """
    
    def __init__(self, fyers, log_file="divergence_trades.csv"):
        self.fyers = fyers
        self.log_file = log_file
        self.logger = logging.getLogger(f"{__name__}.DivergenceStrategy")
        
        # Strategy Parameters
        self.sl_buffer = 0.25
        self.capital = 10000
        
        # State
        self.current_ce_symbol = None
        self.current_pe_symbol = None
        self.current_atm_strike = None  # Track current ATM strike for rotation
        self.active_trades = []  # List of active trade dicts
        self.pending_signals = {} # {symbol: {'type': 'BUY', 'high': float, 'low': float, 'candle': dict, 'time': datetime}}
        self.trade_history = []  # All trades for current day
        self.daily_signals_detected = 0  # Count signals detected today
        
        # Capital Tracking
        self.running_capital = self.capital  # Start with base capital
        self.daily_pnl = 0.0
        
        # Candle Data (5-min)
        # Structure: {symbol: [candle1, candle2, ...]}
        self.candles = {} 
        
        # Initialize CSV log
        self._init_log_file()

    def _init_log_file(self):
        if not os.path.exists(self.log_file):
            with open(self.log_file, 'w', newline='') as f:
                writer = csv.writer(f)
                writer.writerow([
                    "Date", "Time", "Symbol", "Type", "Entry Price", "SL", "Target", 
                    "Status", "Exit Price", "Exit Time", "PnL", "Reason", "Strategy"
                ])

    def get_best_strikes(self, spot_price):
        """
        Gets ATM CE and PE strikes based on current spot price.
        Returns: (ce_symbol, pe_symbol)
        """
        try:
            # Get Option Chain for nearest expiry with strikecount=1 (ATM only)
            data = {"symbol": "NSE:NIFTY50-INDEX", "strikecount": 1, "timestamp": ""}
            response = self.fyers.optionchain(data=data)
            if response['code'] != 200:
                self.logger.error(f"Failed to get option chain: {response}")
                return None, None
            
            options = response['data']['optionsChain']
            
            # Find ATM CE and PE
            ce_option = next((o for o in options if o['option_type'] == 'CE'), None)
            pe_option = next((o for o in options if o['option_type'] == 'PE'), None)
            
            if ce_option and pe_option:
                # Store ATM strike for rotation detection
                self.current_atm_strike = ce_option.get('strike_price', 0)
                return ce_option['symbol'], pe_option['symbol']
            
            return None, None
            
        except Exception as e:
            self.logger.error(f"Error finding ATM strikes: {e}")
            return None, None

    def process_candle(self, symbol, candle):
        """
        Process a completed 5-minute candle.
        candle dict: {'time': datetime, 'open': float, 'high': float, 'low': float, 'close': float}
        """
        if symbol not in self.candles:
            self.candles[symbol] = []
        self.candles[symbol].append(candle)
        
        # Keep only last few candles to save memory
        if len(self.candles[symbol]) > 10:
            self.candles[symbol].pop(0)
            
        # Check for signals if we have data for Spot and the Option
        self._check_signals(candle['time'])

    def _check_signals(self, timestamp):
        """Check for divergence signals at the given timestamp."""
        spot_symbol = "NSE:NIFTY50-INDEX"
        
        # Ensure we have candles for this timestamp
        spot_candle = self._get_candle(spot_symbol, timestamp)
        if not spot_candle:
            return

        # Check PE Signal
        if self.current_pe_symbol:
            pe_candle = self._get_candle(self.current_pe_symbol, timestamp)
            if pe_candle:
                # Logic: Spot Green AND PE Green
                is_spot_green = spot_candle['close'] > spot_candle['open']
                is_pe_green = pe_candle['close'] > pe_candle['open']
                
                if is_spot_green and is_pe_green:
                    self.daily_signals_detected += 1
                    reason = f"DIVERGENCE: Spot GREEN (O:{spot_candle['open']:.2f} C:{spot_candle['close']:.2f}) + PE GREEN (O:{pe_candle['open']:.2f} C:{pe_candle['close']:.2f})"
                    self.logger.info(f"Signal Detected (Pending): PE Buy on {self.current_pe_symbol} at {timestamp}. High: {pe_candle['high']}, Low: {pe_candle['low']}")
                    self.logger.info(f"  Reason: {reason}")
                    self.pending_signals[self.current_pe_symbol] = {
                        'type': 'BUY',
                        'high': pe_candle['high'],
                        'low': pe_candle['low'],
                        'candle': pe_candle,
                        'time': timestamp,
                        'reason': reason
                    }

        # Check CE Signal
        if self.current_ce_symbol:
            ce_candle = self._get_candle(self.current_ce_symbol, timestamp)
            if ce_candle:
                # Logic: Spot Red AND CE Green
                is_spot_red = spot_candle['close'] < spot_candle['open']
                is_ce_green = ce_candle['close'] > ce_candle['open']
                
                if is_spot_red and is_ce_green:
                    self.daily_signals_detected += 1
                    reason = f"DIVERGENCE: Spot RED (O:{spot_candle['open']:.2f} C:{spot_candle['close']:.2f}) + CE GREEN (O:{ce_candle['open']:.2f} C:{ce_candle['close']:.2f})"
                    self.logger.info(f"Signal Detected (Pending): CE Buy on {self.current_ce_symbol} at {timestamp}. High: {ce_candle['high']}, Low: {ce_candle['low']}")
                    self.logger.info(f"  Reason: {reason}")
                    self.pending_signals[self.current_ce_symbol] = {
                        'type': 'BUY',
                        'high': ce_candle['high'],
                        'low': ce_candle['low'],
                        'candle': ce_candle,
                        'time': timestamp,
                        'reason': reason
                    }

    def _get_candle(self, symbol, timestamp):
        """Retrieve candle for a specific timestamp."""
        if symbol in self.candles:
            for c in reversed(self.candles[symbol]):
                if c['time'] == timestamp:
                    return c
        return None

    def _place_dummy_order(self, symbol, side, signal_candle, timestamp, entry_price=None, reason=""):
        """Execute a paper trade (Dual Journaling: 1:1 and 1:3)."""
        # Check for existing open trade for this symbol
        # We allow multiple trades for the same symbol IF they are different strategies
        # But here we are triggering BOTH strategies at once.
        # So we check if ANY trade is open for this symbol to avoid duplicate entry on the same signal
        if any(t['symbol'] == symbol and t['status'] == 'OPEN' for t in self.active_trades):
            self.logger.debug(f"Skipping trade for {symbol}: Trade already open.")
            return

        # If entry_price is not provided (legacy calls), use signal high. 
        # But with new logic, we pass LTP as entry_price.
        if entry_price is None:
            entry_price = signal_candle['high']
            
        sl_price = signal_candle['low'] - self.sl_buffer
        
        risk = entry_price - sl_price
        if risk <= 0:
            self.logger.warning(f"Invalid Risk for {symbol}: Entry {entry_price}, SL {sl_price}")
            return

        # Calculate Quantity
        # Capital = 10000. Qty = 10000 / Entry Price
        quantity = int(self.capital / entry_price)
        
        # Create Trade 1:1
        target_1_1 = entry_price + (risk * 1.0)
        trade_1_1 = {
            "symbol": symbol,
            "entry_time": timestamp,
            "entry_price": entry_price,
            "sl": sl_price,
            "target": target_1_1,
            "quantity": quantity,
            "status": "OPEN",
            "pnl": 0.0,
            "strategy": "1:1",
            "reason": reason
        }
        
        # Create Trade 1:3
        target_1_3 = entry_price + (risk * 3.0)
        trade_1_3 = {
            "symbol": symbol,
            "entry_time": timestamp,
            "entry_price": entry_price,
            "sl": sl_price,
            "target": target_1_3,
            "quantity": quantity,
            "status": "OPEN",
            "pnl": 0.0,
            "strategy": "1:3",
            "reason": reason
        }
        
        self.active_trades.append(trade_1_1)
        self.active_trades.append(trade_1_3)
        
        self.logger.info(f"Trades Taken: {symbol} at {entry_price} (SL: {sl_price}) | Targets: 1:1={target_1_1:.2f}, 1:3={target_1_3:.2f}")
        self._log_trade(trade_1_1, "ENTRY")
        self._log_trade(trade_1_3, "ENTRY")

    def update_ltp(self, symbol, ltp, timestamp):
        """
        Update strategy with latest LTP.
        Checks for SL/Target on active trades.
        Checks for Entry/Invalidation on pending signals.
        """
        # 1. Check Active Trades
        # Iterate over a copy to allow modification of the list during iteration (if needed, though we don't remove here)
        for trade in self.active_trades[:]:
            if trade['symbol'] == symbol and trade['status'] == 'OPEN':
                # Check SL
                if ltp <= trade['sl']:
                    self._close_trade(trade, ltp, timestamp, "SL")
                # Check Target
                elif ltp >= trade['target']:
                    self._close_trade(trade, ltp, timestamp, "TARGET")

        # 2. Check Pending Signals
        if symbol in self.pending_signals:
            signal = self.pending_signals[symbol]
            
            # Check Invalidation (Price breaks Low)
            if ltp < signal['low']:
                self.logger.info(f"Signal Invalidated for {symbol}: Price {ltp} broke low {signal['low']}")
                del self.pending_signals[symbol]
                return

            # Check Entry (Price breaks High)
            if ltp > signal['high']:
                self.logger.info(f"Signal Triggered for {symbol}: Price {ltp} broke high {signal['high']}")
                # Entry Price is the Breakout Level (Signal High)
                # But practically, we enter at LTP (which might be slightly higher due to slippage/gap)
                # For simulation, we can use signal['high'] or ltp. Using LTP mimics market order.
                # User requirement: "next candle need to breaks the 65 to trigger".
                # We'll pass the original signal candle to calculate SL based on THAT candle's low.
                self._place_dummy_order(symbol, signal['type'], signal['candle'], timestamp, entry_price=ltp, reason=signal.get('reason', ''))
                del self.pending_signals[symbol]
                return

    def _close_trade(self, trade, exit_price, timestamp, exit_reason):
        """Close an active paper trade."""
        trade['status'] = 'CLOSED'
        trade['exit_price'] = exit_price
        trade['exit_time'] = timestamp
        trade['exit_reason'] = exit_reason
        
        # Calculate PnL
        # Long only: (Exit - Entry) * Qty
        trade['pnl'] = (exit_price - trade['entry_price']) * trade['quantity']
        
        # Update daily PnL and running capital
        self.daily_pnl += trade['pnl']
        self.running_capital += trade['pnl']
        
        # Add to trade history
        self.trade_history.append(trade.copy())
        
        self.logger.info(f"Trade Closed ({trade['strategy']}): {trade['symbol']} PnL: ₹{trade['pnl']:.2f} | Daily PnL: ₹{self.daily_pnl:.2f} | Capital: ₹{self.running_capital:.2f}")
        self._log_trade(trade, "EXIT")

    def check_strike_rotation(self, spot_price):
        """
        Check if ATM strike has changed by querying option chain.
        Only checks periodically (throttled) to avoid API spam.
        Returns: (new_ce_symbol, new_pe_symbol) or (None, None) if no change.
        """
        import time
        
        # Throttle: Only check every 30 seconds
        current_time = time.time()
        if hasattr(self, '_last_rotation_check'):
            if current_time - self._last_rotation_check < 30:
                return None, None
        
        self._last_rotation_check = current_time
        
        # Fetch current ATM from option chain
        try:
            data = {"symbol": "NSE:NIFTY50-INDEX", "strikecount": 1, "timestamp": ""}
            response = self.fyers.optionchain(data=data)
            if response['code'] != 200:
                return None, None
            
            options = response['data']['optionsChain']
            ce_option = next((o for o in options if o['option_type'] == 'CE'), None)
            pe_option = next((o for o in options if o['option_type'] == 'PE'), None)
            
            if not ce_option or not pe_option:
                return None, None
            
            new_ce = ce_option['symbol']
            new_pe = pe_option['symbol']
            new_atm_strike = ce_option.get('strike_price', 0)
            
            # Check if ATM has changed
            if new_ce != self.current_ce_symbol or new_pe != self.current_pe_symbol:
                print()  # New line before log
                self.logger.info(f"ATM Strike Rotation: {self.current_atm_strike} -> {new_atm_strike}")
                self.logger.info(f"  CE: {self.current_ce_symbol} -> {new_ce}")
                self.logger.info(f"  PE: {self.current_pe_symbol} -> {new_pe}")
                
                self.current_atm_strike = new_atm_strike
                self.current_ce_symbol = new_ce
                self.current_pe_symbol = new_pe
                return new_ce, new_pe
            
            return None, None
            
        except Exception as e:
            self.logger.error(f"Error checking strike rotation: {e}")
            return None, None

    def _log_trade(self, trade, log_type):
        with open(self.log_file, 'a', newline='') as f:
            writer = csv.writer(f)
            writer.writerow([
                datetime.now().strftime("%Y-%m-%d"),
                trade.get('entry_time'),
                trade['symbol'],
                "BUY", # Strategy only buys options
                f"{trade['entry_price']:.2f}",
                f"{trade['sl']:.2f}",
                f"{trade['target']:.2f}",
                log_type,
                f"{trade.get('exit_price', 0):.2f}",
                trade.get('exit_time', ''),
                f"{trade.get('pnl', 0):.2f}",
                trade.get('reason', ''),  # Entry reason (divergence details)
                trade.get('strategy', 'N/A')
            ])
    
    def generate_daily_report(self):
        """
        Generate end-of-day summary report.
        Called at market close.
        """
        today = datetime.now().strftime("%Y-%m-%d")
        report_file = f"daily_report_{today}.json"
        
        # Count trades by outcome
        winning_trades = [t for t in self.trade_history if t.get('pnl', 0) > 0]
        losing_trades = [t for t in self.trade_history if t.get('pnl', 0) < 0]
        breakeven_trades = [t for t in self.trade_history if t.get('pnl', 0) == 0]
        
        # Separate by strategy
        trades_1_1 = [t for t in self.trade_history if t.get('strategy') == '1:1']
        trades_1_3 = [t for t in self.trade_history if t.get('strategy') == '1:3']
        
        report = {
            "date": today,
            "summary": {
                "total_signals_detected": self.daily_signals_detected,
                "total_trades_taken": len(self.trade_history),
                "winning_trades": len(winning_trades),
                "losing_trades": len(losing_trades),
                "breakeven_trades": len(breakeven_trades),
                "win_rate": f"{(len(winning_trades) / len(self.trade_history) * 100):.1f}%" if self.trade_history else "N/A",
                "daily_pnl": round(self.daily_pnl, 2),
                "running_capital": round(self.running_capital, 2),
                "initial_capital": self.capital
            },
            "strategy_breakdown": {
                "1:1": {
                    "trades": len(trades_1_1),
                    "pnl": round(sum(t.get('pnl', 0) for t in trades_1_1), 2)
                },
                "1:3": {
                    "trades": len(trades_1_3),
                    "pnl": round(sum(t.get('pnl', 0) for t in trades_1_3), 2)
                }
            },
            "options_monitored": {
                "ce_symbol": self.current_ce_symbol,
                "pe_symbol": self.current_pe_symbol
            },
            "trades": []
        }
        
        # Add trade details
        for trade in self.trade_history:
            report["trades"].append({
                "symbol": trade['symbol'],
                "entry_time": str(trade.get('entry_time', '')),
                "entry_price": trade['entry_price'],
                "exit_time": str(trade.get('exit_time', '')),
                "exit_price": trade.get('exit_price', 0),
                "sl": trade['sl'],
                "target": trade['target'],
                "strategy": trade.get('strategy', 'N/A'),
                "pnl": round(trade.get('pnl', 0), 2),
                "exit_reason": trade.get('exit_reason', ''),
                "entry_reason": trade.get('reason', '')
            })
        
        # If no trades taken
        if not self.trade_history:
            report["message"] = "NO TRADES TAKEN TODAY"
            if self.daily_signals_detected > 0:
                report["message"] += f" - {self.daily_signals_detected} signals detected but none triggered (price didn't break high)"
            else:
                report["message"] += " - No divergence signals detected"
        
        # Save report
        with open(report_file, 'w') as f:
            json.dump(report, f, indent=4)
        
        # Log summary
        self.logger.info("="*60)
        self.logger.info("DAILY DIVERGENCE STRATEGY REPORT")
        self.logger.info("="*60)
        self.logger.info(f"Date: {today}")
        self.logger.info(f"Signals Detected: {self.daily_signals_detected}")
        self.logger.info(f"Trades Taken: {len(self.trade_history)}")
        if self.trade_history:
            self.logger.info(f"Winning: {len(winning_trades)} | Losing: {len(losing_trades)}")
            self.logger.info(f"Daily PnL: ₹{self.daily_pnl:.2f}")
            self.logger.info(f"Running Capital: ₹{self.running_capital:.2f}")
        else:
            self.logger.info(report["message"])
        self.logger.info(f"Report saved to: {report_file}")
        self.logger.info("="*60)
        
        return report
    
    def reset_for_new_day(self):
        """
        Reset daily tracking variables for new trading day.
        Called at market open.
        """
        self.logger.info("Resetting Divergence Strategy for new trading day...")
        
        # Clear daily tracking
        self.trade_history = []
        self.daily_signals_detected = 0
        self.daily_pnl = 0.0
        
        # Clear pending signals (stale from yesterday)
        self.pending_signals = {}
        
        # Clear candle data
        self.candles = {}
        
        # Close any open trades (shouldn't happen if EOD was clean)
        for trade in self.active_trades:
            if trade['status'] == 'OPEN':
                self.logger.warning(f"Closing stale trade from previous day: {trade['symbol']}")
                trade['status'] = 'CLOSED'
                trade['exit_reason'] = 'EOD_CARRYOVER_CLOSE'
        self.active_trades = []
        
        self.logger.info(f"Strategy reset. Running capital: ₹{self.running_capital:.2f}")
    
    def save_capital_state(self):
        """Save running capital to file for persistence across restarts."""
        state = {
            "running_capital": self.running_capital,
            "last_updated": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }
        with open("divergence_capital.json", 'w') as f:
            json.dump(state, f, indent=4)
        self.logger.info(f"Capital state saved: ₹{self.running_capital:.2f}")
    
    def load_capital_state(self):
        """Load running capital from file."""
        try:
            if os.path.exists("divergence_capital.json"):
                with open("divergence_capital.json", 'r') as f:
                    state = json.load(f)
                self.running_capital = state.get("running_capital", self.capital)
                self.logger.info(f"Loaded capital state: ₹{self.running_capital:.2f} (Last updated: {state.get('last_updated', 'N/A')})")
            else:
                self.logger.info(f"No capital state file found. Starting with ₹{self.capital}")
        except Exception as e:
            self.logger.error(f"Error loading capital state: {e}. Using default capital.")
            self.running_capital = self.capital

