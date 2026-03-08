"""
Live Forward Test: 1-Min Divergence Strategy with Trailing SL
=============================================================
Uses WebSocket tick data for real-time paper trading.

Strategy:
- Divergence Signal on 1-min candles:
  - PE Buy: Spot Green (Close > Open) AND PE Green (Close > Open)
  - CE Buy: Spot Red (Close < Open) AND CE Green (Close > Open)
- Entry: Tick LTP > divergence candle's high
- Risk Management (Trailing SL):
  - Initial: SL = entry - ₹1, TP = entry + ₹1
  - After ₹1 TP hit: SL moves to entry+₹1, TP = entry+₹1.5
  - After ₹1.5 hit: SL moves to entry+₹1.5, TP = entry+₹2
  - Continues in ₹0.5 steps until SL is hit
- Capital: ₹10,000 | Lot Size: 65

Usage: python live_divergence_1min.py
"""

import os
import csv
import json
import time
import pytz
import logging
import dotenv
from datetime import datetime, time as dt_time, timedelta
from fyers_apiv3 import fyersModel
from fyers_apiv3.FyersWebsocket import data_ws

dotenv.load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('live_divergence.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger('LiveDivergence')

IST = pytz.timezone('Asia/Kolkata')

# ============================================================
# CONSTANTS
# ============================================================
SPOT_SYMBOL = "NSE:NIFTY50-INDEX"
CAPITAL = 10000
LOT_SIZE = 65
OPTION_PRICE_MIN = 60.0    # Min option price range
OPTION_PRICE_MAX = 70.0    # Max option price range
OPTION_PRICE_TARGET = 65.0 # Ideal option price
SL_INITIAL = 1.0           # ₹1 initial stop loss
TP_INITIAL = 1.0           # ₹1 initial take profit
TRAIL_STEP = 0.5           # ₹0.5 trailing step after first TP
OPTION_REFRESH_SECONDS = 300  # Re-check options every 5 minutes
API_DELAY = 0.5
TRADE_LOG_FILE = "live_divergence_trades.csv"
MARKET_OPEN = dt_time(9, 15)
MARKET_CLOSE = dt_time(15, 30)


# ============================================================
# 1-MIN CANDLE MANAGER (from ticks)
# ============================================================
class CandleManager1Min:
    """Builds 1-minute OHLC candles from tick data."""

    def __init__(self):
        self.current_candles = {}  # {symbol: {bucket_time, open, high, low, close}}

    def get_current_bucket(self):
        """Get the current 1-minute bucket start time."""
        now = datetime.now(IST)
        return now.replace(second=0, microsecond=0)

    def update(self, symbol, ltp):
        """
        Update candle with new tick. Returns completed candle dict or None.
        Candle dict: {'time': datetime, 'open': float, 'high': float, 'low': float, 'close': float}
        """
        bucket_time = self.get_current_bucket()
        current = self.current_candles.get(symbol)

        if current is None or current['bucket_time'] != bucket_time:
            # New minute → close previous candle, start new one
            completed = None
            if current is not None:
                completed = {
                    'time': current['bucket_time'],
                    'open': current['open'],
                    'high': current['high'],
                    'low': current['low'],
                    'close': current['close'],
                }

            self.current_candles[symbol] = {
                'bucket_time': bucket_time,
                'open': ltp,
                'high': ltp,
                'low': ltp,
                'close': ltp,
            }
            return completed
        else:
            # Same minute → update OHLC
            current['high'] = max(current['high'], ltp)
            current['low'] = min(current['low'], ltp)
            current['close'] = ltp
            return None

    def reset(self):
        self.current_candles = {}


# ============================================================
# TRAILING SL TRADE MANAGER
# ============================================================
class TrailingTrade:
    """
    Manages a single trade with trailing stop loss.

    Lifecycle:
      Entry → initial SL/TP → on TP hit: trail SL up, set new TP → repeat → SL hit → close
    """

    def __init__(self, symbol, trade_type, entry_price, entry_time, signal_candle, reason):
        self.symbol = symbol
        self.trade_type = trade_type  # 'CE_BUY' or 'PE_BUY'
        self.entry_price = entry_price
        self.entry_time = entry_time
        self.signal_candle = signal_candle
        self.reason = reason

        # Risk management state
        self.sl = entry_price - SL_INITIAL
        self.tp = entry_price + TP_INITIAL
        self.trail_count = 0  # How many times we've trailed
        self.highest_reached = entry_price

        # Trade result (filled on close)
        self.exit_price = None
        self.exit_time = None
        self.exit_reason = None
        self.is_open = True

    def update_tick(self, ltp, timestamp):
        """
        Process a tick. Returns exit info dict if trade closed, else None.
        """
        if not self.is_open:
            return None

        self.highest_reached = max(self.highest_reached, ltp)

        # Check SL first (price falling)
        if ltp <= self.sl:
            return self._close(self.sl, timestamp, 'SL')

        # Check TP (price rising)
        if ltp >= self.tp:
            # Trail up!
            self.trail_count += 1
            old_sl = self.sl
            old_tp = self.tp

            # Move SL to where TP was
            self.sl = self.tp

            if self.trail_count == 1:
                # First TP hit (₹1 profit) → next TP is ₹0.5 further
                self.tp = self.sl + TRAIL_STEP
            else:
                # Subsequent trails → ₹0.5 steps
                self.tp = self.sl + TRAIL_STEP

            logger.info(
                f"  🔄 TRAIL #{self.trail_count}: {self.symbol} | "
                f"SL: {old_sl:.2f}→{self.sl:.2f} | TP: {old_tp:.2f}→{self.tp:.2f} | "
                f"LTP: {ltp:.2f}"
            )

            # Check if the tick ALSO hits the new SL (shouldn't happen, but safety)
            if ltp <= self.sl:
                return self._close(self.sl, timestamp, f'SL_AFTER_TRAIL_{self.trail_count}')

            # Check if tick ALSO already >= new TP (fast move)
            if ltp >= self.tp:
                # Recursively trail again
                return self.update_tick(ltp, timestamp)

        return None

    def _close(self, exit_price, timestamp, reason):
        self.is_open = False
        self.exit_price = exit_price
        self.exit_time = timestamp
        self.exit_reason = reason
        pnl_per_unit = exit_price - self.entry_price
        pnl_total = pnl_per_unit * LOT_SIZE
        return {
            'symbol': self.symbol,
            'type': self.trade_type,
            'entry_price': self.entry_price,
            'entry_time': self.entry_time,
            'exit_price': exit_price,
            'exit_time': timestamp,
            'sl_final': self.sl,
            'trail_count': self.trail_count,
            'highest_reached': self.highest_reached,
            'pnl_per_unit': pnl_per_unit,
            'pnl_total': pnl_total,
            'exit_reason': reason,
            'reason': self.reason,
        }

    def close_eod(self, ltp, timestamp):
        """Force close at end of day."""
        if self.is_open:
            return self._close(ltp, timestamp, 'EOD_CLOSE')
        return None


# ============================================================
# MAIN LIVE ENGINE
# ============================================================
class LiveDivergenceEngine:
    """
    Live forward-testing engine for 1-min divergence strategy.
    """

    def __init__(self):
        self.fyers = None
        self.fyers_ws = None
        self.access_token = None
        self.client_id = None
        self.is_running = False

        # Option tracking
        self.ce_symbol = None
        self.pe_symbol = None
        self.ce_ltp = None
        self.pe_ltp = None
        self.spot_ltp = None
        self.last_option_refresh = 0

        # Candle manager
        self.candle_manager = CandleManager1Min()

        # Signal detection
        self.candle_history = {}  # {symbol: [last_candle]}
        self.pending_signals = {}  # {symbol: signal_dict}

        # Trade management
        self.active_trade = None  # Only 1 trade at a time
        self.trade_history = []
        self.daily_signals = 0

        # Capital
        self.running_capital = CAPITAL
        self.daily_pnl = 0.0

        # CSV logger
        self._init_csv()

    def _init_csv(self):
        if not os.path.exists(TRADE_LOG_FILE):
            with open(TRADE_LOG_FILE, 'w', newline='') as f:
                writer = csv.writer(f)
                writer.writerow([
                    'Date', 'Type', 'Symbol', 'Entry Time', 'Entry Price',
                    'Exit Time', 'Exit Price', 'SL Final', 'Trail Count',
                    'Highest Reached', 'PnL/Unit', 'PnL Total',
                    'Exit Reason', 'Signal Reason'
                ])

    def _log_trade_csv(self, trade):
        with open(TRADE_LOG_FILE, 'a', newline='') as f:
            writer = csv.writer(f)
            entry_t = trade['entry_time'].strftime('%Y-%m-%d %H:%M:%S') if hasattr(trade['entry_time'], 'strftime') else str(trade['entry_time'])
            exit_t = trade['exit_time'].strftime('%Y-%m-%d %H:%M:%S') if hasattr(trade['exit_time'], 'strftime') else str(trade['exit_time'])
            writer.writerow([
                datetime.now(IST).strftime('%Y-%m-%d'),
                trade['type'],
                trade['symbol'],
                entry_t,
                f"{trade['entry_price']:.2f}",
                exit_t,
                f"{trade['exit_price']:.2f}",
                f"{trade['sl_final']:.2f}",
                trade['trail_count'],
                f"{trade['highest_reached']:.2f}",
                f"{trade['pnl_per_unit']:.2f}",
                f"{trade['pnl_total']:.2f}",
                trade['exit_reason'],
                trade.get('reason', ''),
            ])

    # ========== AUTHENTICATION ==========

    def authenticate(self):
        from main import FyersAuthenticator

        self.client_id = os.getenv("CLIENT_ID")
        secret_key = os.getenv("SECRET_KEY")
        username = os.getenv("USERNAME")
        pin = os.getenv("PIN")
        totp_key = os.getenv("TOTP_KEY")

        if not all([self.client_id, secret_key, username, pin, totp_key]):
            logger.error("Missing environment variables!")
            return False

        auth = FyersAuthenticator(self.client_id, secret_key, "https://www.google.com", username, pin, totp_key)
        token, err = auth.get_access_token()
        if not token:
            logger.error(f"Auth failed: {err}")
            return False

        self.access_token = token
        self.fyers = fyersModel.FyersModel(client_id=self.client_id, token=token, log_path="")
        logger.info("✅ Authentication successful!")
        return True

    # ========== OPTION SELECTION (60-70 RANGE) ==========

    def find_options_in_range(self):
        """Find CE and PE options with LTP in the 60-70 rupee range."""
        try:
            time.sleep(API_DELAY)
            # Get nearest expiry
            data = {"symbol": SPOT_SYMBOL, "strikecount": 5, "timestamp": ""}
            resp = self.fyers.optionchain(data=data)
            if resp.get('code') != 200:
                logger.error(f"Failed to get expiry: {resp}")
                return None, None

            nearest_expiry = str(resp['data']['expiryData'][0]['expiry'])

            # Get wide option chain
            time.sleep(API_DELAY)
            data = {"symbol": SPOT_SYMBOL, "strikecount": 30, "timestamp": nearest_expiry}
            resp = self.fyers.optionchain(data=data)
            if resp.get('code') != 200:
                logger.error(f"Failed to get option chain: {resp}")
                return None, None

            options = resp['data']['optionsChain']

            # Filter CE options in 60-70 range
            ce_candidates = [
                o for o in options
                if o.get('option_type') == 'CE'
                and OPTION_PRICE_MIN <= o.get('ltp', 0) <= OPTION_PRICE_MAX
            ]
            pe_candidates = [
                o for o in options
                if o.get('option_type') == 'PE'
                and OPTION_PRICE_MIN <= o.get('ltp', 0) <= OPTION_PRICE_MAX
            ]

            # Pick closest to target price (₹65)
            ce_pick = None
            pe_pick = None

            if ce_candidates:
                ce_pick = min(ce_candidates, key=lambda o: abs(o['ltp'] - OPTION_PRICE_TARGET))
            if pe_candidates:
                pe_pick = min(pe_candidates, key=lambda o: abs(o['ltp'] - OPTION_PRICE_TARGET))

            if ce_pick and pe_pick:
                logger.info(f"  CE: {ce_pick['symbol']} (LTP: ₹{ce_pick['ltp']:.2f}, Strike: {ce_pick.get('strike_price')})")
                logger.info(f"  PE: {pe_pick['symbol']} (LTP: ₹{pe_pick['ltp']:.2f}, Strike: {pe_pick.get('strike_price')})")
                return ce_pick['symbol'], pe_pick['symbol']
            else:
                # Fallback: widen range to 50-80
                logger.warning("No options in 60-70 range. Trying 50-80...")
                ce_candidates = [
                    o for o in options
                    if o.get('option_type') == 'CE'
                    and 50 <= o.get('ltp', 0) <= 80
                ]
                pe_candidates = [
                    o for o in options
                    if o.get('option_type') == 'PE'
                    and 50 <= o.get('ltp', 0) <= 80
                ]
                if ce_candidates:
                    ce_pick = min(ce_candidates, key=lambda o: abs(o['ltp'] - OPTION_PRICE_TARGET))
                if pe_candidates:
                    pe_pick = min(pe_candidates, key=lambda o: abs(o['ltp'] - OPTION_PRICE_TARGET))

                if ce_pick and pe_pick:
                    logger.info(f"  CE (fallback): {ce_pick['symbol']} (LTP: ₹{ce_pick['ltp']:.2f})")
                    logger.info(f"  PE (fallback): {pe_pick['symbol']} (LTP: ₹{pe_pick['ltp']:.2f})")
                    return ce_pick['symbol'], pe_pick['symbol']

                logger.error("No suitable options found even in 50-80 range!")
                return None, None

        except Exception as e:
            logger.error(f"Error finding options: {e}", exc_info=True)
            return None, None

    def maybe_refresh_options(self):
        """Refresh option selection every OPTION_REFRESH_SECONDS."""
        now = time.time()
        if now - self.last_option_refresh < OPTION_REFRESH_SECONDS:
            return

        # Don't refresh if there's an active trade (would mess up tracking)
        if self.active_trade is not None:
            return

        self.last_option_refresh = now
        logger.info("🔄 Refreshing option selection...")

        new_ce, new_pe = self.find_options_in_range()
        if not new_ce or not new_pe:
            return

        if new_ce != self.ce_symbol or new_pe != self.pe_symbol:
            # Unsubscribe old
            old_symbols = [s for s in [self.ce_symbol, self.pe_symbol] if s]
            if old_symbols and self.fyers_ws:
                try:
                    self.fyers_ws.unsubscribe(symbols=old_symbols)
                except:
                    pass

            self.ce_symbol = new_ce
            self.pe_symbol = new_pe

            # Clear candle history for old symbols
            self.candle_history = {}
            self.pending_signals = {}

            # Subscribe new
            if self.fyers_ws:
                self.fyers_ws.subscribe(symbols=[new_ce, new_pe], data_type="SymbolUpdate")
                logger.info(f"📡 Subscribed to new options: CE={new_ce}, PE={new_pe}")

    # ========== DIVERGENCE SIGNAL DETECTION ==========

    def _store_candle(self, symbol, candle):
        """Store completed candle for signal detection."""
        if symbol not in self.candle_history:
            self.candle_history[symbol] = []
        self.candle_history[symbol].append(candle)
        # Keep last 5 candles
        if len(self.candle_history[symbol]) > 5:
            self.candle_history[symbol].pop(0)

    def _get_latest_candle(self, symbol):
        """Get the most recent completed candle for a symbol."""
        candles = self.candle_history.get(symbol, [])
        return candles[-1] if candles else None

    def check_divergence(self, timestamp):
        """Check for divergence signals using the latest completed candles."""
        spot_candle = self._get_latest_candle(SPOT_SYMBOL)
        if not spot_candle:
            return

        # PE Signal: Spot Green + PE Green
        if self.pe_symbol:
            pe_candle = self._get_latest_candle(self.pe_symbol)
            if pe_candle and pe_candle['time'] == spot_candle['time']:
                spot_green = spot_candle['close'] > spot_candle['open']
                pe_green = pe_candle['close'] > pe_candle['open']

                if spot_green and pe_green:
                    self.daily_signals += 1
                    reason = (
                        f"DIVERGENCE: Spot GREEN (O:{spot_candle['open']:.2f} C:{spot_candle['close']:.2f}) "
                        f"+ PE GREEN (O:{pe_candle['open']:.2f} C:{pe_candle['close']:.2f})"
                    )
                    self.pending_signals[self.pe_symbol] = {
                        'type': 'PE_BUY',
                        'high': pe_candle['high'],
                        'low': pe_candle['low'],
                        'candle': pe_candle,
                        'time': timestamp,
                        'reason': reason,
                    }
                    print()  # New line
                    logger.info(f"🎯 PE Signal Detected: {self.pe_symbol} | High: {pe_candle['high']:.2f} | {reason}")

        # CE Signal: Spot Red + CE Green
        if self.ce_symbol:
            ce_candle = self._get_latest_candle(self.ce_symbol)
            if ce_candle and ce_candle['time'] == spot_candle['time']:
                spot_red = spot_candle['close'] < spot_candle['open']
                ce_green = ce_candle['close'] > ce_candle['open']

                if spot_red and ce_green:
                    self.daily_signals += 1
                    reason = (
                        f"DIVERGENCE: Spot RED (O:{spot_candle['open']:.2f} C:{spot_candle['close']:.2f}) "
                        f"+ CE GREEN (O:{ce_candle['open']:.2f} C:{ce_candle['close']:.2f})"
                    )
                    self.pending_signals[self.ce_symbol] = {
                        'type': 'CE_BUY',
                        'high': ce_candle['high'],
                        'low': ce_candle['low'],
                        'candle': ce_candle,
                        'time': timestamp,
                        'reason': reason,
                    }
                    print()
                    logger.info(f"🎯 CE Signal Detected: {self.ce_symbol} | High: {ce_candle['high']:.2f} | {reason}")

    # ========== WEBSOCKET HANDLERS ==========

    def _on_message(self, message):
        """Process each WebSocket tick."""
        try:
            if not isinstance(message, dict) or 'ltp' not in message or 'symbol' not in message:
                return

            symbol = message['symbol']
            ltp = message['ltp']
            now = datetime.now(IST)

            # Track LTPs
            if symbol == SPOT_SYMBOL:
                self.spot_ltp = ltp
            elif symbol == self.ce_symbol:
                self.ce_ltp = ltp
            elif symbol == self.pe_symbol:
                self.pe_ltp = ltp
            else:
                return  # Unknown symbol

            # Check market hours
            current_time = now.time()
            if current_time > MARKET_CLOSE:
                self._handle_eod(now)
                return

            # --- 1. ACTIVE TRADE MANAGEMENT (tick-by-tick) ---
            if self.active_trade is not None and symbol == self.active_trade.symbol:
                result = self.active_trade.update_tick(ltp, now)
                if result:
                    self._handle_trade_exit(result)

            # --- 2. PENDING SIGNAL CHECK (entry trigger) ---
            if symbol in self.pending_signals and self.active_trade is None:
                signal = self.pending_signals[symbol]

                # Invalidation: price broke below signal candle low
                if ltp < signal['low']:
                    logger.info(f"  ❌ Signal invalidated for {symbol}: LTP {ltp:.2f} < Low {signal['low']:.2f}")
                    del self.pending_signals[symbol]

                # Entry: price broke above signal candle high
                elif ltp > signal['high']:
                    self._enter_trade(symbol, ltp, now, signal)
                    del self.pending_signals[symbol]

            # --- 3. CANDLE BUILDING ---
            completed_candle = self.candle_manager.update(symbol, ltp)
            if completed_candle:
                self._store_candle(symbol, completed_candle)
                # Check for divergence on spot candle completion
                if symbol == SPOT_SYMBOL:
                    self.check_divergence(now)

            # --- 4. OPTION REFRESH ---
            self.maybe_refresh_options()

            # --- 5. STATUS LINE ---
            self._print_status(now)

        except Exception as e:
            logger.error(f"Error in tick handler: {e}", exc_info=True)

    def _enter_trade(self, symbol, ltp, timestamp, signal):
        """Enter a new paper trade."""
        entry_price = ltp  # Enter at current LTP (market order simulation)
        cost = entry_price * LOT_SIZE

        if cost > self.running_capital:
            logger.warning(f"  ⚠️ Insufficient capital: Need ₹{cost:.2f}, have ₹{self.running_capital:.2f}")
            return

        self.active_trade = TrailingTrade(
            symbol=symbol,
            trade_type=signal['type'],
            entry_price=entry_price,
            entry_time=timestamp,
            signal_candle=signal['candle'],
            reason=signal['reason'],
        )

        print()
        logger.info("=" * 60)
        logger.info(f"📈 TRADE ENTERED: {signal['type']} on {symbol}")
        logger.info(f"  Entry: ₹{entry_price:.2f} | SL: ₹{self.active_trade.sl:.2f} | TP: ₹{self.active_trade.tp:.2f}")
        logger.info(f"  Cost: ₹{cost:.2f} (1 lot × {LOT_SIZE})")
        logger.info(f"  Signal: {signal['reason']}")
        logger.info("=" * 60)

    def _handle_trade_exit(self, result):
        """Handle trade exit (SL or trailed SL)."""
        self.trade_history.append(result)
        self.daily_pnl += result['pnl_total']
        self.running_capital += result['pnl_total']

        self._log_trade_csv(result)

        # Determine emoji
        emoji = "✅" if result['pnl_total'] > 0 else "❌" if result['pnl_total'] < 0 else "➖"

        print()
        logger.info("=" * 60)
        logger.info(f"{emoji} TRADE CLOSED: {result['type']} on {result['symbol']}")
        logger.info(f"  Entry: ₹{result['entry_price']:.2f} → Exit: ₹{result['exit_price']:.2f}")
        logger.info(f"  Trails: {result['trail_count']} | Highest: ₹{result['highest_reached']:.2f}")
        logger.info(f"  PnL: ₹{result['pnl_per_unit']:.2f}/unit × {LOT_SIZE} = ₹{result['pnl_total']:+.2f}")
        logger.info(f"  Reason: {result['exit_reason']}")
        logger.info(f"  Daily PnL: ₹{self.daily_pnl:+.2f} | Capital: ₹{self.running_capital:.2f}")
        logger.info("=" * 60)

        self.active_trade = None

    def _handle_eod(self, now):
        """End of day handling."""
        if self.active_trade is not None:
            # Get last known LTP for the symbol
            if self.active_trade.symbol == self.ce_symbol:
                last_ltp = self.ce_ltp or self.active_trade.entry_price
            else:
                last_ltp = self.pe_ltp or self.active_trade.entry_price

            result = self.active_trade.close_eod(last_ltp, now)
            if result:
                self._handle_trade_exit(result)

        self._print_daily_report()
        self.is_running = False

    def _print_status(self, now):
        """Print real-time status line (overwriting)."""
        ts = now.strftime('%H:%M:%S')
        parts = []

        if self.spot_ltp:
            parts.append(f"NIFTY: {self.spot_ltp:.2f}")
        if self.ce_symbol and self.ce_ltp:
            ce_short = self.ce_symbol.split('NIFTY')[1] if 'NIFTY' in self.ce_symbol else self.ce_symbol[-12:]
            parts.append(f"CE({ce_short}): ₹{self.ce_ltp:.2f}")
        if self.pe_symbol and self.pe_ltp:
            pe_short = self.pe_symbol.split('NIFTY')[1] if 'NIFTY' in self.pe_symbol else self.pe_symbol[-12:]
            parts.append(f"PE({pe_short}): ₹{self.pe_ltp:.2f}")

        if self.active_trade:
            t = self.active_trade
            parts.append(f"TRADE: {t.trade_type} SL:{t.sl:.2f} TP:{t.tp:.2f} T:{t.trail_count}")

        parts.append(f"PnL: ₹{self.daily_pnl:+.2f}")
        parts.append(f"Signals: {self.daily_signals}")

        line = f"[{ts}] {' | '.join(parts)}"
        print(f"\r{line:<160}", end='', flush=True)

    def _print_daily_report(self):
        """Print end-of-day summary."""
        print()
        logger.info("=" * 60)
        logger.info("📊 DAILY REPORT — 1-MIN DIVERGENCE STRATEGY")
        logger.info("=" * 60)
        logger.info(f"  Date: {datetime.now(IST).strftime('%Y-%m-%d')}")
        logger.info(f"  Signals Detected: {self.daily_signals}")
        logger.info(f"  Trades Taken: {len(self.trade_history)}")

        if self.trade_history:
            wins = [t for t in self.trade_history if t['pnl_total'] > 0]
            losses = [t for t in self.trade_history if t['pnl_total'] < 0]
            avg_trail = sum(t['trail_count'] for t in self.trade_history) / len(self.trade_history)

            logger.info(f"  Wins: {len(wins)} | Losses: {len(losses)}")
            logger.info(f"  Win Rate: {len(wins)/len(self.trade_history)*100:.1f}%")
            logger.info(f"  Avg Trails: {avg_trail:.1f}")
            logger.info(f"  Daily PnL: ₹{self.daily_pnl:+.2f}")
            logger.info(f"  Running Capital: ₹{self.running_capital:.2f}")

            # Best/worst trade
            best = max(self.trade_history, key=lambda t: t['pnl_total'])
            worst = min(self.trade_history, key=lambda t: t['pnl_total'])
            logger.info(f"  Best Trade: ₹{best['pnl_total']:+.2f} ({best['trail_count']} trails)")
            logger.info(f"  Worst Trade: ₹{worst['pnl_total']:+.2f}")
        else:
            logger.info("  No trades taken today.")

        logger.info("=" * 60)

        # Save report JSON
        report = {
            'date': datetime.now(IST).strftime('%Y-%m-%d'),
            'signals': self.daily_signals,
            'trades': len(self.trade_history),
            'daily_pnl': self.daily_pnl,
            'capital': self.running_capital,
            'trade_details': self.trade_history,
        }
        report_file = f"live_divergence_report_{report['date']}.json"
        with open(report_file, 'w') as f:
            json.dump(report, f, indent=4, default=str)
        logger.info(f"  Report saved: {report_file}")

    # ========== WEBSOCKET CONNECTION ==========

    def _on_open(self):
        """WebSocket connected — subscribe to symbols."""
        logger.info("=" * 60)
        logger.info("📡 WebSocket Connected!")
        logger.info("=" * 60)

        # Subscribe to spot
        symbols_to_subscribe = [SPOT_SYMBOL]

        # Subscribe to options
        if self.ce_symbol:
            symbols_to_subscribe.append(self.ce_symbol)
        if self.pe_symbol:
            symbols_to_subscribe.append(self.pe_symbol)

        logger.info(f"Subscribing to: {symbols_to_subscribe}")
        self.fyers_ws.subscribe(symbols=symbols_to_subscribe, data_type="SymbolUpdate")

        self.is_running = True
        self.fyers_ws.keep_running()

    def _on_error(self, message):
        logger.error(f"WebSocket Error: {message}")

    def _on_close(self, message):
        logger.info(f"WebSocket Closed: {message}")
        self.is_running = False

    # ========== MAIN RUN ==========

    def run(self):
        """Main entry point."""
        logger.info("=" * 60)
        logger.info("🚀 LIVE DIVERGENCE 1-MIN STRATEGY")
        logger.info(f"   Capital: ₹{CAPITAL} | Lot Size: {LOT_SIZE}")
        logger.info(f"   SL: ₹{SL_INITIAL} | TP: ₹{TP_INITIAL} | Trail Step: ₹{TRAIL_STEP}")
        logger.info(f"   Option Range: ₹{OPTION_PRICE_MIN}-{OPTION_PRICE_MAX}")
        logger.info("=" * 60)

        # Step 1: Authenticate
        if not self.authenticate():
            return

        # Step 2: Wait for market open
        now = datetime.now(IST)
        if now.time() < MARKET_OPEN:
            wait_until = datetime.combine(now.date(), MARKET_OPEN, tzinfo=IST)
            wait_secs = (wait_until - now).total_seconds()
            logger.info(f"⏳ Market opens at 09:15. Waiting {int(wait_secs//60)} min...")
            time.sleep(max(0, wait_secs - 30))  # Wake up 30s early

        if now.time() > MARKET_CLOSE:
            logger.info("Market already closed for today.")
            return

        # Step 3: Find options in 60-70 range
        logger.info("🔍 Finding options in ₹60-70 range...")
        self.ce_symbol, self.pe_symbol = self.find_options_in_range()
        if not self.ce_symbol or not self.pe_symbol:
            logger.error("Cannot find suitable options. Exiting.")
            return

        self.last_option_refresh = time.time()

        # Step 4: Connect WebSocket
        logger.info("📡 Connecting WebSocket...")
        try:
            self.fyers_ws = data_ws.FyersDataSocket(
                access_token=self.access_token,
                log_path="",
                litemode=False,
                write_to_file=False,
                reconnect=True,
                on_connect=self._on_open,
                on_close=self._on_close,
                on_error=self._on_error,
                on_message=self._on_message,
            )
            self.fyers_ws.connect()

            # Keep alive until market close
            while self.is_running:
                time.sleep(1)

        except KeyboardInterrupt:
            print()
            logger.info("⚠️ Interrupted by user.")
            self._handle_eod(datetime.now(IST))
        except Exception as e:
            logger.error(f"Fatal error: {e}", exc_info=True)
            self._handle_eod(datetime.now(IST))


# ============================================================
# ENTRY POINT
# ============================================================
def main():
    engine = LiveDivergenceEngine()
    engine.run()


if __name__ == "__main__":
    main()
